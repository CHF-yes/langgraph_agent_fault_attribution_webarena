"""Stage C 主分析：配对退化、Model × Architecture 交互、区间与多重比较。

预注册要点（与 ``docs/stage_c_analysis_plan.md`` 一致，先写后跑）：

* 观测单位是一次 trial，结局是官方 evaluator 的二元判定（成功/失败）。**完成率不进入
  任何主分析**，它不是正确性。
* 主估计量：每个 ``(model, architecture, fault)`` 的配对抗动
  ``Δ = P(成功|control) − P(成功|fault)``。先在同一 ``(task, seed)`` 内配对，再对任务
  取等权平均——任务才是独立抽样单位，不能把同一任务的两次重复当成两个独立任务。
* 区间：按**任务**做整群自助（cluster bootstrap），固定随机种子，保证可复现。
* 交互：以二元成功率为因变量的线性概率模型，含 condition×model×architecture 全交互；
  ``condition:model:architecture`` 系数就是"退化量上的 Model × Architecture 交互"，
  与 ``Condition × Model × Architecture`` 是同一个检验的两种写法。推断用按任务聚类的
  稳健标准误。
* 多重比较：三个故障的退化检验构成预注册家族，用 Holm 校正；交互检验作为单一预注册
  对比报告原 p 值，并在多于一个交互检验时同样给出 Holm 校正值。
* 验证模型（V4 Pro）只在共同 8 任务上做次级分析，绝不进入 16 任务主估计。
"""

from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict

Z_95 = 1.959963984540054
DEFAULT_BOOTSTRAP = 10000
DEFAULT_SEED = 20260921
DEFAULT_INTERACTION_BOOTSTRAP = 4000

CONTRACT_ERROR = "error"

# Holm 校正的检验家族：**按故障**的三个主对比（两个主模型 × 两个架构合并，
# 任务为聚类单位）。逐 (model, architecture, fault) 的格子只作描述性报告，
# 不进入这个家族——否则家族会随模型/架构数量变化，校正失去意义。
HOLM_FAMILY_ID = "per_fault_primary"
HOLM_FAMILY_DESCRIPTION = ("三个故障各自的配对退化（两个主模型 × 两个架构合并，"
                           "任务整群、按类别分层）")
CONTRACT_NATIVE = "native"
CONTRACT_COMPATIBILITY = "compatibility"


# --------------------------------------------------------------------------
# 观测整理
# --------------------------------------------------------------------------

def build_observations(rows: list[dict]) -> list[dict]:
    """把 pipeline 的行转成分析用观测。

    只接受已经由官方 evaluator 判定、且不是 evaluator error 的行；compatibility
    与 native 都计入，但会被标记，便于做敏感性分析。
    """
    observations = []
    for row in rows:
        if row.get("official_success") is None:
            continue
        if row.get("evaluation_status") == CONTRACT_ERROR:
            continue
        cell = row.get("cell") or {}
        record = {"model_profile": cell.get("model_profile") or row.get("model_profile"),
                  "architecture": cell.get("architecture") or row.get("architecture"),
                  "fault_type": cell.get("fault_type") or row.get("fault_type"),
                  "task_id": int(cell.get("task_id", row.get("task_id"))),
                  "condition": cell.get("condition") or row.get("condition"),
                  "seed": int(cell.get("fault_seed", row.get("seed", 0))),
                  "replicate": row.get("replicate"),
                  "official_success": int(bool(row.get("official_success"))),
                  "evaluation_status": row.get("evaluation_status")}
        observations.append(record)
    return observations


def _key(obs: dict, *, with_condition: bool) -> tuple:
    base = (obs["model_profile"], obs["architecture"], obs["fault_type"], obs["task_id"], obs["seed"])
    return base + ((obs["condition"],) if with_condition else ())


def pair_observations(observations: list[dict]) -> tuple[list[dict], list[dict]]:
    """按 (model, architecture, fault, task, seed) 配对 control/fault。

    返回 ``(pairs, unpaired)``；``unpaired`` 里的条目会被报告而不是被静默丢弃。
    """
    buckets: dict[tuple, dict] = defaultdict(dict)
    for obs in observations:
        buckets[_key(obs, with_condition=False)][obs["condition"]] = obs
    pairs, unpaired = [], []
    for key, arms in sorted(buckets.items()):
        if "control" in arms and "fault" in arms:
            pairs.append({
                "model_profile": key[0], "architecture": key[1], "fault_type": key[2],
                "task_id": key[3], "seed": key[4],
                "control": arms["control"]["official_success"],
                "fault": arms["fault"]["official_success"],
                "delta": arms["control"]["official_success"] - arms["fault"]["official_success"],
                "evaluation_status": sorted({arms["control"]["evaluation_status"],
                                             arms["fault"]["evaluation_status"]}),
            })
        else:
            unpaired.append({"key": key, "arms": sorted(arms)})
    return pairs, unpaired


