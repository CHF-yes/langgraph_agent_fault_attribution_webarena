#!/usr/bin/env python3
"""Merge, evaluate, and compare the five completed fault matrices."""

import argparse
import csv
import hashlib
import json
import re
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from run_baseline import load_tasks
from standard_agent.webarena_evaluator import evaluate_task_safe
from standard_agent.webarena_verified import make_agent_response

MATRICES = {
    "web_dom_missing": "experiments/formal_20260824_gpt54_react_web_dom_missing_deterministic",
    "web_popup_block": "experiments/formal_gpt54_react_web_popup_block_6workers_20260825",
    "web_http_error": "experiments/formal_gpt54_react_web_http_error_6workers_20260825",
    "web_timeout": "experiments/formal_gpt54_react_web_timeout_6workers_20260825",
    "agent_param_error": "experiments/formal_gpt54_react_agent_param_error_6workers_20260825_foreground",
}

SUMMARY_RE = re.compile(
    r"control \(control\): (?P<cpass>\d+)/(?P<ctotal>\d+) successful,?"
    r".*?avg (?P<csteps>[0-9.]+) steps, avg (?P<ccalls>[0-9.]+) LLM calls, avg (?P<ctime>[0-9.]+)s"
)
SUMMARY_CN_RE = re.compile(
    r"control \(control\): (?P<cpass>\d+)/(?P<ctotal>\d+) 成功, avg (?P<csteps>[0-9.]+) 步,"
    r" avg (?P<ccalls>[0-9.]+) LLM calls, avg (?P<ctime>[0-9.]+)s"
)
FAULT_RE = re.compile(
    r"fault_high: (?P<fpass>\d+)/(?P<ftotal>\d+) (?:successful|成功),?"
    r".*?avg (?P<fsteps>[0-9.]+) (?:steps|步), avg (?P<fcalls>[0-9.]+) LLM calls, avg (?P<ftime>[0-9.]+)s"
)
INJECTION_RE = re.compile(r"injection_count=(\d+) fault_seed=(\d+) faults=\[(.*?)\] steps=\[(.*?)\]")
ANSWER_RE = re.compile(r"任务完成！答案: (.*)")


def args():
    p = argparse.ArgumentParser()
    p.add_argument("--output-dir", required=True)
    p.add_argument("--evaluator-config", default="experiments/webarena_local_config.json")
    p.add_argument("--skip-evaluator", action="store_true")
    return p.parse_args()


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def parse_summary(text):
    control = SUMMARY_RE.search(text) or SUMMARY_CN_RE.search(text)
    fault = FAULT_RE.search(text)
    if not control or not fault:
        return {}
    values = {}
    for prefix, match in (("control", control), ("fault", fault)):
        values.update({
            f"{prefix}_successes": int(match.group(f"{prefix[0]}pass")),
            f"{prefix}_trials": int(match.group(f"{prefix[0]}total")),
            f"{prefix}_steps": float(match.group(f"{prefix[0]}steps")),
            f"{prefix}_llm_calls": float(match.group(f"{prefix[0]}calls")),
            f"{prefix}_time_sec": float(match.group(f"{prefix[0]}time")),
        })
    return values


def parse_log(path, task, fault_type, condition):
    full_text = path.read_text(encoding="utf-8", errors="replace")
    text = full_text
    injections = []
    for match in INJECTION_RE.finditer(text):
        if int(match.group(2)) != 0:
            injections.append({
                "count": int(match.group(1)),
                "fault_seed": int(match.group(2)),
                "faults": match.group(3),
                "steps": match.group(4),
            })
    answers = ANSWER_RE.findall(text)
    summary = parse_summary(full_text)
    prefix = "control" if condition == "control" else "fault"
    return {
        "source_log": str(path.relative_to(ROOT)),
        "task_id": task["task_id"],
        "fault_type": fault_type,
        "condition": condition,
        "intent": task["intent"],
        "answer": answers[-1] if answers else "",
        "injection_count": injections[-1]["count"] if injections else 0,
        "injection": injections[-1] if injections else None,
        "completed": bool(answers),
        "steps": summary.get(f"{prefix}_steps"),
        "llm_calls": summary.get(f"{prefix}_llm_calls"),
        "time_sec": summary.get(f"{prefix}_time_sec"),
        "summary": summary,
    }


