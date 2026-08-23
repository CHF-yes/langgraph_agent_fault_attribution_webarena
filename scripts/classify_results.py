"""Backfill attribution labels for an existing baseline result JSON."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from run_baseline import load_tasks
from standard_agent.failure_classification import classify_trial


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    rows = json.loads(args.input.read_text(encoding="utf-8"))
    tasks = {task["task_id"]: task for task in load_tasks()}
    for row in rows:
        row.update(classify_trial(row, tasks.get(row.get("task_id"))))
        # In the parallel batch, each subprocess used trial_id=1 while the
        # run_seed encoded the intended repetition number.
        if row.get("run_seed") in {1, 2, 3}:
            row["trial_id"] = row["run_seed"]
    args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