# --------------------------------------------------------------------------
# 统计工具
# --------------------------------------------------------------------------

def wilson_interval(successes: int, total: int, z: float = Z_95) -> tuple[float, float]:
    """Wilson 区间；对 0/n 与 n/n 也给出有限区间。"""
    if total <= 0:
        return (0.0, 1.0)
    phat = successes / total
    denominator = 1 + z * z / total
    centre = (phat + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(phat * (1 - phat) / total + z * z / (4 * total * total)) / denominator
    return (max(0.0, centre - margin), min(1.0, centre + margin))



def resample_tasks_by_stratum(strata: dict[str, list], rng: random.Random) -> list:
    """在**每个类别内部**重采样任务，且保持每类的任务数不变。

    任务才是独立抽样单位（16 个任务提供主要信息量），960 条 trial 不是 960 个
    独立样本：同一任务下的故障/架构/模型/两次重复必须整体进出样本。按类别分层
    是为了不让重采样偶然打破四个预注册类别的构成（每类 4 个主任务、2 个验证任务）。
    """
    sampled: list = []
    for _category, tasks in sorted(strata.items()):
        if not tasks:
            continue
        for _ in range(len(tasks)):
            sampled.append(tasks[rng.randrange(len(tasks))])
    return sampled


def strata_for_tasks(category_of: dict | None, tasks) -> dict[str, list]:
    """把 ``task -> category`` 映射转成 ``category -> [tasks]``，只保留出现过的任务。"""
    strata: dict[str, list] = defaultdict(list)
    for task in sorted(set(tasks)):
        category = (category_of or {}).get(int(task)) or "uncategorized"
        strata[str(category)].append(task)
    return dict(strata)


def cluster_bootstrap_ci(values_by_task: dict, *,
                         strata: dict[str, list] | None = None,
                         n_boot: int = DEFAULT_BOOTSTRAP, seed: int = DEFAULT_SEED,
                         alpha: float = 0.05) -> tuple[float, float]:
    """按任务整群自助得到"任务等权均值"的百分位区间。

    传入 ``strata`` 时按类别分层重采样（类别内抽样、每类任务数不变）；不传则对所有
    任务等概率重采样。两种情形都只重采样**任务**，绝不下沉到 trial 级。
    """
    tasks = sorted(values_by_task)
    if not tasks:
        return (float("nan"), float("nan"))
    if not strata:
        strata = {"all": list(tasks)}
    else:
        strata = {category: [task for task in category_tasks if task in values_by_task]
                  for category, category_tasks in strata.items()}
        strata = {category: category_tasks for category, category_tasks in strata.items()
                  if category_tasks}
    rng = random.Random(seed)
    estimates = []
    for _ in range(n_boot):
        total, count = 0.0, 0
        for task in resample_tasks_by_stratum(strata, rng):
            values = values_by_task[task]
            total += sum(values) / len(values)
            count += 1
        if count:
            estimates.append(total / count)
    estimates.sort()
    low = estimates[int(math.floor(alpha / 2 * len(estimates)))]
    high = estimates[min(len(estimates) - 1, int(math.ceil((1 - alpha / 2) * len(estimates)) - 1))]
    return (low, high)


def ols(X: list[list[float]], y: list[float]) -> list[float]:
    """最小二乘解（正规方程 + 高斯-约当消元，纯标准库）。"""
    xtx, xty = normal_equations(X, y)
    return solve(xtx, xty)


def normal_equations(X: list[list[float]],
                     y: list[float]) -> tuple[list[list[float]], list[float]]:
    """累加 X'X 与 X'y。小矩阵逐次累加，便于按任务预聚合。"""
    k = len(X[0]) if X else 0
    xtx = [[0.0] * k for _ in range(k)]
    xty = [0.0] * k
    for row, target in zip(X, y):
        for i in range(k):
            value = row[i]
            if value == 0.0:
                continue
            xty[i] += value * target
            for j in range(k):
                xtx[i][j] += value * row[j]
    return xtx, xty


def add_normal_equations(a: tuple, b: tuple) -> tuple:
    """把两个 (X'X, X'y) 相加，用于自助重采样任务。"""
    xtx_a, xty_a = a
    xtx_b, xty_b = b
    xtx = [[x + z for x, z in zip(row_a, row_b)] for row_a, row_b in zip(xtx_a, xtx_b)]
    xty = [x + z for x, z in zip(xty_a, xty_b)]
    return xtx, xty


def solve(a: list[list[float]], b: list[float]) -> list[float]:
    """高斯-约当消元（列主元），求解 a·x = b。"""
    n = len(a)
    augmented = [list(row) + [b[i]] for i, row in enumerate(a)]
    for column in range(n):
        pivot = max(range(column, n), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            continue  # 奇异：该方向没有信息，系数保持 0
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(n):
            if row == column:
                continue
            factor = augmented[row][column]
            if factor == 0.0:
                continue
            augmented[row] = [value - factor * base
                              for value, base in zip(augmented[row], augmented[column])]
    return [augmented[i][n] for i in range(n)]


def inverse(a: list[list[float]]) -> list[list[float]]:
    """矩阵求逆（对单位阵逐列求解）。"""
    n = len(a)
    columns = []
    for index in range(n):
        unit = [0.0] * n
        unit[index] = 1.0
        columns.append(solve(a, unit))
    return [[columns[j][i] for j in range(n)] for i in range(n)]


def matvec(matrix: list[list[float]], vector: list[float]) -> list[float]:
    """矩阵乘向量。"""
    return [sum(value * item for value, item in zip(row, vector)) for row in matrix]


def outer(vector: list[float]) -> list[list[float]]:
    """向量外积。"""
    return [[a * b for b in vector] for a in vector]


def matadd(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    """矩阵相加。"""
    return [[x + z for x, z in zip(row_a, row_b)] for row_a, row_b in zip(a, b)]


def cluster_robust_cov(X: list[list[float]], residuals: list[float],
                       clusters: list) -> list[list[float]]:
    """按聚类（任务）的稳健协方差，含常用的小样本修正。"""
    n = len(X)
    k = len(X[0]) if X else 0
    xtx = [[0.0] * k for _ in range(k)]
    for row in X:
        for i in range(k):
            for j in range(k):
                xtx[i][j] += row[i] * row[j]
    xtx_inv = inverse(xtx)
    meat = [[0.0] * k for _ in range(k)]
    groups: dict = defaultdict(list)
    for index, cluster in enumerate(clusters):
        groups[cluster].append(index)
    for indices in groups.values():
        score = [0.0] * k
        for index in indices:
            row = X[index]
            residual = residuals[index]
            for i in range(k):
                score[i] += row[i] * residual
        meat = matadd(meat, outer(score))
    group_count = len(groups)
    if group_count > 1:
        correction = group_count / (group_count - 1)
        meat = [[value * correction for value in row] for row in meat]
    # xtx_inv @ meat @ xtx_inv
    left = [matvec(xtx_inv, row) for row in meat]          # 行向量左乘
    result = [[0.0] * k for _ in range(k)]
    for i in range(k):
        for j in range(k):
            result[i][j] = sum(left[i][m] * xtx_inv[m][j] for m in range(k))
    return result


def holm_adjust(pvalues: list[float]) -> list[float]:
    """Holm 逐步法校正，保持与输入顺序一致。"""
    m = len(pvalues)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda index: pvalues[index])
    adjusted = [0.0] * m
    running = 0.0
    for rank, index in enumerate(order):
        value = min(1.0, (m - rank) * pvalues[index])
        running = max(running, value)
        adjusted[index] = running
    return adjusted


def _normal_two_sided_p(z: float) -> float:
    """双侧正态 p 值。"""
    return 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))


