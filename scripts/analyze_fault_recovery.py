"""Post-fault behaviour from the existing traces, with paired controls.

Three things the earlier pass got wrong and this one fixes:

1. It picked ``traces[0]``. Some cells have more than one trace (reruns); the
   official one is whatever ``trial_record.paths.trace`` records, so the analysis
   now follows that exact path.
2. "Recovered" only meant "the next event did not error" (or, for HTTP, "the next
   observation left the fake error page"). That shows the agent continued, not
   that it retried successfully, and it cannot by itself explain the final
   success rate. Three separate metrics are reported instead: continued,
   retried and succeeded, and official final success.
3. web_dom_missing was "observed" by diffing AX element ids against the control at
   the same step, which also fires when the two arms simply navigated apart. Its
   visibility is reported as detection-uncertain rather than as a rate.

The step cost is the paired difference fault minus control in the trial records,
not the number of steps after the fault.  Reads existing artefacts only.
"""
from __future__ import annotations

import json
import re
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments/stage_c_audit"

ROOTS = {
    "stage_c_slice1": ("deepseek_v41_flash", "react"),
    "stage_c_slice1b": ("deepseek_v41_flash", "react"),
    "stage_c_slice2b": ("deepseek_v41_flash", "plan_execute"),
    "stage_c_slice2c": ("deepseek_v41_flash", "plan_execute"),
    "stage_c_slice3": ("qwen38_flash", "react"),
    "stage_c_slice3b": ("qwen38_flash", "react"),
    "stage_c_slice4": ("qwen38_flash", "plan_execute"),
    "stage_c_slice4b": ("qwen38_flash", "plan_execute"),
}
FAULTS = ("web_http_error", "agent_param_error", "web_dom_missing")

SIGNATURES = {
    "web_http_error": ("500 internal server error", "about:error"),
    "agent_param_error": ("invalid_",),
    "web_dom_missing": ("not found in current page",),
}


def fault_signature(fault: str, observation: str) -> bool:
    text = (observation or "").lower()
    return any(marker in text for marker in SIGNATURES[fault])


def element_ids(observation: str) -> set:
    return {int(m) for m in re.findall(r"\[(\d+)\]", observation or "")}


def load_events(trace_path: str | Path):
    path = Path(trace_path)
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("event") is None and (event.get("action") or event.get("observation") is not None):
            events.append(event)
    return sorted(events, key=lambda e: (e.get("step") or 0))


def trial_dir(root, model, arch, fault, task, seed, condition):
    arm = f"control_seed_{seed}" if condition == "control" else f"{fault}_seed_{seed}"
    return (ROOT / "experiments" / root / "outputs" / model / arch
            / f"{fault}_task{task}_seed{seed}" / str(task) / arm)


def read_trial(path: Path):
    try:
        return json.load(open(path / "trial_record.json"))
    except Exception:
        return {}


def official_success_index():
    rows = json.load(open(ROOT / "experiments/stage_c_full/rows.json"))["rows"]
    index = {}
    for row in rows:
        cell = row["cell"]
        index[(cell["model_profile"], cell["architecture"], cell["fault_type"],
               int(cell["task_id"]), int(cell["fault_seed"]), cell["condition"])] = \
            row.get("official_success")
    return index


