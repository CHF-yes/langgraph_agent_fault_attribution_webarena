#!/usr/bin/env python3
"""Offline plan generators for the next Agent Stress batches.

Stages (plan only; no model calls, no browser trials):
- ``http-acceptance``: 1 task x 2 models x 2 architectures x 3 conditions = 12.
  Task 118, service-level HTTP fault, 10-step sustained window, 20-step budget.
- ``screening-480``: 40 holdout candidates x 2 models x 2 architectures x 3 seeds
  = 480 clean trials with the frozen improved execution version.
- ``screening-qwen-240``: the 240 Qwen cells in the same relative order.

The plans bind the task definitions, dataset/evaluator, shared login states,
model identities and the current code fingerprint, and are written to a fresh
directory. Execution is deliberately not implemented here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from fault_injection.http_service import ServiceEndpoint
from standard_agent.stage_c_pipeline import current_evaluator_version
from standard_agent.webarena_verified import load_task_definition

load_dotenv(ROOT / ".env")

DATASET = Path(os.getenv("WEBARENA_DATASET", "/data/webarena/webarena-dataset/webarena-verified.json"))
CONFIG = ROOT / "experiments" / "webarena_local_config.local.json"
CONTRACTS = ROOT / "docs" / "agent_stress_v1" / "task_contracts.json"
LOGIN = ROOT / "experiments" / "agent_stress_v1" / "login_condition_20261112.json"
AUTH_DIR = ROOT / "experiments" / "auth"
EVIDENCE = ROOT / "experiments" / "agent_stress_v1" / "execution_prep_20261116"
PROVENANCE = EVIDENCE / "model_provenance.json"
MODEL_PROFILES = ("qwen38_flash", "deepseek_v41_flash")
QWEN_SCREENING_SCHEMA = "agent_stress_screening_qwen_plan/1"
ARCHITECTURES = ("react", "plan_execute")
PORTS = {"shopping": 7770, "gitlab": 8023, "reddit": 9999}
ENDPOINT = "http://localhost:7770/catalogsearch/result"
MAX_STEPS = 20
WINDOW_STEPS = 10
SCHEDULE_SEED = 20261116
CONDITIONS = (
    ("control", "control", "single", "off"),
    ("single", "fault", "single", "high"),
    ("sustained", "fault", "persistent", "high"),
)
SWITCHES = {"SUBMISSION_CONTRACT": "1", "RETRIEVAL_REPAIR": "1"}


def digest(data) -> str:
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False,
                   separators=(",", ":")).encode()
    ).hexdigest()


def file_sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git_head() -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                                   text=True).strip()


def code_fingerprint() -> str:
    """Fingerprint the committed code under test, not untracked run artifacts.

    ``git ls-files`` keeps the fingerprint reproducible from a clean checkout:
    untracked experiment helpers must not silently change the plan's code
    binding.  Tracked content is hashed from disk, so uncommitted edits are
    still visible to the preflight gate.
    """
    tracked = subprocess.check_output(
        ["git", "-C", str(ROOT), "ls-files", "--", "main.py", "run_baseline.py",
         "requirements.txt", "standard_agent", "fault_injection", "scripts"],
        text=True).splitlines()
    paths = [ROOT / rel for rel in tracked]
    paths = [path for path in paths if path.is_file()
             and (path.suffix == ".py" or path.name in {"requirements.txt"})]
    return digest({str(path.relative_to(ROOT)): file_sha(path) for path in sorted(paths)})


def login_states() -> dict:
    manifest = json.loads(LOGIN.read_text(encoding="utf-8"))
    states = {}
    for item in manifest["states"]:
        path = ROOT / item["state_path"]
        if not path.is_file():
            raise FileNotFoundError(path)
        if file_sha(path) != item["state_sha256"]:
            raise ValueError(f"{item['site']} storage state hash changed")
        states[item["site"]] = item["state_sha256"]
    return states


def model_identity(profiles=MODEL_PROFILES, provenance: Path | None = None) -> dict:
    report_path = Path(provenance) if provenance else PROVENANCE
    report = json.loads(report_path.read_text(encoding="utf-8"))
    by_profile = {item["profile"]: item for item in report}
    identities = {}
    for profile in profiles:
        item = by_profile.get(profile)
        if item is None or not item.get("verdict_ok"):
            raise ValueError(f"{profile}: provenance report is missing or not OK")
        runs = item.get("runs") or []
        identities[profile] = {
            "requested_model": item["requested_model"],
            "served_models": sorted({run["served_model"] for run in runs if run.get("served_model")}),
            "system_fingerprints": sorted({run["system_fingerprint"] for run in runs
                                           if run.get("system_fingerprint")}),
            "probe_report": str(report_path.relative_to(ROOT)),
            "probe_report_sha256": file_sha(report_path),
        }
    return identities


def task_row(task_id: int) -> dict:
    task = load_task_definition(task_id, path=DATASET)
    site = (task.get("sites") or [None])[0]
    if site not in PORTS:
        raise ValueError(f"task {task_id}: unsupported site {site!r}")
    start_url = (task.get("start_urls") or [None])[0]
    if not start_url:
        raise ValueError(f"task {task_id}: missing start URL")
    start_url = start_url.replace(f"__{site.upper()}__", f"http://localhost:{PORTS[site]}")
    parsed = ServiceEndpoint.from_url(start_url) if site == "shopping" else None
    if parsed is not None and parsed.host != "localhost":
        raise ValueError(f"task {task_id}: start URL host mismatch")
    return {
        "task_id": task_id,
        "site": site,
        "start_url": start_url,
        "intent": task["intent"],
        "intent_template_id": task["intent_template_id"],
        "results_schema": (task.get("eval") or [{}])[0].get("results_schema"),
    }


def holdout_candidates() -> list[dict]:
    contracts = json.loads(CONTRACTS.read_text(encoding="utf-8"))
    candidates = [item for item in contracts["tasks"]
                  if item.get("split_role") == "holdout_candidate"]
    if len(candidates) != 40:
        raise ValueError(f"expected 40 holdout candidates, got {len(candidates)}")
    if len({item["task_id"] for item in candidates}) != 40:
        raise ValueError("duplicate holdout task ids")
    rows = []
    for item in candidates:
        task = task_row(item["task_id"])
        if (task["site"] != item["site"]
                or task["intent_template_id"] != item["intent_template_id"]):
            raise ValueError(f"task {item['task_id']}: contract/dataset identity mismatch")
        rows.append(task)
    return rows


def storage_flags() -> list[str]:
    return ["--storage-state-dir", str(AUTH_DIR)]


def improved_flags() -> list[str]:
    return ["--submission-contract"]


def trial_env() -> dict:
    return dict(os.environ, WEBARENA_DATASET=str(DATASET), WEBARENA_HOST="localhost", **SWITCHES)


def python_cmd() -> str:
    return sys.executable


def build_http_plan(out: Path) -> dict:
    task = task_row(118)
    endpoint = ServiceEndpoint.from_url(ENDPOINT)
    blocks = []
    for profile in MODEL_PROFILES:
        for architecture in ARCHITECTURES:
            blocks.append((profile, architecture))
    random.Random(SCHEDULE_SEED).shuffle(blocks)
    cells = []
    for block_index, (profile, architecture) in enumerate(blocks, 1):
        for order, (condition, run_condition, variant, intensity) in enumerate(CONDITIONS, 1):
            key = f"{profile}_{architecture}_{condition}"
            output_root = out / "cells" / key / "outputs"
            arm = output_root / f"web_http_error_task118_seed1" / "118" / (
                "control_seed_1" if condition == "control"
                else "web_http_error_seed_1")
            command = [
                python_cmd(), "main.py", "--benchmark",
                "--site", task["site"], "--url", task["start_url"],
                "--task", task["intent"], "--task-id", "118",
                "--model-profile", profile, "--architecture", architecture,
                "--max-steps", str(MAX_STEPS), "--trials", "1",
                "--fault-type", "web_http_error", "--fault-intensity", intensity,
                "--fault-seed", "1", "--experiment-id", "ep-http-service-v1",
                "--fault-variant", variant, "--fault-window-steps", str(WINDOW_STEPS),
                "--fault-target-endpoint", ENDPOINT,
                "--condition", run_condition,
                "--webarena-output-dir", str(output_root / "web_http_error_task118_seed1"),
                *storage_flags(), *improved_flags(),
            ]
            cells.append({
                "cell_key": key,
                "block": block_index,
                "order_in_block": order,
                "model_profile": profile,
                "architecture": architecture,
                "condition": condition,
                "fault_variant": variant,
                "fault_intensity": intensity,
                "fault_enabled": condition != "control",
                "fault_seed": 1,
                "experiment_id": "ep-http-service-v1",
                "fault_type": "web_http_error",
                "max_steps": MAX_STEPS,
                "window_steps": WINDOW_STEPS if condition != "control" else None,
                "output_dir": str((out / "cells" / key).relative_to(ROOT)),
                "arm_dir": str(arm.relative_to(ROOT)),
                "command": command,
            })
    plan = {
        "schema": "agent_stress_http_acceptance_plan/1",
        "experiment_id": "agent-stress-http-acceptance-v1",
        "protocol_experiment_id": "ep-http-service-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "plan_only",
        "approved_for_paid_runs": False,
        "task": task,
        "endpoint": endpoint.spec(),
        "conditions": [item[0] for item in CONDITIONS],
        "condition_order": "fixed within each block: control, single, sustained",
        "window_steps": WINDOW_STEPS,
        "window_definition": "first exposure plus the following 9 steps",
        "max_steps": MAX_STEPS,
        "model_profiles": list(MODEL_PROFILES),
        "architectures": list(ARCHITECTURES),
        "blocks": [{"block": i, "model_profile": p, "architecture": a}
                   for i, (p, a) in enumerate(blocks, 1)],
        "cells": cells,
        "planned_trials": len(cells),
        "switches": SWITCHES,
        "storage_state_sha256": {item["site"]: item["state_sha256"]
                                 for item in json.loads(LOGIN.read_text())["states"]},
        "storage_state_dir": str(AUTH_DIR.relative_to(ROOT)),
        "model_identity": model_identity(),
        "code": {"head": git_head(), "fingerprint_sha256": code_fingerprint()},
        "dataset_path": str(DATASET),
        "dataset_sha256": file_sha(DATASET),
        "evaluator": current_evaluator_version(),
        "faults_enabled": True,
    }
    plan["plan_sha256"] = digest(plan)
    return plan


def build_screening_plan(out: Path, *, qwen_only: bool = False) -> dict:
    tasks = holdout_candidates()
    profiles = MODEL_PROFILES[:1] if qwen_only else MODEL_PROFILES
    combos = [(profile, architecture) for profile in MODEL_PROFILES
              for architecture in ARCHITECTURES]
    cells = []
    for task in tasks:
        for profile, architecture in combos:
            for seed in (1, 2, 3):
                key = f"{profile}_task{task['task_id']}_{architecture}_seed{seed}"
                output_root = out / "cells" / key / "outputs"
                arm = output_root / str(task["task_id"]) / f"control_seed_{seed}"
                command = [
                    python_cmd(), "main.py", "--benchmark", "--condition", "control",
                    "--site", task["site"], "--url", task["start_url"],
                    "--task", task["intent"], "--task-id", str(task["task_id"]),
                    "--model-profile", profile, "--architecture", architecture,
                    "--fault-seed", str(seed), "--trials", "1",
                    "--max-steps", str(MAX_STEPS),
                    "--webarena-output-dir", str(output_root),
                    *storage_flags(), *improved_flags(),
                ]
                cells.append({
                    "cell_key": key,
                    "task_id": task["task_id"],
                    "site": task["site"],
                    "start_url": task["start_url"],
                    "intent": task["intent"],
                    "model_profile": profile,
                    "architecture": architecture,
                    "seed": seed,
                    "max_steps": MAX_STEPS,
                    "condition": "control",
                    "experiment_id": "stage-c",
                    "output_dir": str((out / "cells" / key).relative_to(ROOT)),
                    "arm_dir": str(arm.relative_to(ROOT)),
                    "command": command,
                })
    random.Random(SCHEDULE_SEED).shuffle(cells)
    if qwen_only:
        cells = [cell for cell in cells if cell["model_profile"] in profiles]
    plan = {
        "schema": QWEN_SCREENING_SCHEMA if qwen_only else "agent_stress_screening_plan/3",
        "experiment_id": "agent-stress-v1-screening-qwen-240" if qwen_only else "agent-stress-v1-screening-480",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "plan_only",
        "approved_for_paid_runs": False,
        "stage": "qwen-first-screening" if qwen_only else "four-combination-screening",
        "candidate_source": str(CONTRACTS.relative_to(ROOT)),
        "candidate_count": len(tasks),
        "candidate_ids": [task["task_id"] for task in tasks],
        "tasks": {str(task["task_id"]): task for task in tasks},
        "model_profiles": list(profiles),
        "architectures": list(ARCHITECTURES),
        "seeds": [1, 2, 3],
        "max_steps": MAX_STEPS,
        "cells": cells,
        "planned_trials": len(cells),
        "eligibility_rule": (
            "both Qwen architectures need >= 2/3 clean successes; DeepSeek eligibility remains pending"
            if qwen_only else "each model x architecture combination needs >= 2/3 clean successes on a task"
        ),
        "selection_rule": (
            "report the Qwen shortlist and template coverage; no final enrollment"
            if qwen_only else "24 tasks, 12 templates, at most 2 tasks per template; frozen before fault results"
        ),
        "switches": SWITCHES,
        "storage_state_sha256": {item["site"]: item["state_sha256"]
                                 for item in json.loads(LOGIN.read_text())["states"]},
        "storage_state_dir": str(AUTH_DIR.relative_to(ROOT)),
        "model_identity": model_identity(profiles),
        "code": {"head": git_head(), "fingerprint_sha256": code_fingerprint()},
        "dataset_path": str(DATASET),
        "dataset_sha256": file_sha(DATASET),
        "evaluator": current_evaluator_version(),
        "faults_enabled": False,
    }
    plan["plan_sha256"] = digest(plan)
    return plan


def preflight(plan: dict) -> dict:
    checks = {
        "dataset_bound": plan["dataset_sha256"] == file_sha(DATASET),
        "dataset_path": str(DATASET),
        "code_fingerprint": plan["code"]["fingerprint_sha256"] == code_fingerprint(),
        "evaluator_config_readable": CONFIG.is_file(),
        "storage_states": all(
            (ROOT / "experiments" / "auth" / f"{site}.json").is_file()
            for site in plan["storage_state_sha256"]),
        "model_identities": all(
            item["served_models"] for item in plan["model_identity"].values()),
        "paid_trials_executed": 0,
    }
    try:
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        checks["evaluator_dataset_binding"] = Path(config["test_data_file"]).resolve() == DATASET.resolve()
        checks["evaluator_sites"] = True
    except (OSError, KeyError, ValueError, TypeError):
        checks["evaluator_dataset_binding"] = False
        checks["evaluator_sites"] = False
    for site, port in PORTS.items():
        if site not in {task["site"] for task in ([plan["task"]] if "task" in plan
                                                 else plan["tasks"].values())}:
            continue
        try:
            with urllib.request.urlopen(f"http://localhost:{port}/", timeout=5) as response:
                checks[f"site:{site}"] = 200 <= response.status < 500
        except (urllib.error.URLError, OSError):
            checks[f"site:{site}"] = False
    if "endpoint" in plan:
        try:
            with urllib.request.urlopen(plan["endpoint"]["scheme"] + "://"
                                        + plan["endpoint"]["host"] + ":"
                                        + str(plan["endpoint"]["port"]) + "/", timeout=5) as response:
                checks["endpoint_host_reachable"] = 200 <= response.status < 500
        except (urllib.error.URLError, OSError):
            checks["endpoint_host_reachable"] = False
    for profile in plan["model_profiles"]:
        key = profile.upper().replace("-", "_")
        checks[f"model:{profile}"] = bool(os.getenv(f"MODEL_{key}_API_KEY"))
    checks["ok"] = all(value is not False for value in checks.values())
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["http-acceptance", "screening-480", "screening-qwen-240"])
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if (out / "plan.json").exists():
        raise SystemExit(f"refusing to overwrite existing plan: {out / 'plan.json'}")
    plan = (build_http_plan(out) if args.stage == "http-acceptance"
            else build_screening_plan(out, qwen_only=args.stage == "screening-qwen-240"))
    checks = preflight(plan)
    (out / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")
    (out / "preflight.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2) + "\n",
                                        encoding="utf-8")
    if args.stage == "screening-qwen-240":
        candidate = {
            "schema": "agent_stress_batch_authorization/1",
            "experiment_id": plan["experiment_id"],
            "status": "candidate",
            "approved_for_paid_runs": False,
            "plan_path": str((out / "plan.json").relative_to(ROOT)),
            "plan_sha256": plan["plan_sha256"],
            "cells": plan["planned_trials"],
            "cell_keys": [cell["cell_key"] for cell in plan["cells"]],
            "code_fingerprint_sha256": plan["code"]["fingerprint_sha256"],
            "dataset_sha256": plan["dataset_sha256"],
            "prompt_token_stop_before_next_trial": None,
        }
        (out / "authorization.candidate.json").write_text(
            json.dumps(candidate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"stage": args.stage, "planned_trials": plan["planned_trials"],
                      "plan_sha256": plan["plan_sha256"], "preflight_ok": checks["ok"],
                      "out": str(out)}, ensure_ascii=False))
    return 0 if checks["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