# --------------------------------------------------------------------------
# 主估计：配对退化
# --------------------------------------------------------------------------

def degradation_by_cell(pairs: list[dict], *, model_profile: str | None = None,
                        category_of: dict | None = None,
                        n_boot: int = DEFAULT_BOOTSTRAP,
                        seed: int = DEFAULT_SEED) -> list[dict]:
    """按 (model, architecture, fault) 汇总配对退化。"""
    grouped: dict[tuple, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    for pair in pairs:
        if model_profile is not None and pair["model_profile"] != model_profile:
            continue
        grouped[(pair["model_profile"], pair["architecture"], pair["fault_type"])][
            pair["task_id"]].append(float(pair["delta"]))

    results = []
    for (model, architecture, fault), by_task in sorted(grouped.items()):
        per_task_means = [sum(values) / len(values) for values in by_task.values()]
        n_pairs = sum(len(values) for values in by_task.values())
        mean_delta = sum(per_task_means) / len(per_task_means) if per_task_means else float("nan")
        strata = strata_for_tasks(category_of, by_task)
        low, high = cluster_bootstrap_ci(by_task, strata=strata, n_boot=n_boot, seed=seed)
        control_successes = control_total = fault_successes = fault_total = 0
        for pair in pairs:
            if (pair["model_profile"], pair["architecture"], pair["fault_type"]) != (model, architecture, fault):
                continue
            if model_profile is not None and pair["model_profile"] != model_profile:
                continue
            control_successes += pair["control"]
            fault_successes += pair["fault"]
            control_total += 1
            fault_total += 1
        # 退化量的检验：任务等权均值的正态近似（区间用整群自助）。
        per_task_variance = (statistics.variance(per_task_means)
                             if len(per_task_means) > 1 else 0.0)
        stderr = math.sqrt(per_task_variance / len(per_task_means)) if per_task_means else float("nan")
        z = mean_delta / stderr if stderr and stderr > 0 else float("nan")
        results.append({
            "model_profile": model, "architecture": architecture, "fault_type": fault,
            "n_pairs": n_pairs, "n_tasks": len(by_task),
            "control_success_rate": control_successes / control_total if control_total else None,
            "fault_success_rate": fault_successes / fault_total if fault_total else None,
            "control_wilson": wilson_interval(control_successes, control_total) if control_total else None,
            "fault_wilson": wilson_interval(fault_successes, fault_total) if fault_total else None,
            "degradation": mean_delta,
            "ci_low": low, "ci_high": high,
            "std_error": stderr if stderr == stderr else None,
            "p_value": _normal_two_sided_p(z) if z == z else None,
            "degenerate": bool(control_total and (control_successes in (0, control_total)
                                                 or fault_successes in (0, fault_total))),
        })
    return results


def interaction_on_degradation(observations: list[dict], *, models: list[str] | None = None,
                               category_of: dict | None = None,
                               n_boot: int = DEFAULT_INTERACTION_BOOTSTRAP,
                               seed: int = DEFAULT_SEED) -> dict:
    """估计二元成功率上的 condition×model×architecture 交互。

    等价于"退化量上的 Model × Architecture 交互"：把 Δ 直接对 model×architecture
    做回归，三阶交互系数与这里的 ``condition:model:architecture`` 系数相同。

    需要恰好两个模型与两个架构；其余情况返回 ``{"error": ...}``，不猜测。
    """
    observations = [obs for obs in observations if obs["condition"] in ("control", "fault")]
    if models is not None:
        observations = [obs for obs in observations if obs["model_profile"] in models]
    model_values = sorted({obs["model_profile"] for obs in observations})
    architecture_values = sorted({obs["architecture"] for obs in observations})
    if len(model_values) != 2 or len(architecture_values) != 2:
        return {"error": "interaction requires exactly two models and two architectures",
                "models": model_values, "architectures": architecture_values}
    if not observations:
        return {"error": "no observations"}

    model_index = {value: index for index, value in enumerate(model_values)}
    architecture_index = {value: index for index, value in enumerate(architecture_values)}

    def design_row(observation: dict) -> list[float]:
        condition = 1.0 if observation["condition"] == "fault" else 0.0
        model = float(model_index[observation["model_profile"]])
        architecture = float(architecture_index[observation["architecture"]])
        return [1.0, condition, model, architecture,
                condition * model, condition * architecture, model * architecture,
                condition * model * architecture]

    rows = [design_row(obs) for obs in observations]
    targets = [float(obs["official_success"]) for obs in observations]
    clusters = [obs["task_id"] for obs in observations]
    xtx, xty = normal_equations(rows, targets)
    beta = solve(xtx, xty)
    residuals = [target - sum(value * coefficient for value, coefficient in zip(row, beta))
                 for row, target in zip(rows, targets)]
    covariance = cluster_robust_cov(rows, residuals, clusters)
    index = 7
    coefficient = beta[index]
    variance = covariance[index][index]
    stderr = math.sqrt(variance) if variance > 0 else float("nan")
    z = coefficient / stderr if stderr and stderr > 0 else float("nan")

    # 按任务预聚合 (X'X, X'y)，自助时只做 8×8 矩阵相加，避免逐样本重算。
    per_task: dict = defaultdict(lambda: (None, None))
    grouped_rows: dict = defaultdict(list)
    grouped_targets: dict = defaultdict(list)
    for row, target, task in zip(rows, targets, clusters):
        grouped_rows[task].append(row)
        grouped_targets[task].append(target)
    for task, task_rows in grouped_rows.items():
        per_task[task] = normal_equations(task_rows, grouped_targets[task])

    rng = random.Random(seed)
    interaction_strata = strata_for_tasks(category_of, per_task)
    if not interaction_strata:
        interaction_strata = {"all": sorted(per_task)}
    estimates = []
    for _ in range(n_boot):
        accumulated = None
        for task in resample_tasks_by_stratum(interaction_strata, rng):
            accumulated = per_task[task] if accumulated is None else add_normal_equations(
                accumulated, per_task[task])
        if accumulated is not None:
            estimates.append(solve(accumulated[0], accumulated[1])[index])
    estimates.sort()
    low = estimates[int(math.floor(0.025 * len(estimates)))]
    high = estimates[min(len(estimates) - 1, int(math.ceil(0.975 * len(estimates)) - 1))]
    return {
        "models": model_values, "architectures": architecture_values,
        "n_observations": len(observations),
        "coefficient_condition_x_model_x_architecture": coefficient,
        "std_error_cluster_robust": stderr if stderr == stderr else None,
        "z": z if z == z else None,
        "p_value": _normal_two_sided_p(z) if z == z else None,
        "bootstrap_ci_low": low, "bootstrap_ci_high": high,
        "bootstrap_resamples": n_boot,
        "interpretation": ("该系数 >0 表示：某一 (model, architecture) 组合的故障退化量"
                           "高于另一组合；即退化量上存在 Model × Architecture 交互。"),
    }


# --------------------------------------------------------------------------
# 作用域约束（V4 Pro 只能做共同 8 任务的次级分析）
# --------------------------------------------------------------------------


def degradation_by_fault(pairs: list[dict], *, category_of: dict | None = None,
                         n_boot: int = DEFAULT_BOOTSTRAP,
                         seed: int = DEFAULT_SEED) -> list[dict]:
    """Holm 家族的三个成员：按故障合并 model/architecture 的配对退化。

    每个故障一个检验：先在同一 (task, model, architecture, seed) 内配对，再对
    任务取等权平均（任务内先平均掉模型/架构/重复），任务整群、按类别分层自助。
    """
    grouped: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    for pair in pairs:
        grouped[pair["fault_type"]][pair["task_id"]].append(float(pair["delta"]))

    results = []
    for fault, by_task in sorted(grouped.items()):
        per_task = {task: sum(values) / len(values) for task, values in by_task.items()}
        mean_delta = sum(per_task.values()) / len(per_task) if per_task else float("nan")
        strata = strata_for_tasks(category_of, by_task)
        low, high = cluster_bootstrap_ci(by_task, strata=strata, n_boot=n_boot, seed=seed)
        variance = statistics.variance(list(per_task.values())) if len(per_task) > 1 else 0.0
        stderr = math.sqrt(variance / len(per_task)) if per_task else float("nan")
        z = mean_delta / stderr if stderr and stderr > 0 else float("nan")
        results.append({
            "family_id": HOLM_FAMILY_ID,
            "fault_type": fault,
            "n_pairs": sum(len(values) for values in by_task.values()),
            "n_tasks": len(by_task),
            "degradation": mean_delta,
            "ci_low": low, "ci_high": high,
            "std_error": stderr if stderr == stderr else None,
            "p_value": _normal_two_sided_p(z) if z == z else None,
        })
    return results


def assess_matrix(rows: list[dict], design: dict, *, models: list[str],
                  architectures: list[str]) -> dict:
    """判断这个矩阵能否支撑正式显著性结论。

    缺格、产物不完整、未评分、评分报错、以及控制/故障臂未配对，任何一项存在都
    不允许输出正式推断——此时只能给描述性统计。判断与管道审计用同一套口径：
    ``evaluated + missing + incomplete + unevaluated + error == expected``。
    """
    from standard_agent.stage_c_pipeline import expected_cells

    expected_keys = set()
    for model in models:
        for architecture in architectures:
            for cell in expected_cells(design, model_profile=model,
                                       architecture=architecture):
                expected_keys.add((cell["model_profile"], cell["architecture"],
                                   cell["fault_type"], int(cell["task_id"]),
                                   int(cell["seed"]), cell["condition"]))

    present: dict[tuple, dict] = {}
    for row in rows:
        cell = row.get("cell")
        if not cell:
            continue
        key = (cell["model_profile"], cell["architecture"], cell["fault_type"],
               int(cell["task_id"]), int(cell["fault_seed"]), cell["condition"])
        if key in expected_keys:
            present[key] = row

    missing = sorted(expected_keys - set(present))
    incomplete, unevaluated, errors = [], [], []
    for key, row in present.items():
        if not row.get("complete"):
            incomplete.append(key)
        elif row.get("evaluation_status") == CONTRACT_ERROR:
            errors.append(key)
        elif row.get("evaluation_status") not in (CONTRACT_NATIVE, CONTRACT_COMPATIBILITY):
            unevaluated.append(key)

    evaluated_keys = {key for key, row in present.items()
                      if row.get("complete")
                      and row.get("evaluation_status") in (CONTRACT_NATIVE, CONTRACT_COMPATIBILITY)}
    unpaired = []
    for (model, architecture, fault, task, seed, _condition) in sorted(expected_keys):
        arms = {key[5] for key in expected_keys
                if key[:5] == (model, architecture, fault, task, seed)}
        if not arms <= {"control", "fault"}:
            continue
        have = {key[5] for key in evaluated_keys
                if key[:5] == (model, architecture, fault, task, seed)}
        if have != {"control", "fault"}:
            unpaired.append((model, architecture, fault, task, seed, sorted(have)))

    blocked = []
    if missing:
        blocked.append(f"{len(missing)} 个期望格子没有产物")
    if incomplete:
        blocked.append(f"{len(incomplete)} 个格子产物不完整")
    if unevaluated:
        blocked.append(f"{len(unevaluated)} 个格子未评分")
    if errors:
        blocked.append(f"{len(errors)} 个格子评分报错")
    if unpaired:
        blocked.append(f"{len(unpaired)} 组控制/故障臂未配平")

    return {
        "expected_cells": len(expected_keys),
        "evaluated_cells": len(evaluated_keys),
        "missing_cells": missing,
        "incomplete_cells": incomplete,
        "unevaluated_cells": unevaluated,
        "error_cells": errors,
        "unpaired_pairs": unpaired,
        "formal_inference_allowed": not blocked,
        "blocked_reasons": blocked,
    }


def check_validation_scope(observations: list[dict], design: dict) -> dict:
    """检查验证模型是否只出现在共同 8 任务上。"""
    validation_model = design.get("validation_model")
    allowed = set(design.get("validation_tasks") or [])
    violations = sorted({obs["task_id"] for obs in observations
                         if obs["model_profile"] == validation_model
                         and obs["task_id"] not in allowed})
    return {"validation_model": validation_model,
            "allowed_tasks": sorted(allowed),
            "violations": violations,
            "ok": not violations}




def cap_exhaustion_summary(rows: list[dict]) -> dict:
    """步数耗尽率的**描述性**统计。

    这里只回答"多少格跑满了预算"，不是成功率的一部分：``official_success_rate``
    的计算完全不引用本函数。耗尽格在成功率里照常按 evaluator 判定计（通常失败），
    但报告必须能看出失败是"预算耗尽"还是"答错"。
    """
    def bucket(selector):
        agg: dict = {}
        for row in rows:
            cell = row.get("cell") or {}
            key = selector(row, cell)
            if key is None:
                continue
            entry = agg.setdefault(key, {"present": 0, "exhausted": 0,
                                         "exhausted_steps": []})
            entry["present"] += 1
            if row.get("cap_exhausted"):
                entry["exhausted"] += 1
                if isinstance(row.get("steps"), int):
                    entry["exhausted_steps"].append(row["steps"])
        for entry in agg.values():
            entry["rate"] = (entry["exhausted"] / entry["present"]
                             if entry["present"] else None)
            entry["mean_steps_when_exhausted"] = (
                sum(entry["exhausted_steps"]) / len(entry["exhausted_steps"])
                if entry["exhausted_steps"] else None)
            entry.pop("exhausted_steps", None)
        return agg

    overall = bucket(lambda row, cell: "all")
    by_condition = bucket(lambda row, cell: cell.get("condition"))
    by_fault = bucket(lambda row, cell: cell.get("fault_type"))
    by_cell = bucket(lambda row, cell: "/".join(str(cell.get(k)) for k in (
        "model_profile", "architecture", "fault_type", "task_id")))
    steps = [row["steps"] for row in rows if isinstance(row.get("steps"), int)]
    return {
        "overall": overall.get("all"),
        "by_condition": by_condition,
        "by_fault": by_fault,
        "by_cell": by_cell,
        "mean_steps": (sum(steps) / len(steps)) if steps else None,
        "note": ("描述性指标：耗尽格仍按官方 evaluator 判定计入成功率，二者不得替换。"),
    }

def main_analysis(rows: list[dict], design: dict, *, n_boot: int = DEFAULT_BOOTSTRAP,
                  seed: int = DEFAULT_SEED) -> dict:
    """完整主分析：主模型 16 任务 + 验证模型共同 8 任务次级分析。

    **前置健康检查不通过时拒绝输出正式显著性结论**：p 值与 Holm 校正一律置空，
    只保留描述性点估计与区间，并写明阻塞原因。
    """
    observations = build_observations(rows)
    scope = check_validation_scope(observations, design)
    main_models = list(design.get("main_models") or [])
    architectures = list(design.get("architectures") or [])
    health = assess_matrix(rows, design, models=main_models, architectures=architectures)
    allowed = bool(health["formal_inference_allowed"]) and bool(scope["ok"])

    main_observations = [obs for obs in observations if obs["model_profile"] in main_models]
    pairs, unpaired = pair_observations(main_observations)
    degradations = degradation_by_cell(pairs, category_of=design.get("category_of"),
                                      n_boot=n_boot, seed=seed)

    # Holm 家族 = 三个故障级主对比；逐格结果只作描述性。
    fault_level = degradation_by_fault(pairs, category_of=design.get("category_of"),
                                       n_boot=n_boot, seed=seed)
    raw = [item["p_value"] if item["p_value"] is not None else 1.0 for item in fault_level]
    adjusted = holm_adjust(raw)
    for item, value in zip(fault_level, adjusted):
        item["p_value_holm"] = value
        item["in_holm_family"] = True

    interaction = interaction_on_degradation(main_observations, models=main_models,
                                            category_of=design.get("category_of"),
                                            n_boot=n_boot, seed=seed)

    if not allowed:
        reason = list(health["blocked_reasons"])
        if not scope["ok"]:
            reason.append(f"验证模型出现在共同 8 任务之外: {scope['violations']}")
        for item in degradations:
            item["p_value"] = None
            item["p_value_holm"] = None
            item["in_holm_family"] = False
            item["inference_permitted"] = False
        for item in fault_level:
            item["p_value"] = None
            item["p_value_holm"] = None
            item["inference_permitted"] = False
        # 只把逐项 p 值置空还不够：家族里保存的校正前后数值同样是显著性结论，
        # 留在产物里会被下游当成可用结果，必须一并清空。
        raw = []
        adjusted = []
        if "error" not in interaction:
            interaction["p_value"] = None
            interaction["inference_permitted"] = False
            interaction["inference"] = "blocked"
        interaction["blocked_reasons"] = reason
    else:
        for item in degradations:
            item["in_holm_family"] = False
            item["inference_permitted"] = True
        for item in fault_level:
            item["inference_permitted"] = True
        if "error" not in interaction:
            interaction["inference_permitted"] = True

    validation_model = design.get("validation_model")
    validation_observations = [obs for obs in observations
                               if obs["model_profile"] == validation_model]
    validation_pairs, _ = pair_observations(validation_observations)
    validation_degradations = degradation_by_cell(
        validation_pairs, category_of=design.get("category_of"), n_boot=n_boot, seed=seed)
    for item in validation_degradations:      # 次级分析一律不报显著性
        item["p_value"] = None
        item["p_value_holm"] = None
        item["in_holm_family"] = False
        item["inference_permitted"] = False

    return {
        "scope": scope,
        "matrix_health": health,
        "formal_inference_allowed": allowed,
        "main_models": main_models,
        "main_tasks": sorted({obs["task_id"] for obs in main_observations}),
        "n_observations_main": len(main_observations),
        "n_pairs_main": len(pairs),
        "unpaired": unpaired,
        "degradation_by_cell": degradations,
        "degradation_by_fault": fault_level,
        "cap_exhaustion": cap_exhaustion_summary(rows),
        "interaction": interaction,
        "holm_family": {
            "family_id": HOLM_FAMILY_ID,
            "description": HOLM_FAMILY_DESCRIPTION,
            "members": [item["fault_type"] for item in fault_level],
            "method": "Holm",
            "raw_p_values": raw,
            "adjusted_p_values": adjusted,
        },
        "secondary_validation_model": {
            "model_profile": validation_model,
            "tasks": sorted({obs["task_id"] for obs in validation_observations}),
            "n_pairs": len(validation_pairs),
            "degradation_by_cell": validation_degradations,
        },
        "notes": [
            "退化量定义为 control 成功率减 fault 成功率（百分点 = ×100）。",
            "区间按任务整群、按四个预注册类别分层自助；重复 trial 不是独立样本。",
            "Holm 家族固定为三个故障级主对比；逐 (model, architecture, fault) 格子为描述性。",
            "矩阵不健康（缺格/未配对/评分报错）时不输出 p 值，只给描述性区间；"
            "不显著只能报告为功效不足，不能当作等价性证据。",
            "V4 Pro 仅用于共同 8 任务次级分析，不进入主估计，也不报显著性。",
        ],
    }


def format_report(report: dict) -> str:
    """把主分析结果渲染成 Markdown 片段。"""
    lines = ["## Stage C 主分析", ""]
    scope = report.get("scope") or {}
    lines.append(f"- 验证模型作用域检查: {'通过' if scope.get('ok') else '违规 ' + str(scope.get('violations'))}")
    if not report.get("formal_inference_allowed", True):
        health = report.get("matrix_health") or {}
        lines.append("")
        lines.append("> ⛔ **正式显著性推断已被拒绝**（矩阵不健康）：")
        for reason in health.get("blocked_reasons") or []:
            lines.append(f"> - {reason}")
        lines.append("> 以下数值仅为描述性，p 值已置空。")
    lines.append(f"- 主模型: {', '.join(report.get('main_models') or [])}")
    lines.append(f"- 主观测数: {report.get('n_observations_main')}，配对数: {report.get('n_pairs_main')}")
    if report.get("unpaired"):
        lines.append(f"- ⚠️ 未配对条目: {len(report['unpaired'])}")
    lines.append("")
    lines.append("| 故障 | 配对 | Δ(pp) | 95% CI | p | p(Holm) |")
    lines.append("|---|---|---|---|---|---|")
    for item in report.get("degradation_by_fault") or []:
        lines.append("| {fault} | {n} | {d:+.1f} | [{lo:+.1f}, {hi:+.1f}] | {p} | {ph} |".format(
            fault=item["fault_type"], n=item["n_pairs"],
            d=100 * item["degradation"] if item["degradation"] == item["degradation"] else float("nan"),
            lo=100 * item["ci_low"] if item["ci_low"] == item["ci_low"] else float("nan"),
            hi=100 * item["ci_high"] if item["ci_high"] == item["ci_high"] else float("nan"),
            p="—" if item["p_value"] is None else f"{item['p_value']:.4f}",
            ph="—" if item.get("p_value_holm") is None else f"{item['p_value_holm']:.4f}",
        ))
    cap = report.get("cap_exhaustion") or {}
    if cap.get("overall"):
        lines.append("")
        lines.append("步数耗尽（描述性，不计入成功率）："
                     f"总体 {cap['overall']['exhausted']}/{cap['overall']['present']}"
                     f" = {(cap['overall']['rate'] or 0) * 100:.1f}%"
                     + (f"，均值步数 {cap['mean_steps']:.1f}" if cap.get('mean_steps') else ""))
        for key, entry in sorted((cap.get("by_fault") or {}).items()):
            lines.append(f"  - 故障 `{key}`: {entry['exhausted']}/{entry['present']}"
                         f" = {(entry['rate'] or 0) * 100:.1f}%")
    lines.append("")
    lines.append(f"Holm 家族：{report.get('holm_family', {}).get('family_id')} "
                 f"= {report.get('holm_family', {}).get('members')}")
    lines.append("")
    lines.append("逐 (model, architecture, fault) 描述性结果：")
    lines.append("")
    lines.append("| model | arch | fault | n_pair | control | fault | Δ(pp) | 95% CI | p |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for item in report.get("degradation_by_cell") or []:
        lines.append("| {model} | {arch} | {fault} | {n} | {c:.3f} | {f:.3f} | {d:+.1f} | [{lo:+.1f}, {hi:+.1f}] | {p} |".format(
            model=item["model_profile"], arch=item["architecture"], fault=item["fault_type"],
            n=item["n_pairs"],
            c=item["control_success_rate"] if item["control_success_rate"] is not None else float("nan"),
            f=item["fault_success_rate"] if item["fault_success_rate"] is not None else float("nan"),
            d=100 * item["degradation"] if item["degradation"] == item["degradation"] else float("nan"),
            lo=100 * item["ci_low"] if item["ci_low"] == item["ci_low"] else float("nan"),
            hi=100 * item["ci_high"] if item["ci_high"] == item["ci_high"] else float("nan"),
            p="—" if item["p_value"] is None else f"{item['p_value']:.4f}",
        ))
    interaction = report.get("interaction") or {}
    if report.get("formal_inference_allowed", True):
        p_values = [item.get("p_value") for item in (report.get("degradation_by_fault") or [])]
        p_values += [item.get("p_value") for item in (report.get("degradation_by_cell") or [])]
        if any(value is None for value in p_values):
            lines.append("")
            lines.append("> 表中 `—` 表示该对比的聚类稳健标准误为 0（无任务间变异），"
                         "无法给出 p 值；此时以整群自助区间为准。")
    lines.append("")
    if interaction.get("error"):
        lines.append(f"- 交互检验: 无法估计（{interaction['error']}）")
    else:
        p_text = "—" if interaction.get("p_value") is None else f"{interaction['p_value']:.4f}"
        lines.append(f"- **Model × Architecture 交互**（condition:model:architecture）: "
                     f"{interaction['coefficient_condition_x_model_x_architecture']:+.4f}, "
                     f"p={p_text}, "
                     f"95% CI [{interaction['bootstrap_ci_low']:+.4f}, {interaction['bootstrap_ci_high']:+.4f}]")
    lines.append("")
    lines.extend(["> " + note for note in (report.get("notes") or [])])
    return "\n".join(lines) + "\n"
