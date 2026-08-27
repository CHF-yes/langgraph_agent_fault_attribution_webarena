#!/usr/bin/env python3
"""Evaluate saved WebArena response/HAR pairs and write official_eval.json."""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from standard_agent.webarena_evaluator import evaluate_task_safe


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--config", default="experiments/webarena_local_config.json")
    options = parser.parse_args()
    root = ROOT / options.root
    results = []
    for response in sorted(root.rglob("agent_response.json")):
        har = response.with_name("network.har")
        if not har.exists():
            continue
        parts = response.relative_to(root).parts
        task_id_match = next((part for part in parts if part.isdigit()), None)
        if not task_id_match:
            continue
        task_id = int(task_id_match)
        result = evaluate_task_safe(
            task_id, agent_response_path=response,
            network_trace_path=har, config_path=options.config,
        )
        output = response.with_name("official_eval.json")
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        results.append({
            "task_id": task_id,
            "response": str(response.relative_to(ROOT)),
            "har": str(har.relative_to(ROOT)),
            "official_eval": str(output.relative_to(ROOT)),
            "official_success": result.get("official_success"),
            "status": result.get("status"),
            "error": result.get("error_msg"),
        })
    (root / "official_eval_summary.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "evaluated": len(results),
        "successes": sum(bool(r["official_success"]) for r in results),
        "errors": sum(r["status"] == "ERROR" for r in results),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
