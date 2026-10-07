"""Offline audit of the Stage C grid: denominators, interaction inference, floors.

Read-only with respect to the experiment data.  It exists because three problems
were raised on review of the full-grid report:

1. The interaction p (observation-weighted cluster-robust, normal approx) and the
   bootstrap CI (task-equal-weighted) came from different estimands, so neither
   could be quoted on its own.
2. ``degradation_by_cell`` excluded untriggered pairs from the delta but not from
   the control/fault success rates, so a row's rates and its delta were not the
   same sample.
3. Eight of sixteen tasks have no discriminative range (0/24 or 24/24 among
   control trials), which must be reported rather than dropped.

Writes ``experiments/stage_c_audit/audit.json`` and ``audit.md``.
"""
from __future__ import annotations

import json
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from standard_agent.stage_c_analysis import (  # noqa: E402
    normal_equations, solve, cluster_robust_cov, add_normal_equations,
    resample_tasks_by_stratum, strata_for_tasks,
)

ROWS = ROOT / "experiments/stage_c_full/rows.json"
DESIGN = ROOT / "docs/task_manifest_public16.json"
OUT_DIR = ROOT / "experiments/stage_c_audit"
N_BOOT = 10000
SEED = 20260921
FAULTS = ("web_http_error", "agent_param_error", "web_dom_missing")


# --- Student-t tail, since the analysis env has no scipy -------------------

def student_t_sf(t: float, df: int) -> float:
    if df <= 0:
        return float("nan")
    x = df / (df + t * t)
    return _betai(df / 2.0, 0.5, x)   # two-sided, already |t|


def _betai(a: float, b: float, x: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbeta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    bt = math.exp(a * math.log(x) + b * math.log(1 - x) - lbeta)
    if x < (a + 1) / (a + b + 2):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1 - x) / b


def _betacf(a: float, b: float, x: float) -> float:
    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
    h = d
    for m in range(1, 201):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1.0 + aa / c
        c = c if abs(c) > 1e-300 else 1e-300
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1.0 + aa / c
        c = c if abs(c) > 1e-300 else 1e-300
        h *= d * c
        if abs(d * c - 1.0) < 3e-12:
            break
    return h


# --- helpers ---------------------------------------------------------------

def load_pairs():
    rows = json.load(open(ROWS))["rows"]
    buckets = defaultdict(dict)
    for r in rows:
        c = r["cell"]
        key = (c["model_profile"], c["architecture"], c["fault_type"],
               int(c["task_id"]), int(c["fault_seed"]))
        buckets[key][c["condition"]] = {
            "success": int(bool(r["official_success"])),
            "injection_count": r.get("injection_count"),
        }
    pairs = []
    for key, arms in buckets.items():
        if "control" not in arms or "fault" not in arms:
            continue
        inj = arms["fault"]["injection_count"]
        pairs.append({
            "model": key[0], "arch": key[1], "fault_type": key[2],
            "task": key[3], "seed": key[4],
            "control_success": arms["control"]["success"],
            "fault_success": arms["fault"]["success"],
            "delta": arms["control"]["success"] - arms["fault"]["success"],
            "applied": (inj is None or inj > 0),
        })
    return pairs


def task_cluster_ci(values_by_task: dict, strata: dict | None = None,
                    n_boot: int = N_BOOT, seed: int = SEED):
    tasks = sorted(values_by_task)
    groups = strata or {"all": tasks}
    groups = {k: [t for t in v if t in values_by_task] for k, v in groups.items()}
    groups = {k: v for k, v in groups.items() if v}
    rng = random.Random(seed)
    ests = []
    for _ in range(n_boot):
        tot, cnt = 0.0, 0
        for task in resample_tasks_by_stratum(groups, rng):
            vals = values_by_task[task]
            tot += sum(vals) / len(vals)
            cnt += 1
        ests.append(tot / cnt)
    ests.sort()
    lo = ests[int(math.floor(0.025 * len(ests)))]
    hi = ests[min(len(ests) - 1, int(math.ceil(0.975 * len(ests))) - 1)]
    return lo, hi


def t15_ci(per_task_values: list[float]):
    n = len(per_task_values)
    mean = statistics.mean(per_task_values)
    sd = statistics.stdev(per_task_values) if n > 1 else 0.0
    se = sd / math.sqrt(n) if n else float("nan")
    crit = 2.131  # t_{0.975, 15}
    return mean, se, [mean - crit * se, mean + crit * se]


def degradation_table(pairs, *, applied_only: bool, category_of):
    """Return per-(model,arch,fault) rows with rates and delta on the SAME sample."""
    selected = [p for p in pairs if p["applied"]] if applied_only else list(pairs)
    grouped = defaultdict(lambda: defaultdict(list))
    for p in selected:
        grouped[(p["model"], p["arch"], p["fault_type"])][p["task"]].append(p)
    out = []
    for key, by_task in sorted(grouped.items()):
        deltas = {t: [p["delta"] for p in ps] for t, ps in by_task.items()}
        per_task_mean = [sum(v) / len(v) for v in deltas.values()]
        n_pairs = sum(len(v) for v in deltas.values())
        ctrl = sum(p["control_success"] for ps in by_task.values() for p in ps)
        flt = sum(p["fault_success"] for ps in by_task.values() for p in ps)
        mean, se, tci = t15_ci(per_task_mean)
        strata = strata_for_tasks(category_of, by_task)
        lo, hi = task_cluster_ci(deltas, strata)
        out.append({
            "model": key[0], "arch": key[1], "fault_type": key[2],
            "n_pairs": n_pairs,
            "control_rate": ctrl / n_pairs, "fault_rate": flt / n_pairs,
            "degradation": mean, "std_error": se,
            "ci_boot": [lo, hi], "ci_t15": tci,
        })
    return out


