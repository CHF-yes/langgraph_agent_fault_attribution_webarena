#!/usr/bin/env python3
"""Classify whether existing successful trajectories naturally hit the frozen endpoint.

A natural hit is an Agent browser action whose resulting URL (or ``goto`` target)
matches the frozen service endpoint identity.  A trace that only loads the data
through a same-URL background request is *not* a natural endpoint hit; it is
reported as an exposure question because the service-level injector only arms
after a navigation action.

No model or browser is started.  The script reads frozen progress files and
trace JSONL files.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fault_injection.http_service import ServiceEndpoint

OUT = ROOT / "experiments" / "agent_stress_v1" / "formal_freeze_20261123"
ROSTER = OUT / "roster.json"
PROGRESS = [
    ROOT / "experiments/agent_stress_v1/screening_qwen_240_run2/progress.json",
    ROOT / "experiments/agent_stress_v1/candidate_v2_20261117/supplement_screening/run/progress.json",
    ROOT / "experiments/agent_stress_v1/unified_acceptance_run_20261121/progress.json",
]
LLM_EVENTS = {"llm_request", "llm_provenance", "llm_response"}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def after_url(event: dict) -> str | None:
    """Return the URL after a successful browser action.

    Trace ``url`` is the pre-action URL for ``goto`` and for most tool calls;
    the observation carries the post-action URL.  ``goto`` destination is the
    action argument when the observation has no explicit URL.
    """
    observation = str(event.get("observation") or "")
    if observation.startswith("[Error]"):
        return None
    match = re.search(r"当前 URL:\s*(\S+)", observation)
    if match:
        return match.group(1)
    if event.get("action") == "goto":
        return (event.get("args") or {}).get("url")
    return None


def action_rows(trace: Path) -> list[dict]:
    rows = []
    if not trace.is_file():
        return rows
    for line in trace.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("event") in LLM_EVENTS or not event.get("action"):
            continue
        rows.append({
            "step": event.get("step"),
            "action": event.get("action"),
            "args": event.get("args") or {},
            "url_before": event.get("url"),
            "url_after": after_url(event),
            "observation_head": str(event.get("observation") or "")[:400],
        })
    return rows


def successful_rows() -> dict[int, list[dict]]:
    rows = defaultdict(list)
    for path in PROGRESS:
        if not path.is_file():
            continue
        data = load(path)
        for result in data.get("results") or []:
            if result.get("official_success"):
                rows[int(result["task_id"])].append({
                    "trace": str(result.get("trace") or ""),
                    "architecture": result.get("architecture"),
                    "seed": result.get("seed"),
                    "condition": result.get("condition"),
                    "experiment": data.get("experiment_id"),
                })
    return rows


def classify(task: dict, results: list[dict]) -> dict:
    endpoint = ServiceEndpoint.from_url(task["endpoint"])
    traces = []
    for result in results:
        trace = Path(result["trace"])
        actions = action_rows(trace)
        matches = []
        same_url_interactions = []
        for action in actions:
            target = action["url_after"] or ""
            matched = bool(target) and endpoint.matches(target)[0]
            if matched:
                matches.append({**action, "matched_url": target})
            if (action["action"] != "goto" and action["url_before"]
                    and action["url_after"]
                    and action["url_before"].split("#")[0] == action["url_after"].split("#")[0]):
                same_url_interactions.append({
                    "step": action["step"], "action": action["action"],
                    "url": action["url_after"],
                    "observation_head": action["observation_head"],
                })
        non_stop = [row for row in matches if row["action"] != "stop"]
        first = (non_stop or matches or [None])[0]
        traces.append({
            "trace": str(trace),
            "architecture": result.get("architecture"),
            "seed": result.get("seed"),
            "condition": result.get("condition"),
            "actions": len(actions),
            "matched_actions": matches,
            "first_hit": first,
            "hit": bool(matches),
            "hit_by_non_stop_action": bool(non_stop),
            "same_url_interactions": same_url_interactions,
        })
    hit_traces = [row for row in traces if row["hit"]]
    non_stop_hit_traces = [row for row in traces if row["hit_by_non_stop_action"]]
    if not traces:
        classification = "insufficient_evidence"
    elif hit_traces:
        classification = "confirmed_natural_hit"
    else:
        classification = "existing_trajectory_no_hit"
    coverage = (len(hit_traces) / len(traces)) if traces else None
    question = None
    recommendation = None
    if classification == "existing_trajectory_no_hit":
        question = ("successful trajectories never navigated to the frozen endpoint; "
                    "the data was reached through a same-URL interaction/background request")
        recommendation = ("replace the task, move the endpoint to a naturally navigated "
                          "task-critical page, or amend the protocol to activate on the "
                          "same-URL background request")
    elif classification == "confirmed_natural_hit" and (coverage or 0) < 0.5:
        question = ("the endpoint is naturally reachable, but only in a minority of "
                    "successful trajectories")
        recommendation = ("consider a more frequently used task-critical endpoint or "
                          "retain the task with an explicit low-coverage note")
    return {
        "task_id": task["task_id"],
        "site": task["site"],
        "template_id": task["template_id"],
        "endpoint": task["endpoint"],
        "classification": classification,
        "successful_traces": len(traces),
        "hit_traces": len(hit_traces),
        "non_stop_hit_traces": len(non_stop_hit_traces),
        "coverage": coverage,
        "question": question,
        "recommendation": recommendation,
        "traces": traces,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUT)
    args = parser.parse_args(argv)
    roster = load(ROSTER)
    successes = successful_rows()
    rows = [classify(task, successes.get(int(task["task_id"]), []))
            for task in roster["tasks"]]
    summary = {
        "schema": "agent_stress_endpoint_natural_exposure/1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": "existing successful screening/acceptance trajectories",
        "definition": ("natural hit = an Agent browser action whose resulting URL or goto "
                       "target matches the frozen endpoint path; stop actions alone are "
                       "reported but not counted as a navigation"),
        "rows": rows,
        "counts": {
            "confirmed_natural_hit": sum(1 for row in rows
                                         if row["classification"] == "confirmed_natural_hit"),
            "existing_trajectory_no_hit": sum(1 for row in rows
                                              if row["classification"] == "existing_trajectory_no_hit"),
            "insufficient_evidence": sum(1 for row in rows
                                         if row["classification"] == "insufficient_evidence"),
        },
        "questions": [row["task_id"] for row in rows if row["question"]],
    }
    out = args.output_dir
    dump(out / "endpoint_natural_exposure.json", summary)
    lines = [
        "# Endpoint natural-exposure review", "",
        "A **natural hit** means the Agent's own browser action changed the page URL (or",
        "issued a `goto`) to the frozen endpoint. A same-URL click that only triggers a",
        "background request is not counted as a natural hit.", "",
        "| Task | Site | Template | Endpoint | Success traces | Hit traces | Coverage | Classification | First hit |",
        "| ---: | --- | ---: | --- | ---: | ---: | ---: | --- | --- |",
    ]
    for row in rows:
        first = ""
        for trace in row["traces"]:
            hit = trace.get("first_hit")
            if hit:
                first = (f"`{Path(trace['trace']).name}` step {hit['step']} "
                         f"{hit['action']} -> `{hit['matched_url']}`")
                break
        lines.append(
            f"| {row['task_id']} | {row['site']} | {row['template_id']} | "
            f"`{row['endpoint']}` | {row['successful_traces']} | {row['hit_traces']} | "
            f"{row['coverage']:.0%} | {row['classification']} | {first or '—'} |")
    lines += ["", "## Questions", ""]
    for row in rows:
        if not row["question"]:
            continue
        lines += [f"- **{row['task_id']}**: {row['question']}",
                  f"  - Recommendation: {row['recommendation']}"]
    lines += ["", "Per-trace action steps and paths are in `endpoint_natural_exposure.json`.",
              "Scripted `goto` and HAR requests remain mechanism evidence; they are not",
              "counted as natural Agent navigation."]
    (out / "endpoint_natural_exposure.md").write_text("\n".join(lines) + "\n",
                                                      encoding="utf-8")
    print(json.dumps({
        "counts": summary["counts"],
        "questions": summary["questions"],
        "output": str(out / "endpoint_natural_exposure.json"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
