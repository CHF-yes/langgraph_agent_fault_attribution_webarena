#!/usr/bin/env python3
"""Audit and rebuild the historical DeepSeek Plan-and-Execute subset."""

import csv
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "experiments/architecture_deepseek_react_planexecute_subset36_v1"
OUT = ROOT / "experiments/analysis/deepseek_plan_execute_audit_v1"
TASK_IDS = [27, 30, 132]
ANSWER_RE = re.compile(r"任务完成！答案:\s*(.*)")
LOG_RE = re.compile(r"plan_execute_(control|fault)_(control|web_dom_missing|web_http_error)_task(\d+)_seed(\d+)\.log$")


def main():
    from run_baseline import load_tasks
    from standard_agent.webarena_verified import make_agent_response

    tasks = {task["task_id"]: task for task in load_tasks(task_ids=TASK_IDS)}
    OUT.mkdir(parents=True, exist_ok=True)
    audit = []
    rebuilt = 0
    missing = 0
    for log in sorted(SOURCE.glob("plan_execute_*.log")):
        match = LOG_RE.match(log.name)
        if not match:
            continue
        condition, fault, task_text, seed_text = match.groups()
        task_id, seed = int(task_text), int(seed_text)
        fault_type = "control" if condition == "control" else fault
        key = f"plan_execute_{condition}_{fault}_task{task_id}_seed{seed}"
        output_source = SOURCE / "outputs" / key / str(task_id)
        source_har = next(output_source.rglob("network.har"), None) if output_source.exists() else None
        text = log.read_text(encoding="utf-8", errors="replace")
        answers = ANSWER_RE.findall(text)
        destination = OUT / key / str(task_id)
        if source_har is None or not answers:
            missing += 1
            audit.append({"key": key, "architecture": "plan_execute", "task_id": task_id, "seed": seed, "condition": condition, "fault_type": fault_type, "status": "needs_agent_rerun", "reason": "missing HAR" if source_har is None else "missing final answer"})
            continue
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_har, destination / "network.har")
        response = make_agent_response(tasks[task_id], completed=True, answer=answers[-1])
        (destination / "agent_response.json").write_text(json.dumps(response, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        audit.append({"key": key, "architecture": "plan_execute", "task_id": task_id, "seed": seed, "condition": condition, "fault_type": fault_type, "status": "rebuildable", "reason": "HAR and final answer recovered"})
        rebuilt += 1
    fields = list(audit[0]) if audit else ["status"]
    with (OUT / "audit.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(audit)
    (OUT / "manifest.json").write_text(json.dumps({"source": str(SOURCE.relative_to(ROOT)), "architecture": "plan_execute", "candidate_logs": len(audit), "rebuilt": rebuilt, "needs_agent_rerun": missing, "note": "Rebuilt responses use the real task contract loaded by task_id; source files are untouched."}, indent=2) + "\n")
    print(json.dumps({"candidate_logs": len(audit), "rebuilt": rebuilt, "needs_agent_rerun": missing}, ensure_ascii=False))


if __name__ == "__main__":
    main()
