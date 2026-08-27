#!/usr/bin/env python3
"""Summarize the 54-trial ReAct vs Plan-and-Execute matrix."""

import csv
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "experiments/architecture_gpt54_react_planexecute_v1"
OUT = ROOT / "experiments/analysis/architecture_gpt54_react_planexecute_v1"

METRIC = re.compile(r"(?:control \(control\)|fault_high): 1/1 成功, avg ([0-9.]+) 步, avg ([0-9.]+) LLM calls, avg ([0-9.]+)s")
FINAL_METRIC = re.compile(r"[✅❌] steps=(\d+) time=([0-9.]+)s")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for response in sorted((SOURCE / "outputs").rglob("agent_response.json")):
        parts = response.relative_to(SOURCE / "outputs").parts
        outer, task_text, condition_seed = parts[-4:-1]
        match = re.match(r"(react|plan_execute)_(control|fault)_(.+)_task(\d+)_seed(\d+)", outer)
        if not match:
            raise RuntimeError(f"Unexpected output path: {response}")
        architecture, condition, fault_type, task_id, seed = match.groups()
        task_id, seed = int(task_id), int(seed)
        evaluation = json.loads(response.with_name("official_eval.json").read_text())
        log = SOURCE / f"{outer}.log"
        log_text = log.read_text(encoding="utf-8", errors="replace")
        metrics = METRIC.search(log_text)
        final = FINAL_METRIC.findall(log_text)
        steps = float(metrics.group(1)) if metrics else (float(final[-1][0]) if final else None)
        calls = float(metrics.group(2)) if metrics else steps
        elapsed = float(metrics.group(3)) if metrics else (float(final[-1][1]) if final else None)
        rows.append({
            "architecture": architecture,
            "condition": condition,
            "fault_type": "control" if condition == "control" else fault_type,
            "task_id": task_id,
            "seed": seed,
            "official_success": evaluation.get("official_success"),
            "evaluator_status": evaluation.get("status"),
            "steps": steps,
            "llm_calls": calls,
            "time_sec": elapsed,
            "agent_response": str(response.relative_to(ROOT)),
            "network_har": str(response.with_name("network.har").relative_to(ROOT)),
            "official_eval": str(response.with_name("official_eval.json").relative_to(ROOT)),
        })
    rows.sort(key=lambda r: (r["architecture"], r["condition"], r["fault_type"], r["task_id"], r["seed"]))
    with (OUT / "architecture_trial_results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n"); writer.writeheader(); writer.writerows(rows)
    summary = []
    for architecture in ("react", "plan_execute"):
        for condition, fault_type in (("control", "control"), ("fault", "web_dom_missing"), ("fault", "web_http_error")):
            group = [r for r in rows if r["architecture"] == architecture and r["condition"] == condition and r["fault_type"] == fault_type]
            summary.append({"architecture": architecture, "condition": condition, "fault_type": fault_type, "trials": len(group), "official_successes": sum(bool(r["official_success"]) for r in group), "official_success_rate": sum(bool(r["official_success"]) for r in group)/len(group), "mean_steps": sum(r["steps"] or 0 for r in group)/len(group), "mean_llm_calls": sum(r["llm_calls"] or 0 for r in group)/len(group), "mean_time_sec": sum(r["time_sec"] or 0 for r in group)/len(group), "evaluator_errors": sum(r["evaluator_status"] == "ERROR" for r in group)})
    with (OUT / "architecture_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary[0]), lineterminator="\n"); writer.writeheader(); writer.writerows(summary)
    pairs = []
    by_key = {(r["architecture"], r["condition"], r["fault_type"], r["task_id"], r["seed"]): r for r in rows}
    for condition, fault_type in (("control", "control"), ("fault", "web_dom_missing"), ("fault", "web_http_error")):
        for task in (27, 30, 132):
            for seed in (1,2,3):
                react = by_key[("react", condition, fault_type, task, seed)]
                plan = by_key[("plan_execute", condition, fault_type, task, seed)]
                pairs.append({"condition": condition, "fault_type": fault_type, "task_id": task, "seed": seed, "react_success": react["official_success"], "plan_execute_success": plan["official_success"], "success_delta_plan_minus_react": int(bool(plan["official_success"]))-int(bool(react["official_success"])), "react_steps": react["steps"], "plan_execute_steps": plan["steps"], "step_delta_plan_minus_react": (plan["steps"] or 0)-(react["steps"] or 0), "react_llm_calls": react["llm_calls"], "plan_execute_llm_calls": plan["llm_calls"], "llm_call_delta_plan_minus_react": (plan["llm_calls"] or 0)-(react["llm_calls"] or 0), "react_time_sec": react["time_sec"], "plan_execute_time_sec": plan["time_sec"], "time_delta_plan_minus_react": (plan["time_sec"] or 0)-(react["time_sec"] or 0)})
    with (OUT / "architecture_paired.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(pairs[0]), lineterminator="\n"); writer.writeheader(); writer.writerows(pairs)
    (OUT / "manifest.json").write_text(json.dumps({"trials":len(rows),"tasks":[27,30,132],"architectures":["react","plan_execute"],"conditions":["control","web_dom_missing","web_http_error"],"seeds":[1,2,3],"evaluator_errors":sum(r["evaluator_status"]=="ERROR" for r in rows)}, indent=2)+"\n")
    print(json.dumps({"trials":len(rows),"successes":sum(bool(r["official_success"]) for r in rows),"pairs":len(pairs)}))
if __name__ == "__main__": main()
