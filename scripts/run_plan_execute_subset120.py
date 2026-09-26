#!/usr/bin/env python3
"""Historical 120-trial GPT-5.4 subset; not a current experiment launcher.

For new runs use scripts/run_fault_matrix.py and docs/experiment_roadmap.md.
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TASKS = [22, 27, 28, 30, 132]
FAULTS = [
    "web_dom_missing",
    "web_popup_block",
    "web_http_error",
    "agent_param_error",
]
SEEDS = [1, 2, 3]
DEFAULT_OUTPUT = "experiments/formal_gpt54_plan_execute_official_subset120_v1"


def command_for(options: argparse.Namespace) -> list[str]:
    command = [
        sys.executable,
        "scripts/run_fault_matrix.py",
        "--output-dir", options.output_dir,
        "--official-output-root", f"{options.output_dir}/outputs",
        "--model-profile", "gpt54",
        "--architecture", "plan_execute",
        "--task-ids", *(str(task_id) for task_id in TASKS),
        "--fault-types", *FAULTS,
        "--trials", str(len(SEEDS)),
        "--fault-seed", str(SEEDS[0]),
        "--fault-intensity", "high",
        # 历史复现：本脚本固定注入步 2，复现已发布的历史产物（含 agent_param_error 臂）。
        # Stage C 正式运行请用 scripts/run_fault_matrix.py，其口径为 agent_param_error→第1个参数动作、其余→第2步。
        "--fault-injection-step", "2",
        "--max-steps", "20",
        "--workers", str(options.workers),
        "--job-timeout-minutes", str(options.job_timeout_minutes),
    ]
    if options.resume:
        command.append("--resume")
    return command


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--job-timeout-minutes", type=int, default=45)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    options = parser.parse_args()
    command = command_for(options)
    expected_trials = len(TASKS) * len(FAULTS) * len(SEEDS) * 2
    print({
        "experiment": "gpt54_plan_execute_official_subset120_v1",
        "expected_trials": expected_trials,
        "tasks": TASKS,
        "faults": FAULTS,
        "seeds": SEEDS,
        "command": command,
    })
    if not options.dry_run:
        raise SystemExit(subprocess.call(command, cwd=ROOT))


if __name__ == "__main__":
    main()
