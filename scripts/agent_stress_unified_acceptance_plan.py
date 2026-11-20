#!/usr/bin/env python3
"""Plan (do not execute) the 36-trial unified-architecture acceptance batch.

3 verified tasks x 2 models x 2 architectures x 3 conditions x 1 seed = 36.
A block is one task/model/architecture cell with its clean, single and sustained
conditions.  Existing screening and pilot artifacts are not modified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from fault_injection.http_service import (
    DEFAULT_WINDOW_STEPS, EXPERIMENT_ID, ServiceEndpoint,
)
from scripts.agent_stress_next_plans import (
    code_fingerprint, digest, file_sha, git_head, model_identity, python_cmd,
)
from standard_agent.core import execution
from standard_agent.stage_c_pipeline import current_evaluator_version
from standard_agent.webarena_verified import load_task_definition

load_dotenv(ROOT / ".env")

DATASET = Path(os.getenv("WEBARENA_DATASET",
                         "/data/webarena/webarena-dataset/webarena-verified.json"))
AUTH_DIR = ROOT / "experiments/auth_20261120"
OUT = ROOT / "experiments/agent_stress_v1/unified_acceptance_plan_20261120"
MODELS = ("qwen38_flash", "deepseek_v41_flash")
ARCHITECTURES = ("react", "plan_execute")
SEED = 1
MAX_STEPS = 20
WINDOW_STEPS = DEFAULT_WINDOW_STEPS
SCHEDULE_SEED = 20261120
SWITCHES = {"SUBMISSION_CONTRACT": "1", "RETRIEVAL_REPAIR": "1"}
PORTS = {"shopping": 7770, "gitlab": 8023, "reddit": 9999}
TASKS = {
    228: {
        "site": "shopping",
        "endpoint": "http://localhost:7770/catalogsearch/result",
        "alternative": "目录/商品页、/search/ajax/suggest、/catalogsearch/advanced",
    },
    28: {
        "site": "reddit",
        "endpoint": "http://localhost:9999/f/WorcesterMA",
        "alternative": "/forums/all、/forums、其他板块与 /search",
    },
    314: {
        "site": "gitlab",
        "endpoint": "http://localhost:8023/primer/design/-/graphs",
        "alternative": "项目树/commits 页与 REST /api/v4/projects/180/repository/contributors",
    },
}
CONDITIONS = (
    ("control", "control", "single", "off"),
    ("single", "fault", "single", "high"),
    ("sustained", "fault", "persistent", "high"),
)


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def task_row(task_id: int) -> dict:
    task = load_task_definition(task_id, path=DATASET)
    site = (task.get("sites") or [None])[0]
    if site != TASKS[task_id]["site"]:
        raise ValueError(f"task {task_id}: unexpected site {site!r}")
    start_url = (task.get("start_urls") or [None])[0]
    if not start_url:
        raise ValueError(f"task {task_id}: missing start URL")
    start_url = start_url.replace(f"__{site.upper()}__",
                                  f"http://localhost:{PORTS[site]}")
    return {
        "task_id": task_id,
        "site": site,
        "start_url": start_url,
        "intent": task["intent"],
        "intent_template_id": task.get("intent_template_id"),
        "endpoint": TASKS[task_id]["endpoint"],
        "alternative_paths": TASKS[task_id]["alternative"],
    }


def build_plan(out: Path = OUT) -> dict:
    tasks = {tid: task_row(tid) for tid in TASKS}
    blocks = [(tid, model, architecture) for tid in TASKS for model in MODELS
              for architecture in ARCHITECTURES]
    random.Random(SCHEDULE_SEED).shuffle(blocks)
    cells = []
    block_rows = []
    for block_index, (tid, model, architecture) in enumerate(blocks, 1):
        order = list(CONDITIONS)
        random.Random(SCHEDULE_SEED + block_index).shuffle(order)
        block_rows.append({
            "block": block_index, "task_id": tid, "model_profile": model,
            "architecture": architecture, "seed": SEED,
            "condition_order": [item[0] for item in order],
        })
        task = tasks[tid]
        for position, (condition, run_condition, variant, intensity) in enumerate(order, 1):
            key = f"{model}_task{tid}_{architecture}_seed{SEED}_{condition}"
            output_root = out / "cells" / key / "outputs"
            arm_label = "control" if condition == "control" else "web_http_error"
            arm = (output_root / f"web_http_error_task{tid}_seed{SEED}" / str(tid)
                   / f"{arm_label}_seed_{SEED}")
            command = [
                python_cmd(), "main.py", "--benchmark",
                "--site", task["site"], "--url", task["start_url"],
                "--task", task["intent"], "--task-id", str(tid),
                "--model-profile", model, "--architecture", architecture,
                "--max-steps", str(MAX_STEPS), "--trials", "1",
                "--fault-type", "web_http_error",
                "--fault-intensity", intensity,
                "--fault-seed", str(SEED),
                "--experiment-id", EXPERIMENT_ID,
                "--fault-variant", variant,
                "--fault-window-steps", str(WINDOW_STEPS),
                "--fault-target-endpoint", task["endpoint"],
                "--condition", run_condition,
                "--webarena-output-dir", str(output_root),
                "--storage-state-dir", str(AUTH_DIR),
                "--submission-contract",
            ]
            cells.append({
                "cell_key": key,
                "block": block_index,
                "order_in_block": position,
                "task_id": tid,
                "site": task["site"],
                "start_url": task["start_url"],
                "intent": task["intent"],
                "model_profile": model,
                "architecture": architecture,
                "seed": SEED,
                "fault_seed": SEED,
                "condition": condition,
                "run_condition": run_condition,
                "fault_variant": variant,
                "fault_intensity": intensity,
                "fault_type": "web_http_error",
                "experiment_id": EXPERIMENT_ID,
                "max_steps": MAX_STEPS,
                "window_steps": WINDOW_STEPS if condition != "control" else None,
                "endpoint": task["endpoint"],
                "output_dir": _rel(out / "cells" / key),
                "arm_dir": _rel(arm),
                "command": command,
            })
    plan = {
        "schema": "agent_stress_unified_acceptance_plan/1",
        "experiment_id": "agent-stress-v1-unified-acceptance-36",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "plan_only",
        "approved_for_paid_runs": False,
        "approval_note": ("Plan only. No trial may run until the unified execution "
                          "contract is reviewed and this plan is authorized."),
        "stage": "unified-execution-acceptance",
        "protocol_experiment_id": EXPERIMENT_ID,
        "tasks": {str(k): v for k, v in tasks.items()},
        "conditions": [item[0] for item in CONDITIONS],
        "models": list(MODELS),
        "architectures": list(ARCHITECTURES),
        "seeds": [SEED],
        "max_steps": MAX_STEPS,
        "window_steps": WINDOW_STEPS,
        "window_definition": "first exposure plus the following 9 proxy steps",
        "condition_order": "pre-randomised within each task/model/architecture block",
        "blocks": block_rows,
        "cells": cells,
        "planned_trials": len(cells),
        "switches": SWITCHES,
        "unified_execution": {
            "prompt_contract_sha256": hashlib.sha256(
                (execution.BROWSER_PROTOCOL + "\\n" + execution.THOUGHT_REMINDER_TEXT
                 + "\\n" + execution.NO_TOOL_NUDGE_TEXT).encode()).hexdigest(),
            "shared_prompt": "standard_agent/core/execution.py:build_system_prompt",
            "shared_messages": "standard_agent/core/execution.py:build_execution_messages",
            "shared_turn": "standard_agent/core/execution.py:run_executor_turn",
            "offline_acceptance": {
                "test_file": "tests/test_unified_execution.py",
                "sha256": file_sha(ROOT / "tests/test_unified_execution.py"),
                "command": "python -m pytest tests -q",
            },
            "plan_execute_only_fields": ["Current sub-goal", "Success condition"],
        },
        "storage_state_dir": str(AUTH_DIR.relative_to(ROOT)),
        "storage_state_sha256": {site: file_sha(AUTH_DIR / f"{site}.json")
                                 for site in ("shopping", "gitlab", "reddit")},
        "login_state": {
            "shopping": "logged in (My Account / Sign Out verified)",
            "gitlab": "logged in (dashboard projects verified)",
            "reddit": "anonymous by design (no-login empty state)",
        },
        "model_identity": model_identity(MODELS),
        "dataset_path": str(DATASET),
        "dataset_sha256": file_sha(DATASET),
        "evaluator": current_evaluator_version(),
        "code": {"head": git_head(), "fingerprint_sha256": code_fingerprint()},
        "prior_evidence": {
            "pilot_18": "experiments/agent_stress_v1/http_pilot_20261120/report.md",
            "qwen_clean_screening": "experiments/agent_stress_v1/qwen_seed_verify_20261120/summary.json",
            "note": "Prior artifacts keep their original code/version; this plan is the first unified comparison.",
        },
    }
    plan["plan_sha256"] = digest(plan)
    return plan


def write_readme(plan: dict, out: Path) -> Path:
    lines = [
        "# Unified-execution acceptance plan (36 trials)", "",
        "Plan only. No trial has been run and `approved_for_paid_runs` is false.", "",
        f"- Grid: 3 tasks x 2 models x 2 architectures x 3 conditions x 1 seed = "
        f"**{plan['planned_trials']}** cells in **{len(plan['blocks'])}** blocks.",
        f"- Models: {', '.join(plan['models'])}",
        f"- Architectures: {', '.join(plan['architectures'])}",
        f"- Tasks: {', '.join(plan['tasks'])}",
        f"- Max execution turns: {plan['max_steps']}; fault window: "
        f"{plan['window_steps']} proxy steps.",
        "- Each block is one task/model/architecture with clean, single and "
        "sustained in a pre-randomised order.", "",
        "## Blocks", "",
        "| Block | Task | Model | Architecture | Condition order |",
        "| ---: | ---: | --- | --- | --- |",
    ]
    for block in plan["blocks"]:
        lines.append(f"| {block['block']} | {block['task_id']} | "
                     f"{block['model_profile']} | {block['architecture']} | "
                     f"{' -> '.join(block['condition_order'])} |")
    lines += [
        "", "## Shared execution contract", "",
        "- One system prompt and one human message; history order is always "
        "System, Human, then prior AI/Tool messages.",
        "- Same tool list, AX Tree rules, scroll/navigation guidance, examples, "
        "THOUGHT requirement, retrieval guidance and submission contract.",
        "- Same one-tool-per-turn rule, multi-tool truncation, no-tool retry "
        "(one retry, then `no_tool_call`), THOUGHT check/record, stop corrections "
        "and 20-turn cap.",
        "- Same URL/page/empty-value fallbacks and the same visible tool-error "
        "observations (no filtering by architecture).",
        "- Planner and replanner never bind browser tools and never consume "
        "execution turns or the fault window; calls, tokens and time are recorded "
        "separately.", "",
        "## Intentional differences", "",
        "- ReAct sees a high-level plan context and no plan owner.",
        "- The plan-and-execute executor sees only `Current sub-goal` and "
        "`Success condition`; the planner/replanner own plan progression.",
        "- A replanner `finish` only requests executor wrap-up; completion still "
        "requires a valid `stop(answer)`.",
        "- A rejected submission routes the executor straight back to the same "
        "atomic sub-goal; it never advances or replans.",
        "", "## Preserved prior evidence", "",
        f"- 18-trial pilot: `{plan['prior_evidence']['pilot_18']}`.",
        f"- Qwen clean screening: `{plan['prior_evidence']['qwen_clean_screening']}`.",
        f"- {plan['prior_evidence']['note']}", "",
    ]
    path = out / "README.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    plan = build_plan(args.out)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_readme(plan, args.out)
    print(json.dumps({
        "plan": str(args.out / "plan.json"),
        "cells": plan["planned_trials"],
        "blocks": len(plan["blocks"]),
        "sha256": plan["plan_sha256"],
        "head": plan["code"]["head"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
