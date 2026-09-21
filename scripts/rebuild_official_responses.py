#!/usr/bin/env python3
"""Rebuild official responses from historical logs without rerunning the agent."""

import argparse
import csv
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASK_IDS = [21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 132, 133, 134, 135, 136]
ANSWER_RE = re.compile(r"任务完成！答案:\s*(.*)")


def tasks_by_id():
    from run_baseline import load_tasks
    return {task["task_id"]: task for task in load_tasks(task_ids=TASK_IDS)}


def result_paths(root):
    for response in sorted(root.rglob("agent_response.json")):
        har = response.with_name("network.har")
        if not har.exists():
            continue
        parts = response.relative_to(root).parts
        task_id = next((int(p) for p in parts if p.isdigit()), None)
        if task_id is None:
            continue
        trial_dir = next((p for p in parts if "_task" in p and "_seed" in p), None)
        if not trial_dir:
            continue
        match = re.search(r"(?P<fault>.+)_task(?P<task>\d+)_seed(?P<seed>\d+)$", trial_dir)
        if not match:
            continue
        condition = "control" if "control_seed" in parts[-2] else "fault"
        yield {
            "root": root,
            "response": response,
            "har": har,
            "fault_type": match.group("fault"),
            "task_id": int(match.group("task")),
            "seed": int(match.group("seed")),
            "condition": condition,
        }


def log_for(item):
    experiment_dir = item["root"].parent
    if experiment_dir.name.startswith("architecture_"):
        outer = next(part for part in item["response"].relative_to(item["root"]).parts if "_task" in part and "_seed" in part)
        return experiment_dir / f"{outer}.log"
    return experiment_dir / f"{item['fault_type']}_task{item['task_id']}_seed{item['seed']}.log"


def answer_for_condition(text, condition, architecture_log):
    if not architecture_log:
        if condition == "control":
            text = text.split("[control]", 1)[-1].split("[fault_high]", 1)[0]
        else:
            text = text.split("[fault_high]", 1)[-1]
    answers = ANSWER_RE.findall(text)
    return answers[-1] if answers else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    output = ROOT / args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    tasks = tasks_by_id()
    source_roots = [
        ROOT / "experiments/formal_gpt54_react_faults_official_subset_v1/outputs",
        ROOT / "experiments/formal_gpt54_react_faults_official_subset_v1_extend/outputs",
        ROOT / "experiments/formal_gpt54_react_faults_official_subset_v1_missing60_rerun/outputs",
        ROOT / "experiments/architecture_gpt54_react_planexecute_v1/outputs",
        ROOT / "experiments/architecture_gpt54_react_planexecute_core240_v1/outputs",
    ]
    audit = []
    rebuilt = 0
    needs_rerun = 0
    for source in source_roots:
        if not source.exists():
            continue
        for item in result_paths(source):
            source_log = log_for(item)
            text = source_log.read_text(encoding="utf-8", errors="replace") if source_log.exists() else ""
            architecture_log = source.name.startswith("architecture_")
            answer = answer_for_condition(text, item["condition"], architecture_log)
            source_label = source.parent.name
            relative_key = f"{source_label}/{item['fault_type']}_task{item['task_id']}_seed{item['seed']}/{item['task_id']}/{item['condition']}_seed_{item['seed']}"
            destination = output / relative_key
            destination.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item["har"], destination / "network.har")
            if not answer:
                needs_rerun += 1
                audit.append({"source": str(item["root"].relative_to(ROOT)), "fault_type": item["fault_type"], "task_id": item["task_id"], "seed": item["seed"], "condition": item["condition"], "status": "needs_agent_rerun", "reason": "no final answer in log"})
                continue
            from standard_agent.webarena_verified import make_agent_response
            response = make_agent_response(tasks[item["task_id"]], completed=True, answer=answer)
            (destination / "agent_response.json").write_text(json.dumps(response, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            rebuilt += 1
            audit.append({"source": str(item["root"].relative_to(ROOT)), "fault_type": item["fault_type"], "task_id": item["task_id"], "seed": item["seed"], "condition": item["condition"], "status": "rebuildable", "reason": "final answer recovered from log"})
    with (output / "rebuild_audit.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(audit[0]) if audit else ["status"])
        writer.writeheader(); writer.writerows(audit)
    (output / "manifest.json").write_text(json.dumps({"source_roots": [str(p.relative_to(ROOT)) for p in source_roots], "rebuilt": rebuilt, "needs_agent_rerun": needs_rerun, "note": "HAR copied unchanged; responses rebuilt using the task contract loaded by task_id."}, indent=2) + "\n")
    print(json.dumps({"rebuilt": rebuilt, "needs_agent_rerun": needs_rerun}, ensure_ascii=False))


if __name__ == "__main__":
    main()