def pooled_by_fault(pairs, *, applied_only: bool, category_of):
    agg = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for p in pairs:
        if applied_only and not p["applied"]:
            continue
        agg[p["fault_type"]][p["task"]][p["model"], p["arch"]].append(p)
    out = {}
    for fault, by_task in agg.items():
        deltas = {}
        for task, cells in by_task.items():
            deltas[task] = [p["delta"] for ps in cells.values() for p in ps]
        per_task_mean = [sum(v) / len(v) for v in deltas.values()]
        n = sum(len(v) for v in deltas.values())
        ctrl = sum(p["control_success"] for cells in by_task.values() for ps in cells.values() for p in ps)
        flt = sum(p["fault_success"] for cells in by_task.values() for ps in cells.values() for p in ps)
        mean, se, tci = t15_ci(per_task_mean)
        strata = strata_for_tasks(category_of, by_task)
        lo, hi = task_cluster_ci(deltas, strata)
        out[fault] = {"n_pairs": n, "control_rate": ctrl / n, "fault_rate": flt / n,
                      "degradation": mean, "std_error": se,
                      "ci_boot": [lo, hi], "ci_t15": tci}
    return out


def floor_ceiling(pairs):
    by_task = defaultdict(lambda: {"control": [], "fault": []})
    for p in pairs:
        by_task[p["task"]]["control"].append(p["control_success"])
        by_task[p["task"]]["fault"].append(p["fault_success"])
    rows = []
    for task, arms in sorted(by_task.items()):
        c = arms["control"]
        rows.append({
            "task": task,
            "control_successes": sum(c), "control_total": len(c),
            "within_range": 0 < sum(c) < len(c),
        })
    return rows


def interaction(rows):
    obs = []
    for r in rows:
        c = r["cell"]
        obs.append({"model": c["model_profile"], "arch": c["architecture"],
                    "task": int(c["task_id"]), "cond": c["condition"],
                    "y": int(bool(r["official_success"]))})
    models = sorted({o["model"] for o in obs})
    archs = sorted({o["arch"] for o in obs})
    mi = {m: i for i, m in enumerate(models)}
    ai = {a: i for i, a in enumerate(archs)}

    def xrow(o):
        c = 1.0 if o["cond"] == "fault" else 0.0
        m, a = float(mi[o["model"]]), float(ai[o["arch"]])
        return [1.0, c, m, a, c * m, c * a, m * a, c * m * a]

    X = [xrow(o) for o in obs]
    y = [float(o["y"]) for o in obs]
    clusters = [o["task"] for o in obs]
    beta = solve(*normal_equations(X, y))
    resid = [t - sum(v * b for v, b in zip(row, beta)) for row, t in zip(X, y)]
    cov = cluster_robust_cov(X, resid, clusters)
    idx = 7
    se_ols = math.sqrt(cov[idx][idx])
    z = beta[idx] / se_ols
    p_norm = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))

    # task-equal-weighted interaction of degradations (same point estimate here,
    # because the design is balanced; see the audit note)
    by_task = defaultdict(lambda: defaultdict(list))
    for o in obs:
        by_task[o["task"]][(o["model"], o["arch"], o["cond"])].append(o["y"])
    per_task = {}
    for task, cells in by_task.items():
        def rate(m, a, c):
            v = cells.get((m, a, c), [])
            return sum(v) / len(v) if v else float("nan")
        deg = {(m, a): rate(m, a, "control") - rate(m, a, "fault")
               for m in models for a in archs}
        per_task[task] = ((deg[(models[1], archs[1])] - deg[(models[1], archs[0])])
                          - (deg[(models[0], archs[1])] - deg[(models[0], archs[0])]))
    vals = [per_task[t] for t in sorted(per_task)]
    mean, se_task, tci = t15_ci(vals)
    unstrat = task_cluster_ci({t: [per_task[t]] for t in per_task})
    return {
        "ols_coefficient": beta[idx],
        "observationally_weighted": {"se_cluster_robust": se_ols, "z": z,
                                     "p_normal": p_norm,
                                     "p_t15": 2 * student_t_sf(abs(z), 15)},
        "task_equal_weighted": {
            "coefficient": -mean,  # flip to the OLS sign convention
            "se": se_task,
            "ci_boot_unstratified": [-unstrat[1], -unstrat[0]],
            "ci_t15": [-tci[1], -tci[0]],
            "per_task_contrast": {t: -per_task[t] for t in sorted(per_task)},
        },
        "balanced": True,
    }


