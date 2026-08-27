#!/usr/bin/env python3
"""Export the 45-job official subset as explicit control/fault CSVs."""

import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "experiments/formal_gpt54_react_faults_official_subset_v1"
OUTPUT = ROOT / "experiments/analysis/fault_subset_45"

INJECTION_RE = re.compile(r"injection_count=(\d+) fault_seed=(\d+) faults=\[(.*?)\] steps=\[(.*?)\]")
METRIC_RE = re.compile(
    r"(?P<label>control \(control\)|fault_high): (?P<success>\d+)/(?P<total>\d+)"
    r" 成功, avg (?P<steps>[0-9.]+) 步, avg (?P<calls>[0-9.]+) LLM calls, avg (?P<time>[0-9.]+)s"
)
ANSWER_RE = re.compile(r"任务完成！答案: (.*)")


def parse_log(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    metrics = {}
    for match in METRIC_RE.finditer(text):
        condition = "control" if match.group("label").startswith("control") else "fault"
        metrics[condition] = {
            "steps": float(match.group("steps")),
            "llm_calls": float(match.group("calls")),
            "time_sec": float(match.group("time")),
            "summary_success": int(match.group("success")) == 1,
        }
    injections = []
    for match in INJECTION_RE.finditer(text):
        if int(match.group(2)) != 0:
            injections.append({
                "count": int(match.group(1)),
                "seed": int(match.group(2)),
                "faults": match.group(3),
                "steps": match.group(4),
            })
    answers = ANSWER_RE.findall(text)
    return metrics, injections, answers


def eval_record(output_root, fault_type, task_id, seed, condition):
    suffix = "control_seed_0" if condition == "control" else f"{fault_type}_seed_{seed}"
    path = output_root / f"{fault_type}_task{task_id}_seed{seed}" / str(task_id) / suffix / "official_eval.json"
    if not path.exists():
        return None, "MISSING"
    result = json.loads(path.read_text(encoding="utf-8"))
    return result.get("official_success"), result.get("status")


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for status_path in sorted(SOURCE.glob("*.status.json")):
        status = json.loads(status_path.read_text(encoding="utf-8"))
        job = status["job"]
        log_path = status_path.with_suffix("").with_suffix(".log")
        metrics, injections, answers = parse_log(log_path)
        for condition in ("control", "fault"):
            metric = metrics.get(condition, {})
            official_success, evaluator_status = eval_record(
                SOURCE / "outputs", job["fault_type"], job["task_id"], job["seed"], condition
            )
            rows.append({
                "condition": condition,
                "fault_type": job["fault_type"],
                "task_id": job["task_id"],
                "seed": job["seed"],
                "trial_id": job["seed"],
                "site": job["site"],
                "model_profile": "gpt54",
                "model_name": "gpt-5.4",
                "architecture": "react",
                "temperature": 0.2,
                "max_steps": 20,
                "completed": metric.get("summary_success"),
                "task_error": status.get("task_error") if condition == "fault" else False,
                "infrastructure_error": status.get("infrastructure_error") if condition == "fault" else False,
                "fault_count": injections[-1]["count"] if condition == "fault" and injections else 0,
                "injection_step": injections[-1]["steps"] if condition == "fault" and injections else "",
                "steps": metric.get("steps"),
                "llm_calls": metric.get("llm_calls"),
                "time_sec": metric.get("time_sec"),
                "official_success": official_success,
                "evaluator_status": evaluator_status,
                "answer_present": bool(answers),
                "source_status": str(status_path.relative_to(ROOT)),
                "source_log": str(log_path.relative_to(ROOT)),
            })

    rows.sort(key=lambda row: (row["fault_type"], row["task_id"], row["seed"], row["condition"]))
    trial_fields = list(rows[0])
    with (OUTPUT / "fault_trial_results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=trial_fields)
        writer.writeheader(); writer.writerows(rows)

    summary = []
    for fault_type in sorted({row["fault_type"] for row in rows}):
        for condition in ("control", "fault"):
            group = [row for row in rows if row["fault_type"] == fault_type and row["condition"] == condition]
            summary.append({
                "fault_type": fault_type,
                "condition": condition,
                "trials": len(group),
                "completed": sum(bool(row["completed"]) for row in group),
                "task_errors": sum(bool(row["task_error"]) for row in group),
                "infrastructure_errors": sum(bool(row["infrastructure_error"]) for row in group),
                "valid_fault_injections": sum(row["fault_count"] == 1 for row in group) if condition == "fault" else 0,
                "official_successes": sum(bool(row["official_success"]) for row in group),
                "official_success_rate": sum(bool(row["official_success"]) for row in group) / len(group),
                "mean_steps": sum(row["steps"] or 0 for row in group) / len(group),
                "mean_llm_calls": sum(row["llm_calls"] or 0 for row in group) / len(group),
                "mean_time_sec": sum(row["time_sec"] or 0 for row in group) / len(group),
            })
    with (OUTPUT / "fault_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary[0]))
        writer.writeheader(); writer.writerows(summary)

    paired = []
    keys = sorted({(row["fault_type"], row["task_id"], row["seed"]) for row in rows})
    for fault_type, task_id, seed in keys:
        control = next(row for row in rows if row["fault_type"] == fault_type and row["task_id"] == task_id and row["seed"] == seed and row["condition"] == "control")
        fault = next(row for row in rows if row["fault_type"] == fault_type and row["task_id"] == task_id and row["seed"] == seed and row["condition"] == "fault")
        paired.append({
            "fault_type": fault_type,
            "task_id": task_id,
            "seed": seed,
            "control_official_success": control["official_success"],
            "fault_official_success": fault["official_success"],
            "official_success_delta": int(bool(fault["official_success"])) - int(bool(control["official_success"])),
            "control_completed": control["completed"],
            "fault_completed": fault["completed"],
            "control_steps": control["steps"],
            "fault_steps": fault["steps"],
            "step_delta": (fault["steps"] or 0) - (control["steps"] or 0),
            "control_time_sec": control["time_sec"],
            "fault_time_sec": fault["time_sec"],
            "time_delta": (fault["time_sec"] or 0) - (control["time_sec"] or 0),
            "fault_count": fault["fault_count"],
        })
    with (OUTPUT / "control_fault_paired.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(paired[0]))
        writer.writeheader(); writer.writerows(paired)
    print(json.dumps({"trial_rows": len(rows), "fault_rows": sum(row["condition"] == "fault" for row in rows), "control_rows": sum(row["condition"] == "control" for row in rows), "paired_rows": len(paired)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
