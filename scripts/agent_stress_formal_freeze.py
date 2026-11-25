#!/usr/bin/env python3
"""Freeze the 24-task roster, the 864-cell formal plan and the analysis record.

Inputs are the archived 20-task candidate pool, the four supplement-screening
tasks and the per-task browser acceptance under ``formal_freeze_20261123``.
The script never starts an agent trial.  It writes the plan, the authorization
*candidate*, the freeze record, and runs the no-model ``--check``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from scripts.agent_stress_next_plans import (
    code_fingerprint, digest, file_sha, git_head, model_identity,
)
from standard_agent.stage_c_pipeline import current_evaluator_version
from standard_agent.webarena_verified import load_task_definition

load_dotenv(ROOT / ".env")

DATASET = Path(os.getenv("WEBARENA_DATASET",
                         "/data/webarena/webarena-dataset/webarena-verified.json"))
AUTH_DIR = ROOT / "experiments" / "auth_20261120"
PROVENANCE = ROOT / "experiments/agent_stress_v1/execution_prep_20261120/model_provenance.json"
OUT = ROOT / "experiments" / "agent_stress_v1" / "formal_freeze_20261123"
REFERENCE = OUT / "reference"
PLAN_PATH = OUT / "plan.json"
ANALYSIS_DOC = ROOT / "docs" / "agent_stress_v1" / "formal_matrix_analysis_20261123.json"
ANALYSIS_COPY = OUT / "analysis_plan.json"
MODELS = ("qwen38_flash", "deepseek_v41_flash")
ARCHITECTURES = ("react", "plan_execute")
SEEDS = (1, 2, 3)
MAX_STEPS = 20
WINDOW_STEPS = 10
SCHEDULE_SEED = 20261123
SWITCHES = {"SUBMISSION_CONTRACT": "1", "RETRIEVAL_REPAIR": "1"}
PORTS = {"shopping": 7770, "gitlab": 8023, "reddit": 9999}
CONDITIONS = (
    ("control", "control", "single", "off"),
    ("single", "fault", "single", "high"),
    ("sustained", "fault", "persistent", "high"),
)
ROSTER = [27, 28, 48, 49, 66, 69, 148, 149, 163, 164, 168, 188, 190, 206,
          227, 228, 294, 295, 305, 314, 316, 384, 784, 785]
SUPPLEMENT = {69, 149, 206, 168}
PILOT = {28, 228, 314}
DIFFICULTY = {
    "easy": {27, 48, 49, 148, 164, 168, 190, 314},
    "medium": {28, 66, 163, 188, 227, 228, 294, 316, 784, 785},
    "hard": {69, 149, 206, 295, 305, 384},
}
COMBINED = {
    27: 6, 28: 4, 48: 6, 49: 5, 66: 4, 69: 2, 148: 5, 149: 2, 163: 4,
    164: 6, 168: 6, 188: 4, 190: 5, 206: 2, 227: 3, 228: 3, 294: 3,
    295: 2, 305: 2, 314: 6, 316: 3, 384: 2, 784: 3, 785: 3,
}
OPERATION = {
    27: "评论统计", 28: "评论统计", 48: "订单统计/订单信息",
    49: "订单统计/订单信息", 66: "帖子筛选/提取", 69: "帖子筛选/提取",
    148: "订单商品选项", 149: "订单商品选项", 163: "评论提取",
    164: "评论提取", 168: "仓库星标检索", 188: "订单统计/订单信息",
    190: "订单统计/订单信息", 206: "代码提交统计", 227: "商品聚合",
    228: "商品聚合", 294: "仓库信息检索", 295: "仓库信息检索",
    305: "代码提交统计", 314: "贡献者检索", 316: "贡献者检索",
    384: "评论提取", 784: "贡献者检索", 785: "贡献者检索",
}
# Endpoint table is the frozen browser acceptance table.
REFERENCE_ENDPOINTS = {
    27: "http://localhost:9999/f/personalfinance",
    28: "http://localhost:9999/f/WorcesterMA",
    48: "http://localhost:7770/sales/order/history",
    49: "http://localhost:7770/sales/order/history",
    66: "http://localhost:9999/f/books",
    69: "http://localhost:9999/f/books",
    148: "http://localhost:7770/sales/order/history",
    149: "http://localhost:7770/sales/order/history",
    163: "http://localhost:7770/review/product/listAjax/id/38805",
    164: "http://localhost:7770/review/product/listAjax/id/6523",
    168: "http://localhost:8023/dashboard/projects",
    188: "http://localhost:7770/sales/order/history",
    190: "http://localhost:7770/sales/order/history",
    206: "http://localhost:8023/a11yproject/a11yproject.com/-/commits/main",
    227: "http://localhost:7770/catalogsearch/result",
    228: "http://localhost:7770/catalogsearch/result",
    294: "http://localhost:8023/search",
    295: "http://localhost:8023/search",
    305: "http://localhost:8023/a11yproject/a11yproject.com/-/commits/main",
    314: "http://localhost:8023/primer/design/-/graphs",
    316: "http://localhost:8023/facebook/create-react-app/-/graphs",
    384: "http://localhost:7770/catalogsearch/result",
    784: "http://localhost:8023/CellularPrivacy/Android-IMSI-Catcher-Detector/-/graphs",
    785: "http://localhost:8023/CellularPrivacy/Android-IMSI-Catcher-Detector/-/graphs",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def task_meta(task_id: int) -> dict:
    task = load_task_definition(task_id, path=DATASET)
    site = (task.get("sites") or [None])[0]
    start = (task.get("start_urls") or [None])[0]
    if start:
        start = start.replace(f"__{site.upper()}__", f"http://localhost:{PORTS[site]}")
    return {"task_id": task_id, "site": site, "start_url": start,
            "intent": task["intent"],
            "intent_template_id": task.get("intent_template_id")}


def reference_row(task_id: int) -> dict:
    base = REFERENCE / f"task{task_id}"
    acceptance = load(base / "acceptance.json")
    normal = load(base / "normal" / "reference_path.json")
    recovery = load(base / "recovery" / "reference_path.json")
    fault = load(base / "recovery" / "fault_evidence.json")
    snapshot = (fault.get("episode") or {}).get("snapshot") or {}
    return {
        "task_id": task_id,
        "passed": acceptance["passed"],
        "endpoint": acceptance["endpoint"],
        "normal": {
            "passed": acceptance["normal"]["passed"],
            "steps": normal["steps"],
            "official_success": normal["official_success"],
            "answer": normal["answer"],
            "path": f"reference/task{task_id}/normal/reference_path.json",
            "eval": f"reference/task{task_id}/normal/official_eval.json",
        },
        "trigger": {
            "passed": acceptance["trigger"]["passed"],
            "injections": acceptance["trigger"]["injections"],
            "first_exposure_step": snapshot.get("t0"),
            "error_type": acceptance["trigger"]["error_type"],
            "path": f"reference/task{task_id}/recovery/fault_evidence.json",
        },
        "recovery": {
            "passed": acceptance["recovery"]["passed"],
            "steps": recovery["steps"],
            "released": snapshot.get("released"),
            "official_success": recovery["official_success"],
            "answer": recovery["answer"],
            "path": f"reference/task{task_id}/recovery/reference_path.json",
            "eval": f"reference/task{task_id}/recovery/official_eval.json",
        },
    }


def consolidate_reference() -> dict:
    rows = []
    for task_id in ROSTER:
        rows.append(load(REFERENCE / f"task{task_id}" / "acceptance.json"))
    summary = {
        "schema": "agent_stress_frozen_reference_summary/1",
        "created_at": now(),
        "roster": ROSTER,
        "passed": sum(1 for row in rows if row["passed"]),
        "total": len(rows),
        "rows": rows,
    }
    dump(REFERENCE / "acceptance.json", summary)
    return summary


def build_roster() -> dict:
    rows = []
    template_counts: dict[str, int] = {}
    site_counts: dict[str, int] = {}
    for task_id in ROSTER:
        meta = task_meta(task_id)
        ref = reference_row(task_id)
        difficulty = next((name for name, members in DIFFICULTY.items()
                          if task_id in members), None)
        template = str(meta["intent_template_id"])
        template_counts[template] = template_counts.get(template, 0) + 1
        site_counts[meta["site"]] = site_counts.get(meta["site"], 0) + 1
        source = ("supplement screening 2026-11-17" if task_id in SUPPLEMENT
                  else "archived candidate_v2 pool 2026-11-17")
        note = (f"{source}; tier {difficulty}; {COMBINED[task_id]}/6 combined clean "
                "successes; endpoint and reference path replayed 2026-11-23")
        if task_id in PILOT:
            note += ("; participated in the accepted 36-trial fault pilot, retained and "
                     "excluded only in the 21-task sensitivity analysis")
        rows.append({
            "task_id": task_id,
            "site": meta["site"],
            "template_id": meta["intent_template_id"],
            "operation": OPERATION[task_id],
            "difficulty": difficulty,
            "combined_success": COMBINED[task_id],
            "enrollment_source": source,
            "enrollment_basis": note,
            "supplement": task_id in SUPPLEMENT,
            "fault_pilot": task_id in PILOT,
            "endpoint": ref["endpoint"],
            "start_url": meta["start_url"],
            "intent": meta["intent"],
            "reference": ref,
        })
    if len(rows) != 24:
        raise ValueError(f"roster must contain 24 tasks, got {len(rows)}")
    if any(count > 2 for count in template_counts.values()):
        raise ValueError(f"template cap violated: {template_counts}")
    if len(template_counts) != 14:
        raise ValueError(f"expected 14 templates, got {len(template_counts)}")
    expected = {"easy": 8, "medium": 10, "hard": 6}
    actual = {name: sum(1 for row in rows if row["difficulty"] == name)
              for name in expected}
    if actual != expected:
        raise ValueError(f"difficulty distribution {actual} != {expected}")
    return {
        "schema": "agent_stress_frozen_roster/1",
        "created_at": now(),
        "source_commit": git_head(),
        "selection_rule": {
            "tasks": 24,
            "templates_min": 12,
            "per_template_max": 2,
            "difficulty": {"easy": "5-6/6", "medium": "3-4/6", "hard": "2/6"},
            "distribution": expected,
            "pilot_policy": ("28, 228 and 314 participated in the 36-trial fault pilot; "
                             "they stay in the formal roster and are excluded only in "
                             "the 21-task sensitivity analysis"),
        },
        "tasks": rows,
        "task_ids": ROSTER,
        "supplement_ids": sorted(SUPPLEMENT),
        "fault_pilot_ids": sorted(PILOT),
        "sensitivity_excluded_ids": sorted(PILOT),
        "sensitivity_included_ids": [t for t in ROSTER if t not in PILOT],
        "distributions": {
            "difficulty": actual,
            "templates": len(template_counts),
            "template_counts": dict(sorted(template_counts.items(),
                                           key=lambda item: int(item[0]))),
            "sites": dict(sorted(site_counts.items())),
            "supplements": len(SUPPLEMENT),
            "fault_pilot": len(PILOT),
        },
    }


def expected_command(cell: dict, plan: dict) -> list[str]:
    output_root = ROOT / cell["output_dir"] / "outputs"
    return [
        sys.executable, "main.py", "--benchmark",
        "--site", cell["site"], "--url", cell["start_url"],
        "--task", cell["intent"], "--task-id", str(cell["task_id"]),
        "--model-profile", cell["model_profile"],
        "--architecture", cell["architecture"],
        "--max-steps", str(cell["max_steps"]), "--trials", "1",
        "--fault-type", cell["fault_type"],
        "--fault-intensity", cell["fault_intensity"],
        "--fault-seed", str(cell["seed"]),
        "--experiment-id", cell["experiment_id"],
        "--fault-variant", cell["fault_variant"],
        "--fault-window-steps", str(plan["window_steps"]),
        "--fault-target-endpoint", cell["endpoint"],
        "--condition", cell["run_condition"],
        "--webarena-output-dir", str(output_root),
        "--storage-state-dir", str(AUTH_DIR),
        "--submission-contract",
    ]


def build_plan(roster: dict) -> dict:
    tasks = {}
    for row in roster["tasks"]:
        tasks[str(row["task_id"])] = {
            "task_id": row["task_id"],
            "site": row["site"],
            "start_url": row["start_url"],
            "intent": row["intent"],
            "intent_template_id": row["template_id"],
            "difficulty": row["difficulty"],
            "combined_success": row["combined_success"],
            "supplement": row["supplement"],
            "fault_pilot": row["fault_pilot"],
            "endpoint": row["endpoint"],
            "alternative_or_recovery": (
                "service-level HTTP fault on the endpoint; documented recovery hits the "
                "endpoint once, spends the 10-step window on non-target navigations, "
                "then re-reads it and completes the official contract"),
            "reference_evidence": f"reference/task{row['task_id']}/acceptance.json",
        }
    block_specs = [(tid, model, architecture, seed)
                   for tid in ROSTER for model in MODELS
                   for architecture in ARCHITECTURES for seed in SEEDS]
    random.Random(SCHEDULE_SEED).shuffle(block_specs)
    cells = []
    blocks = []
    for block_index, (task_id, model, architecture, seed) in enumerate(block_specs, 1):
        order = list(CONDITIONS)
        random.Random(SCHEDULE_SEED + block_index).shuffle(order)
        blocks.append({
            "block": block_index,
            "task_id": task_id,
            "model_profile": model,
            "architecture": architecture,
            "seed": seed,
            "condition_order": [item[0] for item in order],
        })
        task = tasks[str(task_id)]
        for position, (condition, run_condition, variant, intensity) in enumerate(order, 1):
            key = f"{model}_task{task_id}_{architecture}_seed{seed}_{condition}"
            output_dir = f"experiments/agent_stress_v1/formal_freeze_20261123/run/cells/{key}"
            label = "control" if condition == "control" else "web_http_error"
            arm_dir = f"{output_dir}/outputs/{task_id}/{label}_seed_{seed}"
            cell = {
                "cell_key": key,
                "block": block_index,
                "order_in_block": position,
                "task_id": task_id,
                "site": task["site"],
                "start_url": task["start_url"],
                "intent": task["intent"],
                "intent_template_id": task["intent_template_id"],
                "difficulty": task["difficulty"],
                "model_profile": model,
                "architecture": architecture,
                "seed": seed,
                "fault_seed": seed,
                "condition": condition,
                "run_condition": run_condition,
                "fault_variant": variant,
                "fault_intensity": intensity,
                "fault_type": "web_http_error",
                "experiment_id": "ep-http-service-v1",
                "max_steps": MAX_STEPS,
                "window_steps": WINDOW_STEPS if condition != "control" else None,
                "endpoint": task["endpoint"],
                "output_dir": output_dir,
                "arm_dir": arm_dir,
            }
            cell["command"] = expected_command(cell, {"window_steps": WINDOW_STEPS})
            cells.append(cell)
    plan = {
        "schema": "agent_stress_formal_matrix_plan/1",
        "experiment_id": "agent-stress-v1-formal-matrix-864",
        "created_at": now(),
        "status": "plan_only",
        "approved_for_paid_runs": False,
        "approval_note": ("Plan only. No formal trial may run until this plan, its "
                          "authorization candidate and the 24-task acceptance evidence "
                          "are reviewed and explicitly approved."),
        "stage": "formal-matrix",
        "protocol_experiment_id": "ep-http-service-v1",
        "roster_sha256": digest(roster),
        "tasks": tasks,
        "conditions": [item[0] for item in CONDITIONS],
        "models": list(MODELS),
        "architectures": list(ARCHITECTURES),
        "seeds": list(SEEDS),
        "max_steps": MAX_STEPS,
        "window_steps": WINDOW_STEPS,
        "window_definition": "first exposure plus the following 9 proxy steps",
        "schedule_seed": SCHEDULE_SEED,
        "condition_order": "pre-randomised within each task/model/architecture/seed block; fixed in plan order",
        "blocks": blocks,
        "cells": cells,
        "planned_trials": len(cells),
        "planned_blocks": len(blocks),
        "switches": SWITCHES,
        "unified_execution": {
            "prompt_contract_note": "same executor, tool list, 20-turn budget and submission correction as the accepted 36-trial batch",
            "reused_acceptance": "experiments/agent_stress_v1/unified_acceptance_run_20261121",
        },
        "storage_state_dir": str(AUTH_DIR.relative_to(ROOT)),
        "storage_state_sha256": {site: file_sha(AUTH_DIR / f"{site}.json")
                                 for site in ("shopping", "gitlab", "reddit")},
        "login_state": {
            "shopping": "logged in (My Account / Sign Out verified)",
            "gitlab": "logged in (dashboard projects verified)",
            "reddit": "anonymous by design (no-login empty state)",
        },
        "model_identity": model_identity(MODELS, provenance=PROVENANCE),
        "dataset_path": str(DATASET),
        "dataset_sha256": file_sha(DATASET),
        "evaluator_config": str((ROOT / "experiments/webarena_local_config.local.json").relative_to(ROOT)),
        "evaluator_config_sha256": file_sha(ROOT / "experiments/webarena_local_config.local.json"),
        "evaluator": current_evaluator_version(),
        "code": {"head": git_head(), "fingerprint_sha256": code_fingerprint()},
        "reference_acceptance": {
            "path": "reference/acceptance.json",
            "sha256": file_sha(REFERENCE / "acceptance.json"),
            "passed_tasks": sum(1 for row in roster["tasks"] if row["reference"]["passed"]),
        },
        "exclusions": {
            "sensitivity_excludes": sorted(PILOT),
            "sensitivity_kept": [t for t in ROSTER if t not in PILOT],
            "rule": "formal results report all 24 tasks and a 21-task sensitivity analysis excluding the three fault-pilot tasks",
        },
        "artifact_layout": {
            "cell_root": "experiments/agent_stress_v1/formal_freeze_20261123/run/cells/<cell_key>",
            "arm_dir": "<cell_root>/outputs/<task_id>/<control|web_http_error>_seed_<seed>",
            "resume": "plan order + canonical cell_key + persisted per-cell trial_record/official_eval",
        },
    }
    plan["plan_sha256"] = digest(plan)
    return plan


def build_authorization(plan: dict) -> dict:
    return {
        "schema": "agent_stress_formal_matrix_authorization_candidate/1",
        "experiment_id": plan["experiment_id"],
        "status": "candidate",
        "approved_for_paid_runs": False,
        "created_at": now(),
        "note": ("Candidate only. Set status=frozen and approved_for_paid_runs=true "
                 "only after explicit user approval of the 24-task evidence and this plan."),
        "plan_sha256": plan["plan_sha256"],
        "code_fingerprint_sha256": plan["code"]["fingerprint_sha256"],
        "dataset_sha256": plan["dataset_sha256"],
        "prompt_token_stop_before_next_trial": 117_200_000,
        "scope": {
            "tasks": ROSTER,
            "models": list(MODELS),
            "architectures": list(ARCHITECTURES),
            "seeds": list(SEEDS),
            "conditions": [item[0] for item in CONDITIONS],
            "max_steps": MAX_STEPS,
            "window_steps": WINDOW_STEPS,
            "trials": plan["planned_trials"],
            "blocks": plan["planned_blocks"],
        },
        "cell_keys": [cell["cell_key"] for cell in plan["cells"]],
    }


def usage_estimate() -> dict:
    # Observed 36-trial acceptance totals, per condition (12 trials each).
    per_trial = {
        "control": 82_813.5,
        "single": 105_677.1,
        "sustained": 218_181.4,
    }
    trials_per_condition = 24 * 2 * 2 * 3
    prompt = sum(value * trials_per_condition for value in per_trial.values())
    return {
        "basis": "36-trial unified acceptance (experiments/agent_stress_v1/unified_acceptance_run_20261121/summary.json)",
        "observed_prompt_tokens": 4_880_064,
        "per_condition_prompt_tokens_per_trial": per_trial,
        "trials_per_condition": trials_per_condition,
        "estimated_prompt_tokens": int(round(prompt)),
        "estimated_prompt_tokens_range": [int(round(prompt * 0.8)), int(round(prompt * 1.25))],
        "estimated_total_tokens": int(round(prompt * 1.06)),
        "note": ("Sustained arms dominate the estimate; planner tokens are already "
                 "included in the observed prompt totals."),
    }


def write_readme(roster: dict, plan: dict, check: dict) -> None:
    lines = [
        "# Formal freeze: 24 tasks / 864 trials", "",
        f"- Roster: **{len(roster['tasks'])}** tasks, **{roster['distributions']['templates']}** templates, "
        f"max 2 per template.",
        f"- Difficulty: easy {roster['distributions']['difficulty']['easy']}, "
        f"medium {roster['distributions']['difficulty']['medium']}, "
        f"hard {roster['distributions']['difficulty']['hard']}.",
        f"- Supplements: {', '.join(map(str, roster['supplement_ids']))}; "
        f"fault pilot retained: {', '.join(map(str, roster['fault_pilot_ids']))}.",
        f"- Plan: **{plan['planned_trials']}** cells in **{plan['planned_blocks']}** blocks "
        f"(24 x 2 models x 2 architectures x 3 seeds x 3 conditions).",
        f"- Plan SHA-256: `{plan['plan_sha256']}`",
        f"- `--check`: **{'PASS' if check.get('ok') else 'FAIL'}**"
        f" ({', '.join(check.get('blockers') or []) or 'no blockers'}).",
        "", "## Reference acceptance", "",
        "| Task | Site | Tpl | Difficulty | Normal steps | Recovery steps | Injections | Passed |",
        "| ---: | --- | ---: | --- | ---: | ---: | ---: | --- |",
    ]
    for row in roster["tasks"]:
        ref = row["reference"]
        lines.append(f"| {row['task_id']} | {row['site']} | {row['template_id']} | "
                     f"{row['difficulty']} | {ref['normal']['steps']} | "
                     f"{ref['recovery']['steps']} | {ref['trigger']['injections']} | "
                     f"{'yes' if ref['passed'] else 'NO'} |")
    lines += [
        "", "## Frozen analysis", "",
        "- Primary outcome: official binary success; sustained minus control is the fault effect;",
        "  compare that effect across the two models and two architectures.",
        "- Secondary: single minus control, injections, steps, token cost, human-adjudicated",
        "  budget exhaustion.",
        "- ITT denominator: legal non-trigger, detour, wrong answer and budget exhaustion stay",
        "  in the allocated arm.",
        "- Repeats are averaged within task first, then within template; uncertainty is",
        "  clustered by template.",
        "- Formal report covers all 24 tasks; a 21-task sensitivity analysis excludes 28, 228, 314.",
        "", "## Artifacts", "",
        "- `roster.json`, `plan.json`, `authorization.candidate.json`, `freeze.json`, `check.json`",
        "- Per-task evidence: `reference/taskNN/{normal,recovery}/`",
        "- `acceptance.json` / `acceptance.md`: the 24-task endpoint table.",
        "", "No formal agent trial was started by this freeze.",
    ]
    (OUT / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_acceptance_markdown(roster: dict) -> None:
    lines = [
        "# 24-task endpoint and reference-path acceptance", "",
        "All paths are no-model scripted browser actions. `normal` starts from the task",
        "start URL with the shared login state, stays within 20 turns and is scored by",
        "the native evaluator. `recovery` arms the persistent service-level HTTP fault,",
        "confirms one qualified navigation is faulted, spends the 10-step window on",
        "non-target navigations, re-reads the endpoint after release and is scored.", "",
        "| Task | Site | Template | Difficulty | Endpoint | Normal | Recovery | Injections | Score evidence |",
        "| ---: | --- | ---: | --- | --- | --- | --- | ---: | --- |",
    ]
    for row in roster["tasks"]:
        ref = row["reference"]
        lines.append(
            f"| {row['task_id']} | {row['site']} | {row['template_id']} | {row['difficulty']} | "
            f"`{row['endpoint']}` | {ref['normal']['steps']} turns, "
            f"{'pass' if ref['normal']['official_success'] else 'FAIL'} | "
            f"{ref['recovery']['steps']} turns, release={ref['recovery']['released']}, "
            f"{'pass' if ref['recovery']['official_success'] else 'FAIL'} | "
            f"{ref['trigger']['injections']} | `{ref['normal']['eval']}`; `{ref['recovery']['eval']}` |")
    lines += [
        "", f"Passed: **{sum(1 for row in roster['tasks'] if row['reference']['passed'])}/24**.",
        "", "## Sensitivity analysis", "",
        f"- Excluded from sensitivity only: {', '.join(map(str, roster['fault_pilot_ids']))}",
        f"- Sensitivity denominator: {len(roster['sensitivity_included_ids'])} tasks.",
        "- All three retained tasks are marked `fault_pilot` in `roster.json` and the plan.",
    ]
    (OUT / "acceptance.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def sync_analysis_doc(plan: dict) -> None:
    """Keep the frozen analysis record aligned with the regenerated plan."""
    data = load(ANALYSIS_DOC)
    data["source_commit"] = plan["code"]["head"]
    data["frozen_plan_sha256"] = plan["plan_sha256"]
    data["frozen_at"] = plan["created_at"]
    dump(ANALYSIS_DOC, data)
    dump(ANALYSIS_COPY, data)


def run_check() -> dict:
    command = [sys.executable, "scripts/agent_stress_plan_runner.py",
               "--plan", str(PLAN_PATH), "--run-root",
               str(OUT / "run"), "--check"]
    proc = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {"ok": False, "blockers": ["check output was not JSON"],
                   "stdout": proc.stdout, "stderr": proc.stderr}
    payload["command"] = " ".join(command)
    payload["exit_code"] = proc.returncode
    return payload


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUT)
    args = parser.parse_args(argv)
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)

    consolidate_reference()
    roster = build_roster()
    dump(out / "roster.json", roster)
    write_acceptance_markdown(roster)

    plan = build_plan(roster)
    dump(PLAN_PATH, plan)
    dump(out / "authorization.candidate.json", build_authorization(plan))
    sync_analysis_doc(plan)

    check = run_check()
    dump(out / "check.json", check)

    freeze = {
        "schema": "agent_stress_formal_freeze_record/1",
        "frozen_at": now(),
        "source_commit": git_head(),
        "stage": "formal-matrix-frozen-readiness",
        "decision": "ready_for_authorization_review" if check.get("ok") else "blocked",
        "roster": {
            "path": "roster.json",
            "sha256": file_sha(out / "roster.json"),
            "tasks": 24,
            "templates": roster["distributions"]["templates"],
            "difficulty": roster["distributions"]["difficulty"],
            "supplements": roster["supplement_ids"],
            "fault_pilot": roster["fault_pilot_ids"],
        },
        "reference_acceptance": {
            "path": "reference/acceptance.json",
            "sha256": file_sha(REFERENCE / "acceptance.json"),
            "passed": sum(1 for row in roster["tasks"] if row["reference"]["passed"]),
            "total": 24,
        },
        "plan": {
            "path": "plan.json",
            "sha256": plan["plan_sha256"],
            "cells": plan["planned_trials"],
            "blocks": plan["planned_blocks"],
            "seeds": list(SEEDS),
            "max_steps": MAX_STEPS,
            "window_steps": WINDOW_STEPS,
        },
        "check": {
            "path": "check.json",
            "ok": bool(check.get("ok")),
            "blockers": check.get("blockers") or [],
        },
        "analysis": {
            "plan": "analysis_plan.json",
            "plan_sha256": file_sha(ANALYSIS_COPY),
            "spec": "docs/agent_stress_v1/formal_matrix_analysis_20261123.json",
            "spec_sha256": file_sha(ANALYSIS_DOC),
            "legacy_spec": "docs/agent_stress_v1/analysis_spec.json",
            "legacy_spec_sha256": file_sha(ROOT / "docs/agent_stress_v1/analysis_spec.json"),
            "primary": "official success; sustained minus control",
            "interaction": "fault effect difference between models and architectures",
            "itt": "all allocated legal trials retained, including non-trigger and detours",
            "clustering": "template-level cluster bootstrap",
            "sensitivity": "exclude 28, 228 and 314",
        },
        "usage_estimate": usage_estimate(),
        "blockers": check.get("blockers") or [],
        "note": "No agent trial was started; authorization remains a candidate.",
    }
    dump(out / "freeze.json", freeze)
    write_readme(roster, plan, check)

    print(json.dumps({
        "roster": len(roster["tasks"]),
        "reference_passed": freeze["reference_acceptance"]["passed"],
        "plan_cells": plan["planned_trials"],
        "plan_blocks": plan["planned_blocks"],
        "plan_sha256": plan["plan_sha256"],
        "check_ok": check.get("ok"),
        "blockers": check.get("blockers") or [],
        "estimated_prompt_tokens": freeze["usage_estimate"]["estimated_prompt_tokens"],
    }, ensure_ascii=False, indent=2))
    return 0 if check.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
