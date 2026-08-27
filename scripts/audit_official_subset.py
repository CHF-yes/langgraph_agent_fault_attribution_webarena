#!/usr/bin/env python3
"""Freeze and audit the official 400-trial fault subset before reporting it."""

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAULTS = ["web_dom_missing", "web_popup_block", "web_http_error", "web_timeout", "agent_param_error"]
TASKS = [22, 24, 27, 28, 30, 132, 133, 134]
SEEDS = [1, 2, 3, 4, 5]
CONDITIONS = ["control", "fault"]
OUT = ROOT / "experiments/analysis/official_subset_v1_final"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evaluation_source(evaluation: dict) -> str:
    """Separate native evaluator output from the adapter's explicit fallback."""
    results = evaluation.get("evaluators_results") or []
    if any(result.get("assertion_name") == "null_retrieval_schema_compatibility" for result in results):
        return "fallback"
    return "native"


def expected_keys():
    return {
        (fault_type, task_id, seed, condition)
        for fault_type in FAULTS
        for task_id in TASKS
        for seed in SEEDS
        for condition in CONDITIONS
    }


def status_path(row: dict) -> Path:
    source = ROOT / row["source"]
    return source.parent / f"{row['fault_type']}_task{row['task_id']}_seed{row['seed']}.status.json"


def audit_row(row: dict) -> dict:
    key = (row["fault_type"], row["task_id"], row["seed"], row["condition"])
    checks = []
    errors = []
    paths = {name: ROOT / row[name] for name in ("agent_response", "network_har", "official_eval")}
    for name, path in paths.items():
        present = path.is_file() and path.stat().st_size > 0
        checks.append((f"{name}_present", present))
        if not present:
            errors.append(f"missing_or_empty_{name}")

    evaluation = {}
    if paths["official_eval"].is_file():
        try:
            evaluation = json.loads(paths["official_eval"].read_text(encoding="utf-8"))
            checks.extend([
                ("evaluation_status_matches", evaluation.get("status") == row["evaluator_status"]),
                ("evaluation_success_matches", evaluation.get("official_success") == row["official_success"]),
            ])
            if evaluation.get("status") != row["evaluator_status"]:
                errors.append("evaluator_status_mismatch")
            if evaluation.get("official_success") != row["official_success"]:
                errors.append("official_success_mismatch")
        except (json.JSONDecodeError, OSError):
            errors.append("invalid_official_eval")

    path_text = "/".join(str(path) for path in paths.values())
    expected_outer = f"{row['fault_type']}_task{row['task_id']}_seed{row['seed']}"
    expected_condition = "control_seed_0" if row["condition"] == "control" else f"{row['fault_type']}_seed_{row['seed']}"
    names_match = expected_outer in path_text and f"/{row['task_id']}/{expected_condition}/" in path_text
    checks.append(("path_identity_matches", names_match))
    if not names_match:
        errors.append("path_identity_mismatch")

    injection_count = 0
    fault_triggered = None
    if row["condition"] == "fault":
        path = status_path(row)
        try:
            status = json.loads(path.read_text(encoding="utf-8"))
            job = status.get("job", {})
            injection_count = status.get("fault_count", 0)
            fault_triggered = status.get("fault_triggered")
            job_matches = (
                job.get("task_id") == row["task_id"]
                and job.get("seed") == row["seed"]
                and job.get("fault_type") == row["fault_type"]
            )
            injected_once = injection_count == 1 and fault_triggered is True and not status.get("fault_missing", False)
            checks.extend([("status_identity_matches", job_matches), ("fault_injected_once", injected_once)])
            if not job_matches:
                errors.append("status_identity_mismatch")
            if not injected_once:
                errors.append("fault_not_injected_once")
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            errors.append("missing_or_invalid_fault_status")
    else:
        checks.append(("fault_injected_once", True))

    return {
        "fault_type": key[0],
        "task_id": key[1],
        "seed": key[2],
        "condition": key[3],
        "official_success": row["official_success"],
        "evaluator_status": row["evaluator_status"],
        "evaluator_error": row.get("evaluator_error"),
        "evaluation_source": evaluation_source(evaluation),
        "fault_injection_count": injection_count if row["condition"] == "fault" else 0,
        "fault_triggered": fault_triggered,
        "audit_passed": not errors and all(passed for _, passed in checks),
        "audit_errors": ";".join(errors),
        "agent_response": row["agent_response"],
        "network_har": row["network_har"],
        "official_eval": row["official_eval"],
        "source": row["source"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=OUT / "official_trials.json")
    parser.add_argument("--output-dir", type=Path, default=OUT)
    args = parser.parse_args()

    rows = json.loads(args.input.read_text(encoding="utf-8"))
    keys = [(row["fault_type"], row["task_id"], row["seed"], row["condition"]) for row in rows]
    duplicates = sorted(key for key, count in Counter(keys).items() if count > 1)
    missing = sorted(expected_keys() - set(keys))
    unexpected = sorted(set(keys) - expected_keys())
    audited = [audit_row(row) for row in rows]
    failures = [row for row in audited if not row["audit_passed"]]
    if duplicates or missing or unexpected or failures:
        raise RuntimeError(json.dumps({
            "duplicates": duplicates,
            "missing": missing,
            "unexpected": unexpected,
            "failed_audits": [
                [row["fault_type"], row["task_id"], row["seed"], row["condition"], row["audit_errors"]]
                for row in failures
            ],
        }, ensure_ascii=False))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    fields = list(audited[0])
    with (args.output_dir / "official_trial_audit.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(audited)

    source_counts = Counter(row["evaluation_source"] for row in audited)
    manifest = {
        "dataset": "gpt54_react_official_subset_v1_final",
        "role": "primary_official_result_dataset",
        "input": str(args.input.relative_to(ROOT)),
        "input_sha256": sha256(args.input),
        "trials": len(audited),
        "tasks": TASKS,
        "faults": FAULTS,
        "seeds": SEEDS,
        "conditions": CONDITIONS,
        "required_fields": ["official_success", "evaluator_status", "evaluator_error", "evaluation_source"],
        "evaluation_source_counts": dict(sorted(source_counts.items())),
        "evaluator_error_count": sum(row["evaluator_status"].lower() == "error" for row in audited),
        "fault_trials_with_exactly_one_injection": sum(
            row["condition"] == "fault" and row["fault_injection_count"] == 1 and row["fault_triggered"] is True
            for row in audited
        ),
        "audit": {
            "expected_trials": len(expected_keys()),
            "duplicate_trials": 0,
            "missing_trials": 0,
            "unexpected_trials": 0,
            "failed_trial_audits": 0,
            "trial_audit": "experiments/analysis/official_subset_v1_final/official_trial_audit.csv",
        },
        "scope_exclusion": "Do not combine this official dataset with fault_matrices_v1 in official success-rate statistics because the matrix has no HAR.",
    }
    (args.output_dir / "primary_dataset_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"trials": len(audited), "audit": "passed", "evaluation_sources": source_counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
