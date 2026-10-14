"""E-P 主分析：variant 之间的差中差 D_f。

对故障 f、variant v，定义
    Δ_v = P(官方成功 | control, v) − P(官方成功 | fault, v)
核心持续性对比是
    D_f = Δ_repeat3 − Δ_single
正值表示"重复暴露"相对"一次性"增加了成功率退化。

口径（设计第 6 节）：
* 主口径 = **按分配**：全部已分配且有合法官方评分的配对，含未触发/部分剂量/提前结束；
* 先在同 (task, model, arch, fault, seed) 内算 Δ，再在**任务之间等权**汇总；
* 实际触发子集、三次完整剂量子集只作**选择性机制描述**，不得当作随机分配的因果效果；
* 4 任务版本**不输出确认性 p 值**，也不把退化区间当作跨任务泛化证据；
* evaluator error/incomplete/missing 单列并阻断完整矩阵推断。
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict

from fault_injection.episode import dose_class

EP_VARIANTS = ("single", "repeat3")
EP_FAULTS = ("web_http_error", "agent_param_error", "web_dom_missing")
# 剂量分类以 episode 模块为单一来源（single 的 1 次即 full_dose）


def _cell_key(cell: dict, variant: str) -> tuple:
    return (cell["model_profile"], cell["architecture"], cell["fault_type"],
            int(cell["task_id"]), int(cell["fault_seed"]), variant)


def _pairs(rows: list[dict]) -> dict:
    """按 (model, arch, fault, task, seed, variant) 归拢 control/fault 两臂。"""
    buckets: dict = defaultdict(dict)
    for row in rows:
        variant = row.get("fault_variant") or (row.get("cell") or {}).get("fault_variant")
        if variant not in EP_VARIANTS:
            continue
        key = _cell_key(row["cell"], variant)
        buckets[key][row["cell"]["condition"]] = row
    return {key: arms for key, arms in buckets.items()
            if "control" in arms and "fault" in arms}


def _success(row: dict):
    value = row.get("official_success")
    return None if value is None else int(bool(value))


def ep_analysis(rows: list[dict], *, experiment_id: str = "ep-persistence-v1") -> dict:
    pairs = _pairs(rows)
    # Δ 与 D 的计算
    usable = []
    for key, arms in pairs.items():
        control, fault = _success(arms["control"]), _success(arms["fault"])
        if control is None or fault is None:
            continue
        usable.append((key, arms, control, fault))

    by_variant_fault: dict = defaultdict(list)
    for key, arms, control, fault in usable:
        _model, _arch, fault_type, _task, _seed, variant = key
        by_variant_fault[(variant, fault_type)].append(control - fault)

    # 任务内先按 (model, arch, seed) 平均，再跨任务等权
    per_task: dict = defaultdict(lambda: defaultdict(list))
    for key, arms, control, fault in usable:
        _model, _arch, fault_type, task, _seed, variant = key
        per_task[(variant, fault_type)][task].append(control - fault)

    result = {"experiment_id": experiment_id, "confirmatory_inference": False,
              "n_pairs_by_variant": {}, "by_fault": {}, "dose_distribution": {},
              "paired_step_delta": {}, "notes": [
                  "主口径为按分配的全部合法评分配对（含未触发/部分剂量）。",
                  "D_f = Δ_repeat3 − Δ_single；先在任务内平均，再跨任务等权。",
                  "4 任务版本不做确认性推断：不输出 p 值，也不把区间当泛化证据。",
                  "实际剂量子集只作机制描述，不是随机分配的因果效果。",
              ]}

    for (variant, fault_type), deltas in by_variant_fault.items():
        result["n_pairs_by_variant"].setdefault(variant, {})[fault_type] = len(deltas)
        task_values = per_task[(variant, fault_type)]
        per_task_mean = {task: statistics.mean(values) for task, values in task_values.items()}
        result["by_fault"].setdefault(fault_type, {})[f"delta_{variant}"] = {
            "per_task": per_task_mean,
            "task_equal_weighted": (statistics.mean(per_task_mean.values())
                                    if per_task_mean else None),
            "n_pairs": len(deltas),
        }

    for fault_type in {key[1] for key in by_variant_fault}:
        single = result["by_fault"].get(fault_type, {}).get("delta_single", {}).get("task_equal_weighted")
        repeat = result["by_fault"].get(fault_type, {}).get("delta_repeat3", {}).get("task_equal_weighted")
        result["by_fault"][fault_type]["D_f"] = (
            None if single is None or repeat is None else repeat - single)

    # 剂量分布（按 variant/fault 统计整臂 fault 的注入次数）
    for key, arms in pairs.items():
        _model, _arch, fault_type, _task, _seed, variant = key
        count = arms["fault"].get("injection_count")
        if count is None:
            continue
        try:
            klass = dose_class(variant, int(count))
        except (ValueError, TypeError):
            klass = "implementation_error"
        result["dose_distribution"].setdefault(variant, {}).setdefault(fault_type, Counter())[klass] += 1
    for variant, faults in result["dose_distribution"].items():
        for fault_type, counter in faults.items():
            faults[fault_type] = dict(counter)

    # 配对步数/调用数差（fault − control）
    for key, arms, control, fault in usable:
        _model, _arch, fault_type, _task, _seed, variant = key
        fault_row, control_row = arms["fault"], arms["control"]
        if fault_row.get("steps") is None or control_row.get("steps") is None:
            continue
        entry = result["paired_step_delta"].setdefault((variant, fault_type), {"steps": [], "llm_calls": []})
        entry["steps"].append(fault_row["steps"] - control_row["steps"])
        if fault_row.get("llm_calls") is not None and control_row.get("llm_calls") is not None:
            entry["llm_calls"].append(fault_row["llm_calls"] - control_row["llm_calls"])
    result["paired_step_delta"] = {
        f"{variant}:{fault_type}": {
            "step_delta_mean": statistics.mean(v["steps"]) if v["steps"] else None,
            "llm_delta_mean": statistics.mean(v["llm_calls"]) if v["llm_calls"] else None,
            "n": len(v["steps"]),
        }
        for (variant, fault_type), v in result["paired_step_delta"].items()
    }
    return result


def format_report(analysis: dict) -> str:
    lines = ["# E-P 持续性分析（描述性；不做确认性推断）", ""]
    for fault_type, entry in sorted(analysis["by_fault"].items()):
        single = entry.get("delta_single", {}).get("task_equal_weighted")
        repeat = entry.get("delta_repeat3", {}).get("task_equal_weighted")
        d_f = entry.get("D_f")
        fmt = lambda v: "—" if v is None else f"{v:+.3f}"
        lines.append(f"- **{fault_type}**: Δ_single={fmt(single)}，Δ_repeat3={fmt(repeat)}，"
                     f"D_f={fmt(d_f)}")
        for variant in EP_VARIANTS:
            per_task = entry.get(f"delta_{variant}", {}).get("per_task") or {}
            if per_task:
                lines.append(f"  - {variant} 逐任务 Δ: "
                             + ", ".join(f"task{t}={v:+.2f}" for t, v in sorted(per_task.items())))
    lines.append("")
    lines.append("> D_f 为差中差描述统计；4 任务版本不输出确认性 p 值，"
                 "实际剂量子集仅作机制描述。")
    return "\n".join(lines) + "\n"
