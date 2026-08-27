#!/usr/bin/env python3
"""Build a read-only index over baseline, behavioral, and official datasets."""

import csv
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments/analysis/project_snapshot_v1"

BEHAVIOR_DIRS = {
    "web_dom_missing": "experiments/formal_20260824_gpt54_react_web_dom_missing_deterministic",
    "web_popup_block": "experiments/formal_gpt54_react_web_popup_block_6workers_20260825",
    "web_http_error": "experiments/formal_gpt54_react_web_http_error_6workers_20260825",
    "web_timeout": "experiments/formal_gpt54_react_web_timeout_6workers_20260825",
    "agent_param_error": "experiments/formal_gpt54_react_agent_param_error_6workers_20260825_foreground",
}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    baseline_path = ROOT / "experiments/curated/gpt54_react_public16_control_v1.json"
    baseline = json.loads(baseline_path.read_text())
    records = []
    for row in baseline:
        records.append({
            "dataset_layer": "baseline_official",
            "fault_type": "control",
            "condition": "control",
            "task_id": row.get("task_id"),
            "seed": row.get("run_seed"),
            "trial_id": row.get("trial_id"),
            "official_success": row.get("official_success"),
            "official_score": row.get("official_score"),
            "evaluator_status": row.get("evaluator_status"),
            "completed": row.get("completed"),
            "steps": row.get("steps"),
            "llm_calls": row.get("llm_calls"),
            "time_sec": row.get("time_sec"),
            "source": "experiments/curated/gpt54_react_public16_control_v1.json",
        })
    for fault_type, relative in BEHAVIOR_DIRS.items():
        directory = ROOT / relative
        for status_path in sorted(directory.glob("*.status.json")):
            status = json.loads(status_path.read_text())
            job = status["job"]
            records.extend([
                {
                    "dataset_layer": "fault_behavioral",
                    "fault_type": fault_type,
                    "condition": "control",
                    "task_id": job["task_id"],
                    "seed": job["seed"],
                    "trial_id": job["seed"],
                    "official_success": None,
                    "official_score": None,
                    "evaluator_status": "NOT_EVALUATED_NO_HAR",
                    "completed": None,
                    "steps": None,
                    "llm_calls": None,
                    "time_sec": None,
                    "source": str(status_path.relative_to(ROOT)),
                },
                {
                    "dataset_layer": "fault_behavioral",
                    "fault_type": fault_type,
                    "condition": "fault",
                    "task_id": job["task_id"],
                    "seed": job["seed"],
                    "trial_id": job["seed"],
                    "official_success": None,
                    "official_score": None,
                    "evaluator_status": "NOT_EVALUATED_NO_HAR",
                    "completed": None,
                    "steps": None,
                    "llm_calls": None,
                    "time_sec": None,
                    "source": str(status_path.relative_to(ROOT)),
                },
            ])
    official = json.loads((ROOT / "experiments/analysis/official_subset_v1_final/official_trials.json").read_text())
    for row in official:
        records.append({
            "dataset_layer": "fault_official_subset",
            "fault_type": row["fault_type"],
            "condition": row["condition"],
            "task_id": row["task_id"],
            "seed": row["seed"],
            "trial_id": row["seed"],
            "official_success": row["official_success"],
            "official_score": None,
            "evaluator_status": row["evaluator_status"],
            "completed": None,
            "steps": None,
            "llm_calls": None,
            "time_sec": None,
            "source": row["official_eval"],
        })
    fields = list(records[0])
    with (OUT / "experiment_index.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader(); writer.writerows(records)
    summary = {
        "baseline_official_records": sum(r["dataset_layer"] == "baseline_official" for r in records),
        "fault_behavioral_records": sum(r["dataset_layer"] == "fault_behavioral" for r in records),
        "fault_official_subset_records": sum(r["dataset_layer"] == "fault_official_subset" for r in records),
        "total_index_records": len(records),
        "official_subset_successes": sum(bool(r["official_success"]) for r in records if r["dataset_layer"] == "fault_official_subset"),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    manifest = {
        "snapshot": "project_experiment_snapshot_v1",
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "baseline": {"path": "experiments/curated/gpt54_react_public16_control_v1.json", "sha256": sha256(baseline_path), "records": 48},
        "behavioral_matrices": BEHAVIOR_DIRS,
        "official_subset": "experiments/analysis/official_subset_v1_final/official_trials.json",
        "index": "experiments/analysis/project_snapshot_v1/experiment_index.csv",
        "notes": [
            "Behavioral matrix records are indexed as NOT_EVALUATED_NO_HAR; use them for completion/efficiency/behavior analysis, not official success.",
            "Official subset contains 400 unique fault/task/seed/condition records with HAR and evaluator outputs.",
            "Baseline remains a separate frozen 16-task, 3-seed dataset."
        ]
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({**summary, "output": str(OUT)}, indent=2))


if __name__ == "__main__":
    main()