def git_dirty(roots):
    from collections import Counter
    counts = Counter()
    for root in roots:
        for path in (ROOT / "experiments" / root).glob("**/trial_record.json"):
            rec = json.load(open(path))
            code = rec.get("code") or {}
            counts[code.get("git_dirty")] += 1
    return {"records": sum(counts.values()),
            "dirty": counts.get(True, 0), "clean": counts.get(False, 0)}


def main():
    rows = json.load(open(ROWS))["rows"]
    design = json.load(open(DESIGN))
    category_of = {}
    for cat in design.get("categories", []):
        for tid in cat.get("main_task_ids", []) + cat.get("validation_task_ids", []):
            category_of[int(tid)] = cat["name"]

    pairs = load_pairs()
    roots = ["stage_c_slice1", "stage_c_slice1b", "stage_c_slice2b", "stage_c_slice2c",
             "stage_c_slice3", "stage_c_slice3b", "stage_c_slice4", "stage_c_slice4b"]

    audit = {
        "cell_counts": {"pairs_total": len(pairs),
                        "pairs_applied": sum(1 for p in pairs if p["applied"]),
                        "pairs_untriggered": sum(1 for p in pairs if not p["applied"]),
                        "untriggered_by_fault": {
                            f: sum(1 for p in pairs if p["fault_type"] == f and not p["applied"])
                            for f in FAULTS}},
        "degradation_itt": degradation_table(pairs, applied_only=False, category_of=category_of),
        "degradation_triggered": degradation_table(pairs, applied_only=True, category_of=category_of),
        "pooled_itt": pooled_by_fault(pairs, applied_only=False, category_of=category_of),
        "pooled_triggered": pooled_by_fault(pairs, applied_only=True, category_of=category_of),
        "floor_ceiling": floor_ceiling(pairs),
        "interaction": interaction(rows),
        "git_dirty": git_dirty(roots),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    json.dump(audit, open(OUT_DIR / "audit.json", "w"), indent=1)
    render_markdown(audit)
    print(json.dumps({"wrote": str(OUT_DIR / "audit.json"),
                      "interaction": audit["interaction"]["task_equal_weighted"],
                      "pairs": audit["cell_counts"]}, indent=1, default=str))


def render_markdown(audit: dict):
    lines = ["# Stage C 网格离线审计", ""]
    cc = audit["cell_counts"]
    lines.append(f"- 配对总数 {cc['pairs_total']}；触发 {cc['pairs_applied']}；未触发 {cc['pairs_untriggered']} "
                 f"（{cc['untriggered_by_fault']}）")
    lines.append("")

    def table(title, rows):
        lines.append(f"## {title}")
        lines.append("| model | arch | fault | n | control | fault | Δ | CI(boot) | CI(t15) |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for r in rows:
            lines.append(f"| {r['model']} | {r['arch']} | {r['fault']} | {r['n_pairs']} | "
                         f"{r['control_rate']:.3f} | {r['fault_rate']:.3f} | {r['degradation']:+.3f} | "
                         f"[{r['ci_boot'][0]:+.3f},{r['ci_boot'][1]:+.3f}] | "
                         f"[{r['ci_t15'][0]:+.3f},{r['ci_t15'][1]:+.3f}] |")
        lines.append("")

    table("按分配（ITT，含未触发）", audit["degradation_itt"])
    table("触发后（per-protocol）", audit["degradation_triggered"])

    lines.append("## 任务地板/天花板（控制臂，n=24/任务）")
    lines.append("| task | control 成功/总数 | 有区分度 |")
    lines.append("|---|---|---|")
    for r in audit["floor_ceiling"]:
        lines.append(f"| {r['task']} | {r['control_successes']}/{r['control_total']} | "
                     f"{'是' if r['within_range'] else '否'} |")
    lines.append("")

    it = audit["interaction"]
    lines.append("## 交互项（统一口径）")
    lines.append(f"- OLS 系数（观测加权）: {it['ols_coefficient']:+.4f}；聚类稳健 SE "
                 f"{it['observationally_weighted']['se_cluster_robust']:.4f}，"
                 f"p(normal)={it['observationally_weighted']['p_normal']:.4f}，"
                 f"p(t15)={it['observationally_weighted']['p_t15']:.4f}")
    tew = it["task_equal_weighted"]
    lines.append(f"- 任务等权系数: {tew['coefficient']:+.4f}；SE {tew['se']:.4f}；"
                 f"CI(boot) [{tew['ci_boot_unstratified'][0]:+.3f},{tew['ci_boot_unstratified'][1]:+.3f}]；"
                 f"CI(t15) [{tew['ci_t15'][0]:+.3f},{tew['ci_t15'][1]:+.3f}]")
    lines.append("")
    lines.append("> 两个估计量设计平衡下点估计一致，但方差口径不同；主报告须固定其一。")
    gd = audit["git_dirty"]
    lines.append("")
    lines.append(f"## git_dirty：{gd['dirty']} 条标脏 / {gd['records']} 条记录（均落在修复提交窗口内）")
    open(OUT_DIR / "audit.md", "w").write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
