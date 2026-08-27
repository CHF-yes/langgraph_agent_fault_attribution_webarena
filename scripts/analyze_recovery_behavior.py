#!/usr/bin/env python3
"""Extract recovery behavior from the 45 official-subset fault logs."""

import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "experiments/formal_gpt54_react_faults_official_subset_v1"
OUTPUT = ROOT / "experiments/analysis/fault_subset_45"

ACTION_RE = re.compile(r"\[ReAct\]\[step=(\d+)\] ACTION ([a-zA-Z_]+) args=(.*)")
OBS_RE = re.compile(r"\[ReAct\]\[step=(\d+)\] OBSERVATION (.*)")
INJECTION_RE = re.compile(
    r"injection=\{'fault': '([^']+)', 'fault_layer': '([^']+)', "
    r"'fault_seed': (\d+), 'step': (\d+),.*?"
    r"'injection_index': (\d+),.*?\}")
INJECTION_COUNT_RE = re.compile(
    r"injection_count=(\d+) fault_seed=(\d+) faults=\[(.*?)\] steps=\[(.*?)\]"
)


def parse_actions(text, start_line=0):
    actions = []
    observations = []
    for line_number, line in enumerate(text.splitlines()[start_line:], start=start_line + 1):
        action = ACTION_RE.search(line)
        if action:
            actions.append({
                "line": line_number,
                "step": int(action.group(1)),
                "tool": action.group(2),
                "args": action.group(3),
            })
        observation = OBS_RE.search(line)
        if observation:
            observations.append({
                "line": line_number,
                "step": int(observation.group(1)),
                "text": observation.group(2),
            })
    return actions, observations


def official_result(task_id, seed, fault_type):
    path = SOURCE / "outputs" / f"{fault_type}_task{task_id}_seed{seed}" / str(task_id) / f"{fault_type}_seed_{seed}" / "official_eval.json"
    if not path.exists():
        return None, "MISSING", str(path.relative_to(ROOT))
    result = json.loads(path.read_text(encoding="utf-8"))
    return result.get("official_success"), result.get("status"), str(path.relative_to(ROOT))


def analyse_one(status_path):
    status = json.loads(status_path.read_text(encoding="utf-8"))
    job = status["job"]
    fault_type = job["fault_type"]
    log_path = status_path.with_suffix("").with_suffix(".log")
    text = log_path.read_text(encoding="utf-8", errors="replace")
    fault_marker = text.find("[fault_high]")
    fault_text = text[fault_marker:] if fault_marker >= 0 else text
    actions, observations = parse_actions(fault_text)
    injection = None
    for match in INJECTION_RE.finditer(fault_text):
        injection = {
            "fault_type": match.group(1),
            "fault_layer": match.group(2),
            "fault_seed": int(match.group(3)),
            "fault_step": int(match.group(4)),
            "injection_index": int(match.group(5)),
        }
    if injection is None:
        for match in INJECTION_COUNT_RE.finditer(fault_text):
            if int(match.group(2)) != 0:
                steps = [int(x.strip()) for x in match.group(4).split(",") if x.strip().isdigit()]
                injection = {
                    "fault_type": fault_type,
                    "fault_layer": "unknown",
                    "fault_seed": int(match.group(2)),
                    "fault_step": steps[0] if steps else None,
                    "injection_index": 1,
                }
    fault_step = injection["fault_step"] if injection else None
    after_actions = [a for a in actions if fault_step is not None and a["step"] >= fault_step]
    after_observations = [o for o in observations if fault_step is not None and o["step"] > fault_step]
    injected_action = next((a for a in actions if fault_step is not None and a["step"] == fault_step), None)
    first_action = after_actions[0] if after_actions else None
    action_tools = [a["tool"] for a in actions]
    later_tools = [a["tool"] for a in after_actions]
    official_success, evaluator_status, evaluator_path = official_result(job["task_id"], job["seed"], fault_type)

    # These are deliberately evidence-based. A missing signal is not treated as recovery.
    later_actions = [a for a in actions if fault_step is not None and a["step"] > fault_step]
    retry = bool(injected_action and any(
        a["tool"] == injected_action["tool"] and a["args"] == injected_action["args"]
        for a in later_actions
    ))
    goto_fallback = bool(later_actions and later_actions[0]["tool"] == "goto")
    changed_tool = bool(injected_action and any(a["tool"] != injected_action["tool"] for a in later_actions))
    stop_after_fault = bool(later_actions and later_actions[0]["tool"] == "stop")
    premature_stop = stop_after_fault and official_success is not True
    recovered = official_success is True
    labels = []
    if recovered:
        labels.append("recovered")
    if not recovered and official_success is False:
        labels.append("fault_ignored" if not retry and not changed_tool else "fault_detected")
    if retry:
        labels.append("retry_executed")
    if after_observations:
        labels.append("observation_refreshed")
    if goto_fallback:
        labels.append("alternative_action_selected")
    if changed_tool:
        labels.append("alternative_action_selected")
    if premature_stop:
        labels.append("premature_stop")
    if not labels and official_success is None:
        labels.append("unknown")
    return {
        "fault_type": fault_type,
        "task_id": job["task_id"],
        "seed": job["seed"],
        "condition": "fault",
        "evidence_source": "log",
        "fault_step": fault_step,
        "fault_layer": injection["fault_layer"] if injection else None,
        "fault_seed": injection["fault_seed"] if injection else None,
        "first_action_after_fault": first_action["tool"] if first_action else None,
        "first_action_after_fault_step": first_action["step"] if first_action else None,
        "observation_after_fault": bool(after_observations),
        "retry_executed": retry,
        "goto_fallback": goto_fallback,
        "tool_changed": changed_tool,
        "plan_revised": None,
        "stale_action_repeated": retry,
        "premature_stop": premature_stop,
        "official_success": official_success,
        "evaluator_status": evaluator_status,
        "recovered": recovered,
        "behavior_labels": ";".join(dict.fromkeys(labels)),
        "injection_count": status.get("fault_count", 0),
        "source_status": str(status_path.relative_to(ROOT)),
        "source_log": str(log_path.relative_to(ROOT)),
        "official_eval": evaluator_path,
    }


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    rows = [analyse_one(path) for path in sorted(SOURCE.glob("*.status.json"))]
    fields = list(rows[0])
    with (OUTPUT / "fault_recovery_behavior.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)
    summary = []
    for fault_type in sorted({row["fault_type"] for row in rows}):
        group = [row for row in rows if row["fault_type"] == fault_type]
        summary.append({
            "fault_type": fault_type,
            "trials": len(group),
            "fault_detected": sum("fault_detected" in row["behavior_labels"] for row in group),
            "fault_ignored": sum("fault_ignored" in row["behavior_labels"] for row in group),
            "observation_refreshed": sum(row["observation_after_fault"] for row in group),
            "retry_executed": sum(row["retry_executed"] for row in group),
            "goto_fallback": sum(row["goto_fallback"] for row in group),
            "tool_changed": sum(row["tool_changed"] for row in group),
            "plan_revised": "not_observable_for_react",
            "stale_action_repeated": sum(row["stale_action_repeated"] for row in group),
            "premature_stop": sum(row["premature_stop"] for row in group),
            "recovered": sum(row["recovered"] for row in group),
            "official_success_rate": sum(bool(row["official_success"]) for row in group) / len(group),
        })
    with (OUTPUT / "fault_recovery_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary[0]))
        writer.writeheader(); writer.writerows(summary)
    print(json.dumps({"fault_trials": len(rows), "output": str(OUTPUT), "evidence_source": "log"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
