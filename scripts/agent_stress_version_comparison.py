#!/usr/bin/env python3
"""Run the 72-trial contemporaneous baseline/improved comparison.

Scope (frozen design):
- 6 development tasks: 21, 126, 132, 169, 231, 281
- 2 architectures: react, plan_execute
- 3 paired seeds: 1, 2, 3
- 2 versions: baseline (improvement flags off), improved (flags on)
- clean condition, 20-step budget, qwen38_flash
= 6 x 2 x 3 x 2 = 72 trials

Both versions use the same code commit and the same Playwright storage states.
The two versions of a pair run back-to-back in a randomized pair order.

Usage:
    python scripts/agent_stress_version_comparison.py --plan-only
    python scripts/agent_stress_version_comparison.py --limit 2
    python scripts/agent_stress_version_comparison.py --resume
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from standard_agent.webarena_evaluator import evaluate_task_safe
from standard_agent.webarena_verified import load_task_definition

EXPERIMENT = "agent-stress-v1-version-comparison"
TASKS = (21, 126, 132, 169, 231, 281)
ARCHITECTURES = ("react", "plan_execute")
SEEDS = (1, 2, 3)
VERSIONS = ("baseline", "improved")
MODEL_PROFILE = "qwen38_flash"
MAX_STEPS = 20
SCHEDULE_SEED = 20261112
BASE = ROOT / "experiments" / "agent_stress_v1" / "version_comparison_20261112"
AUTH_DIR = ROOT / "experiments" / "auth"
DATASET = Path(os.getenv("WEBARENA_DATASET", "/data/webarena/webarena-dataset/webarena-verified.json"))
CONFIG = ROOT / "experiments" / "webarena_local_config.local.json"
FREEZE = ROOT / "docs" / "agent_stress_v1" / "shared_execution_improvements.json"
LOGIN = ROOT / "experiments" / "agent_stress_v1" / "login_condition_20261112.json"
SITE_BASE = {"shopping": "http://localhost:7770", "gitlab": "http://localhost:8023"}
load_dotenv(ROOT / ".env")

FREEZE_FILES = (
    "main.py",
    "standard_agent/submission_contract.py",
    "standard_agent/core/nodes.py",
    "standard_agent/core/state.py",
    "standard_agent/environment/webarena_config.py",
    "scripts/agent_stress_reference_paths.py",
    "tests/test_submission_contract.py",
)


def digest(data) -> str:
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False,
                   separators=(",", ":")).encode()
    ).hexdigest()


def file_sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def git_head() -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                                   text=True).strip()


def code_fingerprint() -> str:
    return digest({path: file_sha(ROOT / path) for path in FREEZE_FILES})


def verify_freeze() -> tuple[dict, str]:
    manifest = load(FREEZE)
    if manifest.get("status") != "frozen_for_72_trial_block":
        raise ValueError(f"freeze manifest status is {manifest.get('status')!r}")
    fingerprint = code_fingerprint()
    if fingerprint != manifest["code"]["fingerprint_sha256"]:
        raise ValueError("working-tree code fingerprint differs from the freeze manifest")
    if manifest.get("freeze_commit") != manifest["code"]["head"]:
        raise ValueError("freeze commit and code head disagree")
    if not (ROOT / "experiments" / "auth" / "shopping.json").is_file():
        raise FileNotFoundError("missing shopping storage state")
    if not (ROOT / "experiments" / "auth" / "gitlab.json").is_file():
        raise FileNotFoundError("missing gitlab storage state")
    return manifest, fingerprint


def verify_login_condition() -> dict:
    manifest = load(LOGIN)
    states = {item["site"]: item for item in manifest["states"]}
    for site in ("shopping", "gitlab"):
        state = states[site]
        path = ROOT / state["state_path"]
        if not path.is_file():
            raise FileNotFoundError(path)
        if file_sha(path) != state["state_sha256"]:
            raise ValueError(f"{site} storage state hash changed since verification")
    return manifest


def build_plan() -> dict:
    tasks = {}
    for task_id in TASKS:
        task = load_task_definition(task_id, path=DATASET)
        site = task["sites"][0]
        if site not in SITE_BASE:
            raise ValueError(f"task {task_id}: unsupported site {site}")
        start_url = task["start_urls"][0]
        start_url = start_url.replace(f"__{site.upper()}__", SITE_BASE[site])
        tasks[str(task_id)] = {
            "task_id": task_id, "site": site, "start_url": start_url,
            "intent": task["intent"], "intent_template_id": task["intent_template_id"],
            "results_schema": (task.get("eval") or [{}])[0].get("results_schema"),
        }
    pairs = []
    for task_id in TASKS:
        for architecture in ARCHITECTURES:
            for seed in SEEDS:
                pairs.append({
                    "pair_id": f"task{task_id}_{architecture}_seed{seed}",
                    "task_id": task_id, "architecture": architecture, "seed": seed,
                })
    random.Random(SCHEDULE_SEED).shuffle(pairs)
    cells = []
    for index, pair in enumerate(pairs):
        order = list(VERSIONS) if index % 2 == 0 else list(reversed(VERSIONS))
        for version in order:
            cells.append({
                **pair,
                "version": version,
                "cell_key": f"{pair['pair_id']}_{version}",
                "output_dir": str(Path("experiments/agent_stress_v1/version_comparison_20261112")
                                  / "cells" / f"{pair['pair_id']}_{version}"),
            })
    login = verify_login_condition()
    storage = {item["site"]: item["state_sha256"] for item in login["states"]}
    manifest, fingerprint = verify_freeze()
    plan = {
        "schema": "agent_stress_version_comparison_plan/1",
        "experiment_id": EXPERIMENT,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model_profile": MODEL_PROFILE,
        "max_steps": MAX_STEPS,
        "schedule_seed": SCHEDULE_SEED,
        "tasks": tasks,
        "pairs": pairs,
        "cells": cells,
        "planned_trials": len(cells),
        "versions": {
            "baseline": {"SUBMISSION_CONTRACT": "0", "RETRIEVAL_REPAIR": "0"},
            "improved": {"SUBMISSION_CONTRACT": "1", "RETRIEVAL_REPAIR": "1"},
        },
        "storage_state_sha256": storage,
        "storage_state_dir": str(AUTH_DIR.relative_to(ROOT)),
        "code": {
            "run_commit": git_head(),
            "freeze_commit": manifest["freeze_commit"],
            "fingerprint_sha256": fingerprint,
        },
        "dataset_sha256": file_sha(DATASET),
        "faults_enabled": False,
        "conditions": ["control"],
    }
    plan["plan_sha256"] = digest(plan)
    return plan


def command(cell: dict, plan: dict) -> list[str]:
    task = plan["tasks"][str(cell["task_id"])]
    args = [
        sys.executable, "main.py", "--benchmark", "--condition", "control",
        "--site", task["site"], "--url", task["start_url"],
        "--task", task["intent"], "--task-id", str(cell["task_id"]),
        "--model-profile", MODEL_PROFILE, "--architecture", cell["architecture"],
        "--fault-seed", str(cell["seed"]), "--trials", "1",
        "--max-steps", str(MAX_STEPS),
        "--webarena-output-dir", str(ROOT / cell["output_dir"] / "outputs"),
        "--storage-state-dir", str(AUTH_DIR),
    ]
    if cell["version"] == "improved":
        args.append("--submission-contract")
    return args


def trial_env(cell: dict) -> dict:
    env = dict(os.environ, WEBARENA_DATASET=str(DATASET), WEBARENA_HOST="localhost")
    flags = {"baseline": {"SUBMISSION_CONTRACT": "0", "RETRIEVAL_REPAIR": "0"},
             "improved": {"SUBMISSION_CONTRACT": "1", "RETRIEVAL_REPAIR": "1"}}[cell["version"]]
    env.update(flags)
    return env


def arm_dir(cell: dict) -> Path:
    return (ROOT / cell["output_dir"] / "outputs" / str(cell["task_id"])
            / f"control_seed_{cell['seed']}")


def trace_prompt_tokens(trace: Path) -> int:
    if not trace.is_file():
        return 0
    total = 0
    for line in trace.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get("event") == "llm_provenance":
            total += int(event.get("prompt_tokens") or 0)
    return total


def evaluate_cell(cell: dict, plan: dict) -> dict:
    arm = arm_dir(cell)
    record = load(arm / "trial_record.json")
    response = arm / "agent_response.json"
    har = arm / "network.har"
    evaluation = evaluate_task_safe(
        cell["task_id"], agent_response_path=response,
        network_trace_path=har if har.exists() else None, config_path=CONFIG,
    )
    evaluation["_evaluation_binding"] = {
        "agent_response_sha256": file_sha(response),
        "network_har_sha256": file_sha(har) if har.exists() else None,
        "config_sha256": file_sha(CONFIG),
        "evaluator_adapter_version": "stage-c-eval-adapter/1",
    }
    (arm / "official_eval.json").write_text(
        json.dumps(evaluation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    run_config = record.get("run_config") or {}
    expected_flag = cell["version"] == "improved"
    storage_sha = plan["storage_state_sha256"][plan["tasks"][str(cell["task_id"])]["site"]]
    if bool(run_config.get("submission_contract")) != expected_flag:
        raise ValueError(f"{cell['cell_key']}: submission_contract flag mismatch")
    if bool(run_config.get("retrieval_repair")) != expected_flag:
        raise ValueError(f"{cell['cell_key']}: retrieval_repair flag mismatch")
    if run_config.get("storage_state_sha256") != storage_sha:
        raise ValueError(f"{cell['cell_key']}: storage state hash mismatch")
    trace = Path(record["paths"]["trace"])
    if not trace.is_absolute():
        trace = ROOT / trace
    return {
        "cell_key": cell["cell_key"], "pair_id": cell["pair_id"],
        "task_id": cell["task_id"], "architecture": cell["architecture"],
        "seed": cell["seed"], "version": cell["version"],
        "steps": record.get("steps"), "completed": record.get("completed"),
        "official_success": bool(evaluation.get("official_success")),
        "official_status": evaluation.get("status"), "official_score": evaluation.get("score"),
        "answer": json.loads(response.read_text(encoding="utf-8")).get("retrieved_data"),
        "prompt_tokens": trace_prompt_tokens(trace),
        "time_sec": record.get("time_sec"),
        "submission_contract": run_config.get("submission_contract"),
        "retrieval_repair": run_config.get("retrieval_repair"),
        "storage_state_sha256": run_config.get("storage_state_sha256"),
        "trace": str(trace), "agent_response": str(response),
        "network_har": str(har), "official_eval": str(arm / "official_eval.json"),
    }


def summarize(results: list[dict]) -> dict:
    def rate(rows):
        return (sum(1 for row in rows if row["official_success"]) / len(rows)) if rows else None
    by_version = {}
    for version in VERSIONS:
        rows = [row for row in results if row["version"] == version]
        by_version[version] = {
            "trials": len(rows), "successes": sum(1 for row in rows if row["official_success"]),
            "success_rate": rate(rows),
            "steps_mean": (sum(row["steps"] or 0 for row in rows) / len(rows)) if rows else None,
            "prompt_tokens": sum(row["prompt_tokens"] for row in rows),
        }
    by_task = {}
    for task_id in TASKS:
        by_task[str(task_id)] = {}
        for version in VERSIONS:
            rows = [row for row in results if row["task_id"] == task_id and row["version"] == version]
            by_task[str(task_id)][version] = {
                "trials": len(rows),
                "successes": sum(1 for row in rows if row["official_success"]),
                "success_rate": rate(rows),
            }
    pairs = {}
    for row in results:
        pairs.setdefault(row["pair_id"], {})[row["version"]] = row
    paired = []
    for pair_id, arms in sorted(pairs.items()):
        if set(arms) != set(VERSIONS):
            continue
        effect = int(arms["improved"]["official_success"]) - int(arms["baseline"]["official_success"])
        paired.append({"pair_id": pair_id, "task_id": arms["baseline"]["task_id"],
                       "architecture": arms["baseline"]["architecture"],
                       "seed": arms["baseline"]["seed"], "effect": effect})
    return {
        "trials": len(results), "by_version": by_version, "by_task": by_task,
        "paired_effects": paired,
        "pairs_improved": sum(1 for row in paired if row["effect"] > 0),
        "pairs_baseline": sum(1 for row in paired if row["effect"] < 0),
        "pairs_tied": sum(1 for row in paired if row["effect"] == 0),
        "mean_paired_effect": (sum(row["effect"] for row in paired) / len(paired)) if paired else None,
    }


def preflight(plan: dict) -> dict:
    checks = {"shopping": False, "gitlab": False, "model_key": bool(
        os.getenv("MODEL_QWEN38_FLASH_API_KEY"))}
    for site, url in SITE_BASE.items():
        try:
            with urlopen(url, timeout=5) as response:
                checks[site] = 200 <= response.status < 500
        except Exception:
            checks[site] = False
    checks["freeze_fingerprint"] = plan["code"]["fingerprint_sha256"] == code_fingerprint()
    checks["storage_states"] = bool(plan["storage_state_sha256"])
    checks["ok"] = all(checks.values())
    return checks


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--limit", type=int, help="Run only the first N cells (for a smoke check)")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)

    plan = build_plan()
    checks = preflight(plan)
    if not checks["ok"]:
        raise SystemExit(f"preflight failed: {checks}")
    if BASE.exists() and not args.resume:
        raise SystemExit(f"output root already exists: {BASE}")
    BASE.mkdir(parents=True, exist_ok=True)
    (BASE / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
                                    encoding="utf-8")
    (BASE / "preflight.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2) + "\n",
                                         encoding="utf-8")
    if args.plan_only:
        print(f"plan: {plan['planned_trials']} trials, sha256={plan['plan_sha256']}")
        print(json.dumps(checks, ensure_ascii=False))
        return 0

    progress_path = BASE / "progress.json"
    completed = {}
    if args.resume and progress_path.exists():
        progress = load(progress_path)
        completed = {row["cell_key"]: row for row in progress.get("results", [])}
        print(f"resume: {len(completed)} cells already recorded")
    results = list(completed.values())
    failures = []
    cells = plan["cells"][:args.limit] if args.limit else plan["cells"]
    print(f"running {len(cells)} cells; planned total {plan['planned_trials']}", flush=True)

    for index, cell in enumerate(cells, 1):
        if cell["cell_key"] in completed:
            continue
        cell_dir = ROOT / cell["output_dir"]
        cell_dir.mkdir(parents=True, exist_ok=True)
        log_path = cell_dir / "trial.log"
        print(f"[{index}/{len(cells)}] {cell['cell_key']}", flush=True)
        try:
            with log_path.open("w", encoding="utf-8") as log:
                proc = subprocess.run(
                    command(cell, plan), cwd=ROOT, env=trial_env(cell),
                    stdout=log, stderr=subprocess.STDOUT, timeout=600,
                )
            if proc.returncode != 0:
                raise RuntimeError(f"main.py exited {proc.returncode}")
            arm = arm_dir(cell)
            for required in ("trial_record.json", "agent_response.json"):
                if not (arm / required).is_file():
                    raise FileNotFoundError(arm / required)
            result = evaluate_cell(cell, plan)
            results.append(result)
            print(f"    success={result['official_success']} steps={result['steps']} "
                  f"tokens={result['prompt_tokens']}", flush=True)
        except Exception as exc:
            failures.append({"cell_key": cell["cell_key"], "error": str(exc)})
            (cell_dir / "failure.json").write_text(
                json.dumps({"cell_key": cell["cell_key"], "error": str(exc)},
                           ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"    FAILED: {exc}", flush=True)
            break
        finally:
            progress_path.write_text(json.dumps({
                "experiment_id": EXPERIMENT,
                "plan_sha256": plan["plan_sha256"],
                "completed": len(results),
                "planned": plan["planned_trials"],
                "failures": failures,
                "results": results,
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        time.sleep(1)

    summary = summarize(results)
    summary.update({
        "schema": "agent_stress_version_comparison_summary/1",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "experiment_id": EXPERIMENT,
        "plan_sha256": plan["plan_sha256"],
        "code": plan["code"],
        "model_profile": MODEL_PROFILE,
        "failures": failures,
        "complete": len(results) == plan["planned_trials"],
        "results": results,
    })
    (BASE / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                                       encoding="utf-8")
    print(json.dumps({key: summary[key] for key in (
        "trials", "by_version", "pairs_improved", "pairs_baseline", "pairs_tied",
        "mean_paired_effect", "complete", "failures")}, ensure_ascii=False, indent=2))
    return 0 if not failures and len(results) == plan["planned_trials"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