def main():
    official = official_success_index()
    records = []
    for root, (model, arch) in ROOTS.items():
        for status_path in sorted((ROOT / "experiments" / root / "run").glob("*.status.json")):
            if ".parsed" in status_path.name:
                continue
            status = json.load(open(status_path))
            job = status.get("job") or {}
            fault = job.get("fault_type")
            if fault not in FAULTS or not status.get("fault_triggered"):
                continue
            task, seed = job.get("task_id"), job.get("seed")
            fault_record = read_trial(trial_dir(root, model, arch, fault, task, seed, "fault"))
            control_record = read_trial(trial_dir(root, model, arch, fault, task, seed, "control"))
            fault_trace = (fault_record.get("paths") or {}).get("trace")
            control_trace = (control_record.get("paths") or {}).get("trace")
            if not fault_trace:
                continue
            events = load_events(fault_trace)

            # anchor on the fault's own signature
            fault_step = None
            for event in events:
                if fault_signature(fault, json.dumps(event.get("observation") or "", ensure_ascii=False)):
                    fault_step = event.get("step")
                    break
            dom_uncertain = False
            if fault == "web_dom_missing" and fault_step is None and control_trace:
                control_events = load_events(control_trace)
                control_by_step = {e.get("step"): e for e in control_events}
                for event in events:
                    other = control_by_step.get(event.get("step"))
                    if other is None:
                        continue
                    f_ids = element_ids(event.get("observation") or "")
                    c_ids = element_ids(other.get("observation") or "")
                    if f_ids and c_ids and f_ids < c_ids:
                        fault_step = event.get("step")
                        dom_uncertain = True      # AX diff can also mean divergent paths
                        break

            by_step = {e.get("step"): e for e in events}
            after = [e for e in events if (e.get("step") or 0) > (fault_step if fault_step is not None else -1)]
            next_event = after[0] if after else None
            faulted = by_step.get(fault_step) or {}
            retried = bool(next_event and next_event.get("action") == faulted.get("action"))
            if fault == "web_http_error":
                continued = bool(next_event and not fault_signature(
                    fault, json.dumps(next_event.get("observation") or "", ensure_ascii=False)))
            else:
                continued = bool(next_event and not next_event.get("error"))
            retry_succeeded = bool(retried and continued)

            step_delta = None
            if fault_record.get("steps") is not None and control_record.get("steps") is not None:
                step_delta = fault_record["steps"] - control_record["steps"]
            llm_delta = None
            if fault_record.get("llm_calls") is not None and control_record.get("llm_calls") is not None:
                llm_delta = fault_record["llm_calls"] - control_record["llm_calls"]

            records.append({
                "root": root, "model": model, "architecture": arch, "fault": fault,
                "task": task, "seed": seed, "observed": fault_step is not None,
                "fault_step": fault_step, "dom_detection_uncertain": dom_uncertain,
                "continued": continued, "retried_same_tool": retried,
                "retry_succeeded": retry_succeeded,
                "step_delta": step_delta, "llm_delta": llm_delta,
                "success": official.get((model, arch, fault, task, seed, "fault")),
                "control_success": official.get((model, arch, fault, task, seed, "control")),
            })

    def rate(rows, key):
        vals = [r[key] for r in rows if r[key] is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    def mean(rows, key):
        vals = [r[key] for r in rows if r[key] is not None]
        return round(statistics.mean(vals), 3) if vals else None

    summary = {}
    for fault in FAULTS:
        rows = [r for r in records if r["fault"] == fault]
        observed = [r for r in rows if r["observed"]]
        summary[fault] = {
            "n_triggered": len(rows),
            "n_observed": len(observed),
            "observed_rate": round(len(observed) / len(rows), 4) if rows else None,
            "dom_detection_uncertain": fault == "web_dom_missing",
            "continued_rate": rate(observed, "continued"),
            "retried_rate": rate(observed, "retried_same_tool"),
            "retry_succeeded_rate": rate(observed, "retry_succeeded"),
            "step_delta_mean": mean(rows, "step_delta"),
            "llm_delta_mean": mean(rows, "llm_delta"),
            "official_success_observed": rate(observed, "success"),
            "official_success_control_paired": rate(observed, "control_success"),
            "official_success_all": rate(rows, "success"),
        }

    OUT.mkdir(parents=True, exist_ok=True)
    json.dump({"records": records, "by_fault": summary}, open(OUT / "recovery.json", "w"), indent=1)
    lines = ["# 故障后行为与配对代价（现有 trace，官方判定）", "",
             "| 故障 | 触发n | 看到 | 继续执行 | 重试同工具 | 重试成功 | Δ步数(配对) | ΔLLM | 成功(看到) | 控制臂 | 备注 |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for fault in FAULTS:
        s = summary[fault]
        note = "观察率检测不确定" if s["dom_detection_uncertain"] else ""
        lines.append(f"| {fault} | {s['n_triggered']} | {s['n_observed']} ({s['observed_rate']}) | "
                     f"{s['continued_rate']} | {s['retried_rate']} | {s['retry_succeeded_rate']} | "
                     f"{s['step_delta_mean']:+} | {s['llm_delta_mean']:+} | "
                     f"{s['official_success_observed']} | {s['official_success_control_paired']} | {note} |")
    lines.append("")
    lines.append("> 「继续执行」= 下一步无报错/离开假错误页；「重试成功」= 下一步与原动作同工具且无报错；"
                 "二者都不等于最终成功。Δ步数为故障臂减配对控制臂。")
    open(OUT / "recovery.md", "w").write("\n".join(lines) + "\n")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
