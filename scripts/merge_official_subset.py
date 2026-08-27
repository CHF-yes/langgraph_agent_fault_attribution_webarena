#!/usr/bin/env python3
"""Merge the 90, 250, and 60 official subset outputs into 400 unique trials."""

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAULTS = ["web_dom_missing", "web_popup_block", "web_http_error", "web_timeout", "agent_param_error"]
TASKS = [22, 24, 27, 28, 30, 132, 133, 134]


def records_from_root(root):
    records = []
    for evaluation in root.rglob("official_eval.json"):
        response = evaluation.with_name("agent_response.json")
        har = evaluation.with_name("network.har")
        parts = evaluation.relative_to(root).parts
        outer, task_part, condition_part = parts[-4:-1]
        match = re.match(r"(?P<fault>.+)_task(?P<task>\d+)_seed(?P<seed>\d+)$", outer)
        if not match:
            raise RuntimeError(f"unexpected output directory: {outer}")
        fault = match.group("fault")
        seed_text = match.group("seed")
        task_id = int(task_part)
        condition = "control" if condition_part.startswith("control_") else "fault"
        result = json.loads(evaluation.read_text(encoding="utf-8"))
        records.append({
            "fault_type": fault,
            "task_id": task_id,
            "seed": int(seed_text),
            "condition": condition,
            "official_success": result.get("official_success"),
            "evaluator_status": result.get("status"),
            "evaluator_error": result.get("error_msg"),
            "agent_response": str(response.relative_to(ROOT)),
            "network_har": str(har.relative_to(ROOT)),
            "official_eval": str(evaluation.relative_to(ROOT)),
            "source": str(root.relative_to(ROOT)),
        })
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    roots = [
        ROOT / "experiments/formal_gpt54_react_faults_official_subset_v1/outputs",
        ROOT / "experiments/formal_gpt54_react_faults_official_subset_v1_extend/outputs",
        ROOT / "experiments/formal_gpt54_react_faults_official_subset_v1_missing60_rerun/outputs",
    ]
    merged = {}
    for root in roots:
        for record in records_from_root(root):
            key = (record["fault_type"], record["task_id"], record["seed"], record["condition"])
            if key in merged:
                raise RuntimeError(f"duplicate official trial: {key}")
            merged[key] = record

    expected = {(fault, task, seed, condition) for fault in FAULTS for task in TASKS for seed in range(1, 6) for condition in ("control", "fault")}
    missing = expected - set(merged)
    unexpected = set(merged) - expected
    if missing or unexpected:
        raise RuntimeError(f"missing={sorted(missing)} unexpected={sorted(unexpected)}")
    rows = [merged[key] for key in sorted(merged)]
    out = ROOT / args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    (out / "official_trials.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    fields = list(rows[0])
    with (out / "official_trials.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    summary = []
    for fault in FAULTS:
        for condition in ("control", "fault"):
            group = [r for r in rows if r["fault_type"] == fault and r["condition"] == condition]
            summary.append({"fault_type": fault, "condition": condition, "trials": len(group), "official_successes": sum(bool(r["official_success"]) for r in group), "official_success_rate": sum(bool(r["official_success"]) for r in group) / len(group), "evaluator_errors": sum(r["evaluator_status"] == "ERROR" for r in group)})
    with (out / "official_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary[0]), lineterminator="\n")
        writer.writeheader(); writer.writerows(summary)
    manifest = {"trials": len(rows), "tasks": TASKS, "faults": FAULTS, "seeds": [1,2,3,4,5], "conditions": ["control","fault"], "evaluator_errors": sum(r["evaluator_status"] == "ERROR" for r in rows)}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"trials": len(rows), "successes": sum(bool(r["official_success"]) for r in rows), "errors": manifest["evaluator_errors"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
