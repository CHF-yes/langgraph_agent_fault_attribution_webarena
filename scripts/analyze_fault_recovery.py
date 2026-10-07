"""Post-fault recovery behaviour from the existing traces.

Question: the three injected faults barely move the overall success rate.  Is that
because the fault is rarely visible to the agent, because the agent retries and
recovers immediately, or because the affected step is not on the critical path?

Everything here reads existing artefacts (status injection_step, trace step
events, trial records); no new trials.  A fault arm is "observed" when the trace
shows the fault's signature at (or after) its injection step:

  500 Internal Server Error / about:error   web_http_error's substituted page
  invalid_<n> not found in current page     agent_param_error's rewritten id
  not found in current page                 web_dom_missing leaving a stale id

Writes experiments/stage_c_audit/recovery.json and recovery.md.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
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


def fault_observed(fault: str, observation: str) -> bool:
    text = (observation or "").lower()
    return any(marker in text for marker in SIGNATURES[fault])


def step_events(trace_path: Path):
    events = []
    try:
        lines = trace_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return events
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("event") is None and (event.get("action") or event.get("observation") is not None):
            events.append(event)
    return sorted(events, key=lambda e: (e.get("step") or 0))


def element_ids(observation: str) -> set:
    """AX 文本里的元素编号集合，形如 ``[12] link '...'``。"""
    import re
    return {int(m) for m in re.findall(r"\[(\d+)\]", observation or "")}


def dom_removal_step(fault_events, control_events):
    """web_dom_missing 的检测：与匹配控制臂逐步比对，首个"故障臂元素集合
    真子集"的步即删元素发生处。文本签名（not found）只覆盖极少数，因为观察
    每步重建，删掉的元素通常不再被引用，不会报错。"""
    control_by_step = {e.get("step"): e for e in control_events}
    for event in fault_events:
        step = event.get("step")
        other = control_by_step.get(step)
        if other is None:
            continue
        fault_ids = element_ids(event.get("observation") or "")
        control_ids = element_ids(other.get("observation") or "")
        if fault_ids and control_ids and fault_ids < control_ids:
            return step
    return None


def trial_success(path: Path):
    """Official evaluator verdict, not the agent's self-reported status.

    trial_record.success is the agent's submitted verdict (~0.82); the official
    success rate is ~0.39, and the recovery question is about the latter.
    """
    try:
        record = json.load(open(path))
    except Exception:
        return None
    return record.get("official_success")


def official_success_index():
    """(model, arch, fault, task, seed, condition) -> official_success from the
    committed pipeline rows."""
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
            injection_step = job.get("injection_step")
            trace_dir = ROOT / "traces" / root
            traces = list(trace_dir.glob(f"**/*{fault}__task{task}__seed{seed}__fault*"))
            if not traces:
                continue
            events = step_events(traces[0])
            by_step = {e.get("step"): e for e in events}
            # 锚定故障步：用观察里的故障签名首现位置，不依赖 status.injection_step。
            # 两者对 web_http_error 差 1（status 记 2，签名在 trace step 1），
            # 用签名更稳健；status 值仅记录以便对照。
            fault_step = None
            for event in events:
                if fault_observed(fault, json.dumps(event.get("observation") or "", ensure_ascii=False)):
                    fault_step = event.get("step")
                    break
            if fault == "web_dom_missing" and fault_step is None:
                controls = list(trace_dir.glob(f"**/*{fault}__task{task}__seed{seed}__control*"))
                if controls:
                    removal = dom_removal_step(events, step_events(controls[0]))
                    if removal is not None:
                        fault_step = removal
            observed = fault_step is not None
            faulted = by_step.get(fault_step) or {}
            after = [e for e in events if (e.get("step") or 0) > (fault_step or 0)]
            next_event = after[0] if after else None
            if fault == "web_http_error":
                # 该故障把页面替换成假 500 页（工具层 err=False），恢复应看下一观察
                # 是否已回到真实页面，而不是看 err。
                recovered = bool(next_event and not fault_observed(
                    fault, json.dumps(next_event.get("observation") or "", ensure_ascii=False)))
            else:
                recovered = bool(next_event and not next_event.get("error"))
            retried_same_tool = bool(next_event and next_event.get("action") == faulted.get("action"))
            records.append({
                "root": root, "model": model, "architecture": arch, "fault": fault,
                "task": task, "seed": seed,
                "status_injection_step": injection_step, "fault_step": fault_step,
                "observed": observed,
                "retried_same_tool": retried_same_tool,
                "recovered": recovered,
                "steps_after_fault": len(after),
                "error_at_fault": bool(faulted.get("error")),
                "reached_stop": any(e.get("action") == "stop" for e in events),
                "success": official.get((model, arch, fault, task, seed, "fault")),
                "control_success": official.get((model, arch, fault, task, seed, "control")),
            })

    summary = {}
    for fault in FAULTS:
        rows = [r for r in records if r["fault"] == fault and r["success"] is not None]
        observed = [r for r in rows if r["observed"]]
        summary[fault] = {
            "n": len(rows),
            "observed": len(observed),
            "observed_rate": round(len(observed) / len(rows), 4) if rows else None,
            "retry_same_tool_rate": round(sum(r["retried_same_tool"] for r in observed)
                                          / len(observed), 4) if observed else None,
            "recovered_rate": round(sum(r["recovered"] for r in observed)
                                          / len(observed), 4) if observed else None,
            "mean_steps_after_fault": round(sum(r["steps_after_fault"] for r in rows)
                                            / len(rows), 2) if rows else None,
            "success_observed": round(sum(r["success"] for r in observed) / len(observed), 4)
                                if observed else None,
            "success_not_observed": round(sum(r["success"] for r in rows if not r["observed"])
                                          / max(1, len(rows) - len(observed)), 4),
            "control_success_observed": round(sum(r["control_success"] for r in observed
                                                  if r["control_success"] is not None)
                                              / max(1, sum(1 for r in observed
                                                           if r["control_success"] is not None)), 4),
        }

    OUT.mkdir(parents=True, exist_ok=True)
    json.dump({"records": records, "by_fault": summary}, open(OUT / "recovery.json", "w"), indent=1)
    lines = ["# 故障后恢复行为（基于现有 trace，无新 trial）", "",
             "| 故障 | n | 观察到 | 立即重试 | 下一步恢复 | 故障后均步数 | 官方成功(观察) | 控制臂(观察) | 成功(未见) |",
             "|---|---|---|---|---|---|---|---|"]
    for fault in FAULTS:
        s = summary[fault]
        lines.append(f"| {fault} | {s['n']} | {s['observed']} ({s['observed_rate']}) | "
                     f"{s['retry_same_tool_rate']} | {s['recovered_rate']} | "
                     f"{s['mean_steps_after_fault']} | {s['success_observed']} | "
                     f"{s['control_success_observed']} | {s['success_not_observed']} |")
    open(OUT / "recovery.md", "w").write("\n".join(lines) + "\n")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