def evaluate_record(record, task, output_dir, config_path):
    response_dir = output_dir / "responses" / record["fault_type"] / str(record["task_id"]) / record["condition"] / str(record["run_seed"])
    response_dir.mkdir(parents=True, exist_ok=True)
    response_path = response_dir / "agent_response.json"
    response_path.write_text(
        json.dumps(make_agent_response(task, completed=record["completed"], answer=record["answer"]), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    # The matrix runner stores logs/status only and does not retain HAR files.
    # Do not present a response-only reconstruction as an official evaluation.
    record.update({
        "official_success": None,
        "official_score": None,
        "evaluator_status": "NOT_EVALUATED_NO_HAR",
        "evaluator_error": "network_trace/HAR not retained by matrix runner",
        "fallback_used": False,
        "response_path": str(response_path.relative_to(ROOT)),
    })
    return
    result = evaluate_task_safe(  # pragma: no cover
        task["task_id"], agent_response_path=response_path, config_path=config_path
    )
    record.update({
        "official_success": result.get("official_success"),
        "official_score": result.get("official_score", result.get("score")),
        "evaluator_status": result.get("status"),
        "evaluator_error": result.get("error_msg"),
        "fallback_used": any(
            item.get("evaluator_name") == "AgentResponseEvaluatorCompat"
            for item in result.get("evaluators_results", [])
        ),
        "response_path": str(response_path.relative_to(ROOT)),
    })


def write_csv(path, rows, fields):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main():
    options = args()
    output = ROOT / options.output_dir
    output.mkdir(parents=True, exist_ok=True)
    tasks = {task["task_id"]: task for task in load_tasks(task_ids=[21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 132, 133, 134, 135, 136])}
    records = []
    for fault_type, relative_dir in MATRICES.items():
        matrix_dir = ROOT / relative_dir
        for status_path in sorted(matrix_dir.glob("*.status.json")):
            status = json.loads(status_path.read_text(encoding="utf-8"))
            job = status["job"]
            task = tasks[job["task_id"]]
            base = status_path.with_suffix("").with_suffix(".log")
            condition_records = []
            text = base.read_text(encoding="utf-8", errors="replace") if base.exists() else ""
            for condition in ("control", "fault"):
                condition_record = parse_log(base, task, fault_type, condition)
                condition_record.update({
                    "run_seed": job["seed"],
                    "trial_id": job["seed"],
                    "model_profile": "gpt54",
                    "model_name": "gpt-5.4",
                    "architecture": "react",
                    "temperature": 0.2,
                    "max_steps": 20,
                    "fault_layer": "control" if condition == "control" else (
                        "action" if fault_type == "agent_param_error" else "environment" if fault_type in {"web_timeout", "web_http_error"} else "observation"
                    ),
                    "status_path": str(status_path.relative_to(ROOT)),
                    "task_error": status.get("task_error", False) if condition == "fault" else False,
                    "infrastructure_error": status.get("infrastructure_error", False) if condition == "fault" else False,
                })
                if condition == "control":
                    condition_record["injection_count"] = 0
                if not options.skip_evaluator:
                    evaluate_record(condition_record, task, output, options.evaluator_config)
                records.append(condition_record)

    records.sort(key=lambda r: (r["fault_type"], r["task_id"], r["run_seed"], r["condition"]))
    (output / "trials.json").write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    fields = ["fault_type", "task_id", "run_seed", "trial_id", "condition", "model_profile", "model_name", "architecture", "temperature", "max_steps", "fault_layer", "completed", "task_error", "infrastructure_error", "injection_count", "steps", "llm_calls", "time_sec", "answer", "official_success", "official_score", "evaluator_status", "evaluator_error", "fallback_used", "source_log", "status_path", "response_path"]
    write_csv(output / "trials.csv", records, fields)
    pairs = []
    for fault_type in MATRICES:
        for task_id in sorted(tasks):
            for seed in range(1, 6):
                pair = [r for r in records if r["fault_type"] == fault_type and r["task_id"] == task_id and r["run_seed"] == seed]
                control = next((r for r in pair if r["condition"] == "control"), None)
                fault = next((r for r in pair if r["condition"] == "fault"), None)
                if not control or not fault:
                    continue
                pairs.append({
                    "fault_type": fault_type,
                    "task_id": task_id,
                    "seed": seed,
                    "control_success": control.get("official_success"),
                    "fault_success": fault.get("official_success"),
                    "success_delta": (int(bool(fault.get("official_success"))) - int(bool(control.get("official_success")))) if control.get("official_success") is not None and fault.get("official_success") is not None else None,
                    "control_completed": control["completed"],
                    "fault_completed": fault["completed"],
                    "control_steps": control["steps"],
                    "fault_steps": fault["steps"],
                    "step_delta": (fault["steps"] - control["steps"]) if fault["steps"] is not None and control["steps"] is not None else None,
                    "control_time_sec": control["time_sec"],
                    "fault_time_sec": fault["time_sec"],
                    "time_delta": (fault["time_sec"] - control["time_sec"]) if fault["time_sec"] is not None and control["time_sec"] is not None else None,
                    "fault_injection_count": fault["injection_count"],
                })
    write_csv(output / "paired_sensitivity.csv", pairs, list(pairs[0]) if pairs else ["fault_type", "task_id", "seed"])
    summary_rows = []
    for fault_type in MATRICES:
        for condition in ("control", "fault"):
            group = [r for r in records if r["fault_type"] == fault_type and r["condition"] == condition]
            numeric_steps = [r["steps"] for r in group if r["steps"] is not None]
            numeric_time = [r["time_sec"] for r in group if r["time_sec"] is not None]
            summary_rows.append({
                "fault_type": fault_type,
                "condition": condition,
                "trials": len(group),
                "completed": sum(bool(r["completed"]) for r in group),
                "task_errors": sum(bool(r["task_error"]) for r in group),
                "infrastructure_errors": sum(bool(r["infrastructure_error"]) for r in group),
                "valid_fault_injections": sum(r["injection_count"] == 1 for r in group) if condition == "fault" else 0,
                "mean_steps": statistics.mean(numeric_steps) if numeric_steps else None,
                "median_steps": statistics.median(numeric_steps) if numeric_steps else None,
                "mean_llm_calls": statistics.mean([r["llm_calls"] for r in group if r["llm_calls"] is not None]) if any(r["llm_calls"] is not None for r in group) else None,
                "mean_time_sec": statistics.mean(numeric_time) if numeric_time else None,
                "evaluator_status": "NOT_EVALUATED_NO_HAR",
            })
    write_csv(output / "summary.csv", summary_rows, list(summary_rows[0]))
    manifest = {
        "dataset": "gpt54_react_public16_fault_matrices_v1",
        "matrix_count": len(MATRICES),
        "fault_types": list(MATRICES),
        "tasks": sorted(tasks),
        "seeds": [1, 2, 3, 4, 5],
        "conditions": ["control", "fault"],
        "expected_trials": 800,
        "actual_trial_records": len(records),
        "source_matrices": MATRICES,
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "baseline_path": "experiments/curated/gpt54_react_public16_control_v1.json",
        "dataset_sha256": sha256(ROOT / "experiments/curated/gpt54_react_public16_control_v1.json"),
        "evaluator": "NOT_RUN: matrix logs do not include HAR/network_trace",
        "limitations": [
            "Fault logs do not contain original HAR files; responses are reconstructed from final answers.",
            "official_success is null because the official evaluator requires network_trace/HAR, which these matrices did not retain.",
            "This dataset is for sensitivity analysis; it is not yet a model or architecture responsibility experiment."
        ],
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"records": len(records), "pairs": len(pairs), "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
