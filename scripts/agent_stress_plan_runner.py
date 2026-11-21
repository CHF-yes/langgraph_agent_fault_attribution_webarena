#!/usr/bin/env python3
"""Execute a frozen Agent Stress plan with validation, resume and archiving.

``--check`` validates the plan, code/config/data/evaluator/model/login bindings
and batch authorization without starting a subprocess.  ``--execute`` runs the
validated cells one at a time, asks the native evaluator to score each response,
and records response/HAR/trace/official-score/usage/run-identity paths.

The runner never trusts a stored command: every cell command is regenerated from
its validated parameters and compared with the plan before execution.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from scripts.agent_stress_next_plans import (
    ARCHITECTURES, MODEL_PROFILES, QWEN_SCREENING_SCHEMA, code_fingerprint,
)
from standard_agent.stage_c_pipeline import classify_evaluation, current_evaluator_version
from standard_agent.webarena_evaluator import evaluate_task_safe

load_dotenv(ROOT / ".env")

CONFIG = ROOT / "experiments" / "webarena_local_config.local.json"
DATASET = Path(os.getenv("WEBARENA_DATASET", "/data/webarena/webarena-dataset/webarena-verified.json"))
AUTH_DIR = ROOT / "experiments" / "auth"
PROVENANCE = ROOT / "experiments" / "agent_stress_v1" / "execution_prep_20261116" / "model_provenance.json"
SWITCHES = {"SUBMISSION_CONTRACT": "1", "RETRIEVAL_REPAIR": "1"}
PORTS = {"shopping": 7770, "gitlab": 8023, "reddit": 9999}
UNIFIED_SCHEMA = "agent_stress_unified_acceptance_plan/1"
EXPECTED = {
    "agent_stress_http_acceptance_plan/1": ("http-acceptance", 12),
    "agent_stress_screening_plan/3": ("screening-480", 480),
    QWEN_SCREENING_SCHEMA: ("screening-qwen-240", 240),
    UNIFIED_SCHEMA: ("unified-acceptance", 36),
}


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def storage_state_dir(plan: dict) -> Path:
    """Resolve the plan's login-state directory, falling back to the legacy one."""
    if plan.get("storage_state_dir"):
        return ROOT / plan["storage_state_dir"]
    return AUTH_DIR


def file_sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def digest(data) -> str:
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False,
                   separators=(",", ":")).encode()
    ).hexdigest()


def plan_digest(plan: dict) -> str:
    return digest({key: value for key, value in plan.items() if key != "plan_sha256"})


def plan_stage(plan: dict) -> tuple[str, int]:
    try:
        return EXPECTED[plan.get("schema")]
    except KeyError:
        raise ValueError(f"unsupported plan schema: {plan.get('schema')!r}")


def expected_command(cell: dict, plan: dict) -> list[str]:
    stage, _ = plan_stage(plan)
    command = [sys.executable, "main.py", "--benchmark"]
    if stage == "http-acceptance":
        command += [
            "--site", plan["task"]["site"], "--url", plan["task"]["start_url"],
            "--task", plan["task"]["intent"], "--task-id", str(plan["task"]["task_id"]),
            "--model-profile", cell["model_profile"],
            "--architecture", cell["architecture"],
            "--max-steps", str(cell["max_steps"]), "--trials", "1",
            "--fault-type", "web_http_error",
            "--fault-intensity", cell["fault_intensity"],
            "--fault-seed", str(cell["fault_seed"]),
            "--experiment-id", "ep-http-service-v1",
            "--fault-variant", cell["fault_variant"],
            "--fault-window-steps", str(plan["window_steps"]),
            "--fault-target-endpoint", plan["endpoint"]["scheme"] + "://"
            + plan["endpoint"]["host"] + ":" + str(plan["endpoint"]["port"])
            + plan["endpoint"]["path"],
            "--condition", "control" if cell["condition"] == "control" else "fault",
            "--webarena-output-dir", str(ROOT / cell["output_dir"] / "outputs"
                                          / "web_http_error_task118_seed1"),
        ]
    elif stage == "unified-acceptance":
        command += [
            "--site", cell["site"], "--url", cell["start_url"],
            "--task", cell["intent"], "--task-id", str(cell["task_id"]),
            "--model-profile", cell["model_profile"],
            "--architecture", cell["architecture"],
            "--max-steps", str(cell["max_steps"]), "--trials", "1",
            "--fault-type", cell["fault_type"],
            "--fault-intensity", cell["fault_intensity"],
            "--fault-seed", str(cell["seed"]),
            "--experiment-id", cell.get("experiment_id",
                                        plan.get("protocol_experiment_id")),
            "--fault-variant", cell["fault_variant"],
            "--fault-window-steps", str(plan["window_steps"]),
            "--fault-target-endpoint", cell["endpoint"],
            "--condition", cell["run_condition"],
            "--webarena-output-dir", str(ROOT / cell["output_dir"] / "outputs"),
            "--storage-state-dir", str(storage_state_dir(plan)),
        ]
    else:
        command += [
            "--condition", "control",
            "--site", cell["site"], "--url", cell["start_url"],
            "--task", cell["intent"], "--task-id", str(cell["task_id"]),
            "--model-profile", cell["model_profile"],
            "--architecture", cell["architecture"],
            "--fault-seed", str(cell["seed"]), "--trials", "1",
            "--max-steps", str(cell["max_steps"]),
            "--webarena-output-dir", str(ROOT / cell["output_dir"] / "outputs"),
        ]
    if stage != "unified-acceptance":
        command += ["--storage-state-dir", str(storage_state_dir(plan))]
    command += ["--submission-contract"]
    return command


def expected_cells(plan: dict) -> list[str]:
    stage, count = plan_stage(plan)
    cells = plan.get("cells") or []
    errors = []
    if len(cells) != count or plan.get("planned_trials") != count:
        errors.append(f"cell count {len(cells)} != {count}")
    keys = [cell.get("cell_key") for cell in cells]
    if len(set(keys)) != len(keys):
        errors.append("duplicate cell keys")
    if stage == "http-acceptance":
        combos = {(c["model_profile"], c["architecture"]) for c in cells}
        conditions = {(c["model_profile"], c["architecture"], c["condition"]) for c in cells}
        if len(combos) != 4:
            errors.append(f"expected 4 model/architecture combos, got {len(combos)}")
        if len(conditions) != 12:
            errors.append(f"expected 12 unique combo/condition cells, got {len(conditions)}")
        if {c["condition"] for c in cells} != {"control", "single", "sustained"}:
            errors.append("conditions must be control/single/sustained")
        if len({c["block"] for c in cells}) != 4:
            errors.append("expected 4 blocks")
        for block in {c["block"] for c in cells}:
            block_cells = [c for c in cells if c["block"] == block]
            if len(block_cells) != 3 or {c["order_in_block"] for c in block_cells} != {1, 2, 3}:
                errors.append(f"block {block} is not 3 ordered conditions")
    elif stage == "unified-acceptance":
        tasks = plan.get("tasks") or {}
        models = plan.get("models") or []
        architectures = plan.get("architectures") or []
        conditions = plan.get("conditions") or []
        seeds = plan.get("seeds") or []
        if set(models) != {"qwen38_flash", "deepseek_v41_flash"}:
            errors.append("unified plan has unexpected model profiles")
        if set(architectures) != {"react", "plan_execute"}:
            errors.append("unified plan has unexpected architectures")
        if set(conditions) != {"control", "single", "sustained"}:
            errors.append("conditions must be control/single/sustained")
        if not tasks or len(tasks) != 3:
            errors.append("unified plan must cover exactly 3 tasks")
        expected = {(int(task_id), model, architecture, condition, seed)
                    for task_id in tasks for model in models
                    for architecture in architectures for condition in conditions
                    for seed in seeds}
        actual = {(int(c["task_id"]), c["model_profile"], c["architecture"],
                   c["condition"], int(c["seed"])) for c in cells}
        if actual != expected:
            errors.append("unified cells differ from the expected task/model/architecture/condition/seed grid")
        blocks = {c["block"] for c in cells}
        if len(blocks) != 12:
            errors.append(f"expected 12 blocks, got {len(blocks)}")
        for block in blocks:
            block_cells = [c for c in cells if c["block"] == block]
            if (len(block_cells) != 3
                    or {c["order_in_block"] for c in block_cells} != {1, 2, 3}
                    or {c["condition"] for c in block_cells} != set(conditions)):
                errors.append(f"block {block} is not one task/model/architecture with all conditions")
                continue
            if len({(c["task_id"], c["model_profile"], c["architecture"])
                    for c in block_cells}) != 1:
                errors.append(f"block {block} mixes task/model/architecture")
        variants = {"control": ("control", "single", "off"),
                    "single": ("fault", "single", "high"),
                    "sustained": ("fault", "persistent", "high")}
        for c in cells:
            if c.get("max_steps") != plan.get("max_steps"):
                errors.append(f"cell {c.get('cell_key')}: max_steps differs from the plan")
            run_condition, variant, intensity = variants[c["condition"]]
            if (c.get("run_condition") != run_condition
                    or c.get("fault_variant") != variant
                    or c.get("fault_intensity") != intensity):
                errors.append(f"cell {c.get('cell_key')}: condition parameters are inconsistent")
            expected_window = None if c["condition"] == "control" else plan.get("window_steps")
            if c.get("window_steps") != expected_window:
                errors.append(f"cell {c.get('cell_key')}: window_steps differs from the condition")
            endpoint = (tasks.get(str(c["task_id"])) or {}).get("endpoint")
            if endpoint and c.get("endpoint") != endpoint:
                errors.append(f"cell {c.get('cell_key')}: endpoint differs from the task")
            if c.get("cell_key") != (f"{c['model_profile']}_task{c['task_id']}_"
                                     f"{c['architecture']}_seed{c['seed']}_{c['condition']}"):
                errors.append(f"cell {c.get('cell_key')}: cell key is not canonical")
    else:
        tasks = {c["task_id"] for c in cells}
        profiles = MODEL_PROFILES[:1] if stage == "screening-qwen-240" else MODEL_PROFILES
        if set(plan.get("model_profiles") or []) != set(profiles):
            errors.append("screening plan has unexpected model profiles")
        if set(plan.get("model_identity") or {}) != set(profiles):
            errors.append("screening plan has unexpected model identity records")
        if len(tasks) != 40 or tasks != set(plan.get("candidate_ids") or []):
            errors.append("screening cells must cover all 40 candidate tasks")
        expected = {(task_id, profile, architecture, seed)
                    for task_id in tasks for profile in profiles
                    for architecture in ARCHITECTURES for seed in (1, 2, 3)}
        actual = {(c["task_id"], c["model_profile"], c["architecture"], c["seed"]) for c in cells}
        if actual != expected:
            errors.append("screening cells differ from the expected task/model/architecture/seed grid")
        if any(c.get("condition") != "control" or c.get("max_steps") != 20 for c in cells):
            errors.append("screening requires clean controls with a 20-step limit")
    return errors


def validate_plan(plan: dict) -> list[str]:
    errors = []
    try:
        plan_stage(plan)
    except ValueError as exc:
        return [str(exc)]
    if plan.get("plan_sha256") != plan_digest(plan):
        errors.append("plan_sha256 does not match the plan contents")
    errors += expected_cells(plan)
    if plan.get("code", {}).get("fingerprint_sha256") != code_fingerprint():
        errors.append("code fingerprint differs; regenerate the plan on the final code")
    if plan.get("dataset_sha256") != file_sha(DATASET):
        errors.append("dataset hash differs")
    if plan.get("evaluator") != current_evaluator_version():
        errors.append("native evaluator fingerprint differs")
    for site, expected in (plan.get("storage_state_sha256") or {}).items():
        actual = file_sha(storage_state_dir(plan) / f"{site}.json")
        if actual != expected:
            errors.append(f"{site} storage state hash differs")
    for profile, identity in (plan.get("model_identity") or {}).items():
        report_path = PROVENANCE
        if identity.get("probe_report"):
            report_path = ROOT / identity["probe_report"]
            if file_sha(report_path) != identity.get("probe_report_sha256"):
                errors.append(f"{profile}: provenance report hash differs")
        try:
            report = {item["profile"]: item for item in load(report_path)}
        except (OSError, ValueError, KeyError, TypeError):
            report = {}
        item = report.get(profile)
        if item is None or not item.get("verdict_ok"):
            errors.append(f"{profile}: provenance report missing or not OK")
            continue
        if identity.get("requested_model") != item.get("requested_model"):
            errors.append(f"{profile}: requested model differs from the plan")
        served = sorted({run["served_model"] for run in item.get("runs", [])
                         if run.get("served_model")})
        if served != identity.get("served_models"):
            errors.append(f"{profile}: served model identity differs from the plan")
        fingerprints = sorted({run["system_fingerprint"] for run in item.get("runs", [])
                               if run.get("system_fingerprint")})
        if identity.get("system_fingerprints") != fingerprints:
            errors.append(f"{profile}: system fingerprint differs from the plan")
    for cell in plan.get("cells") or []:
        command = expected_command(cell, plan)
        if command != cell.get("command"):
            errors.append(f"cell {cell.get('cell_key')}: stored command differs from validated parameters")
    return errors


def preflight(plan: dict) -> dict:
    checks = {
        "dataset_bound": plan.get("dataset_sha256") == file_sha(DATASET),
        "evaluator_config_readable": CONFIG.is_file(),
        "storage_states": all(file_sha(storage_state_dir(plan) / f"{site}.json") == expected
                              for site, expected in (plan.get("storage_state_sha256") or {}).items()),
        "provenance_report": PROVENANCE.is_file(),
        "paid_trials_executed": 0,
    }
    identities = plan.get("model_identity") or {}
    if identities:
        checks["provenance_report"] = all(
            (ROOT / item["probe_report"]).is_file()
            if item.get("probe_report") else PROVENANCE.is_file()
            for item in identities.values())
    try:
        checks["evaluator_dataset_binding"] = (
            Path(load(CONFIG)["test_data_file"]).resolve() == DATASET.resolve())
    except (OSError, ValueError, KeyError, TypeError):
        checks["evaluator_dataset_binding"] = False
    sites = {plan["task"]["site"]} if "task" in plan else {c["site"] for c in plan["cells"]}
    for site in sites:
        try:
            with urllib.request.urlopen(
                    f"http://localhost:{PORTS[site]}/", timeout=5) as response:
                checks[f"site:{site}"] = 200 <= response.status < 500
        except Exception:
            checks[f"site:{site}"] = False
    for profile in (plan.get("model_profiles") or plan.get("models") or []):
        key = profile.upper().replace("-", "_")
        checks[f"model:{profile}"] = bool(os.getenv(f"MODEL_{key}_API_KEY"))
    if "endpoint" in plan:
        endpoint = plan["endpoint"]
        try:
            with urllib.request.urlopen(
                    f"{endpoint['scheme']}://{endpoint['host']}:{endpoint['port']}/",
                    timeout=5) as response:
                checks["endpoint_host_reachable"] = 200 <= response.status < 500
        except Exception:
            checks["endpoint_host_reachable"] = False
    checks["blockers"] = [key for key, value in checks.items() if value is False]
    return checks


def check_authorization(plan: dict, path: Path) -> tuple[dict, list[str]]:
    try:
        auth = load(path)
    except (OSError, ValueError) as exc:
        return {}, [f"authorization unreadable: {exc}"]
    errors = []
    if auth.get("status") != "frozen":
        errors.append("authorization status is not frozen")
    if auth.get("approved_for_paid_runs") is not True:
        errors.append("authorization is not approved for paid runs")
    if auth.get("plan_sha256") != plan.get("plan_sha256"):
        errors.append("authorization plan hash differs")
    stop = auth.get("prompt_token_stop_before_next_trial")
    if type(stop) is not int or stop <= 0:
        errors.append("prompt token stop line must be a positive integer")
    planned = [cell["cell_key"] for cell in plan.get("cells") or []]
    if sorted(auth.get("cell_keys") or []) != sorted(planned):
        errors.append("authorization cell scope differs from the plan")
    if auth.get("code_fingerprint_sha256") != plan.get("code", {}).get("fingerprint_sha256"):
        errors.append("authorization code fingerprint differs")
    if auth.get("dataset_sha256") != plan.get("dataset_sha256"):
        errors.append("authorization dataset hash differs")
    return auth, errors


def cell_task_id(cell: dict, plan: dict) -> int:
    task_id = cell.get("task_id")
    if task_id is None:
        task_id = (plan.get("task") or {}).get("task_id")
    if task_id is None:
        raise ValueError(f"cell {cell.get('cell_key')} has no task id")
    return task_id


def arm_dir(cell: dict) -> Path:
    if not cell.get("arm_dir"):
        raise ValueError(f"cell {cell.get('cell_key')} has no arm_dir")
    return ROOT / cell["arm_dir"]


def trace_evidence(record: dict) -> dict:
    path = Path((record.get("paths") or {}).get("trace") or "")
    if not path.is_absolute():
        path = ROOT / path
    if not path.is_file():
        raise ValueError(f"missing trace {path}")
    total = 0
    by_phase = {}
    steps = {}
    found = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get("event") != "llm_provenance":
            continue
        found = True
        tokens = event.get("prompt_tokens")
        if type(tokens) is not int or tokens < 0:
            raise ValueError("invalid prompt token usage")
        total += tokens
        phase = str(event.get("phase") or "unknown")
        bucket = "executor" if phase.startswith("executor") else phase
        by_phase[bucket] = by_phase.get(bucket, 0) + tokens
        steps.setdefault(bucket, []).append(event.get("step"))
    if not found:
        raise ValueError("trace has no llm_provenance usage")
    return {
        "trace": str(path),
        "prompt_tokens": total,
        "prompt_tokens_by_phase": by_phase,
        "provenance_steps": steps,
    }


def trace_tokens(record: dict) -> int:
    return trace_evidence(record)["prompt_tokens"]


def verify_budget(record: dict, evidence: dict, max_steps: int) -> list[str]:
    errors = []
    steps = record.get("steps")
    if type(steps) is not int or steps < 0:
        errors.append("trial record has no valid step count")
        steps = 0
    elif steps > max_steps:
        errors.append(f"step count {steps} exceeds the {max_steps}-turn budget")
    executor_calls = record.get("executor_calls")
    if type(executor_calls) is not int:
        errors.append("trial record has no executor call count")
    elif executor_calls != steps:
        errors.append(f"executor_calls {executor_calls} != steps {steps}")
    provenance = evidence.get("provenance_steps") or {}
    executor_steps = [step for step in provenance.get("executor", [])
                      if type(step) is int]
    if any(step > max_steps for step in executor_steps):
        errors.append("executor made an LLM call after the last execution turn")
    for phase in ("planner", "replanner"):
        if any(type(step) is int and step >= max_steps
               for step in provenance.get(phase, [])):
            errors.append(f"{phase} made an LLM call after the last execution turn")
    return errors


def fault_evidence(arm: Path) -> dict:
    path = arm / "fault_evidence.json"
    if not path.is_file():
        return {}
    data = load(path)
    exposures = data.get("exposures") or []
    delivered = [item for item in exposures
                 if item.get("delivered") or item.get("fault_delivered")]
    return {
        "path": str(path),
        "variant": data.get("variant"),
        "window_steps": data.get("window_steps"),
        "injections": len(data.get("injections") or []),
        "exposures": len(exposures),
        "matched_endpoint": sum(1 for item in exposures
                                if item.get("matched_endpoint")),
        "delivered_exposures": len(delivered),
        "delivery_steps": sorted({item.get("step") for item in delivered
                                  if type(item.get("step")) is int}),
    }


def verify_completed(cell: dict, plan: dict) -> tuple[dict | None, list[str]]:
    arm = arm_dir(cell)
    errors = []
    required = {name: arm / name for name in (
        "trial_record.json", "agent_response.json", "network.har", "official_eval.json")}
    for name, path in required.items():
        if not path.is_file():
            errors.append(f"missing {name}")
    if errors:
        return None, errors
    record = load(arm / "trial_record.json")
    response_path = required["agent_response.json"]
    har_path = required["network.har"]
    evaluation = load(arm / "official_eval.json")
    task_id = cell_task_id(cell, plan)
    expected = {
        "task_id": task_id,
        "model_profile": cell["model_profile"],
        "architecture": cell["architecture"],
        "fault_seed": cell.get("seed", cell.get("fault_seed")),
        "condition": "control" if cell["condition"] == "control" else "fault",
        "fault_variant": cell.get("fault_variant", "single"),
        "experiment_id": cell.get("experiment_id", "stage-c"),
    }
    for key, value in expected.items():
        if record.get(key) != value:
            errors.append(f"{key}={record.get(key)!r} != {value!r}")
    config = record.get("run_config") or {}
    if config.get("max_steps") != cell["max_steps"]:
        errors.append("max_steps mismatch")
    if not config.get("submission_contract") or not config.get("retrieval_repair"):
        errors.append("improved switches are not both enabled")
    site = plan["task"]["site"] if "task" in plan else cell["site"]
    if config.get("storage_state_sha256") != plan["storage_state_sha256"].get(site):
        errors.append("storage state hash mismatch")
    if cell["condition"] == "control" and record.get("injection_count") != 0:
        errors.append("control arm has injections")
    status = str(evaluation.get("status") or "").casefold()
    score = classify_evaluation(evaluation)
    if score["evaluation_status"] != "native" or evaluation.get("error_msg"):
        errors.append("evaluation is not a valid native result")
    official_success = evaluation.get("official_success")
    if (type(official_success) is not bool or status not in {"success", "failure"}
            or (status == "success") != official_success):
        errors.append("official_success is inconsistent with the evaluation status")
    if evaluation.get("task_id") != task_id:
        errors.append("evaluation task id mismatch")
    if evaluation.get("webarena_verified_data_checksum") != plan.get("dataset_sha256"):
        errors.append("evaluation dataset hash mismatch")
    binding = evaluation.get("_evaluation_binding") or {}
    if binding.get("agent_response_sha256") != file_sha(response_path):
        errors.append("agent response hash is not bound to the evaluation")
    if binding.get("network_har_sha256") != file_sha(har_path):
        errors.append("HAR hash is not bound to the evaluation")
    if binding.get("config_sha256") != file_sha(CONFIG):
        errors.append("evaluator config hash is not bound to the evaluation")
    strict = plan.get("schema") == UNIFIED_SCHEMA
    try:
        evidence = trace_evidence(record)
        tokens = evidence["prompt_tokens"]
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        errors.append(str(exc))
        evidence = {}
        tokens = 0
    if strict and evidence:
        errors += verify_budget(record, evidence, cell["max_steps"])
    injection_count = record.get("injection_count")
    fault = {}
    if strict:
        fault = fault_evidence(arm)
        if cell["condition"] != "control" and not fault:
            errors.append("fault arm has no fault_evidence.json")
        if cell["condition"] == "single" and (injection_count or 0) > 1:
            errors.append("single arm was injected more than once")
        if cell["condition"] == "control" and fault and fault.get("injections"):
            errors.append("control fault evidence records injections")
    if errors:
        return None, errors
    response = load(response_path)
    phase_tokens = {
        "planning": int(record.get("planning_tokens") or 0),
        "executor": int(record.get("executor_tokens") or 0),
        "replanning": int(record.get("replanning_tokens") or 0),
    }
    return {
        "cell_key": cell["cell_key"], "task_id": task_id,
        "model_profile": cell["model_profile"], "architecture": cell["architecture"],
        "seed": cell.get("seed", cell.get("fault_seed")),
        "condition": cell["condition"],
        "steps": record.get("steps"),
        "completed": record.get("completed"),
        "official_success": evaluation["official_success"],
        "official_status": evaluation.get("status"),
        "official_score": evaluation.get("score"),
        "answer": response.get("retrieved_data"),
        "prompt_tokens": tokens,
        "prompt_tokens_by_phase": evidence.get("prompt_tokens_by_phase", {}),
        "planning_tokens": phase_tokens["planning"],
        "executor_tokens": phase_tokens["executor"],
        "replanning_tokens": phase_tokens["replanning"],
        "total_tokens": sum(phase_tokens.values()),
        "llm_calls": record.get("llm_calls"),
        "planning_calls": record.get("planning_calls"),
        "executor_calls": record.get("executor_calls"),
        "replanning_calls": record.get("replanning_calls"),
        "retry_calls": record.get("retry_calls"),
        "termination": record.get("termination"),
        "cap_exhausted": record.get("cap_exhausted"),
        "injection_count": injection_count,
        "fault_evidence": fault or None,
        "repeated_exposure_covered": (
            (injection_count or 0) >= 2 if cell["condition"] == "sustained" else None),
        "storage_state_sha256": config.get("storage_state_sha256"),
        "code_sha": (record.get("code") or {}).get("git_sha"),
        "trace": evidence.get("trace") or str(Path((record.get("paths") or {}).get("trace"))),
        "agent_response": str(response_path),
        "network_har": str(har_path),
        "official_eval": str(arm / "official_eval.json"),
    }, []


def evaluate_and_verify(cell: dict, plan: dict, *, reevaluate: bool) -> tuple[dict | None, list[str]]:
    arm = arm_dir(cell)
    evaluation_path = arm / "official_eval.json"
    if reevaluate or not evaluation_path.is_file():
        response_path = arm / "agent_response.json"
        har_path = arm / "network.har"
        if not response_path.is_file():
            return None, ["missing agent_response.json"]
        evaluation = evaluate_task_safe(
            cell_task_id(cell, plan), agent_response_path=response_path,
            network_trace_path=har_path if har_path.is_file() else None,
            config_path=CONFIG)
        evaluation["_evaluation_binding"] = {
            "agent_response_sha256": file_sha(response_path),
            "network_har_sha256": file_sha(har_path) if har_path.is_file() else None,
            "config_sha256": file_sha(CONFIG),
            "evaluator_adapter_version": "stage-c-eval-adapter/1",
        }
        evaluation_path.parent.mkdir(parents=True, exist_ok=True)
        evaluation_path.write_text(
            json.dumps(evaluation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return verify_completed(cell, plan)


def run_cell(cell: dict, plan: dict, *, timeout: float, reevaluate: bool) -> tuple[dict | None, list[str]]:
    command = expected_command(cell, plan)
    cell_root = ROOT / cell["output_dir"]
    cell_root.mkdir(parents=True, exist_ok=True)
    log_path = cell_root / "trial.log"
    env = dict(os.environ, WEBARENA_DATASET=str(DATASET),
               WEBARENA_HOST="localhost", **SWITCHES)
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.run(command, cwd=ROOT, env=env, stdout=log,
                              stderr=subprocess.STDOUT, timeout=timeout)
    if proc.returncode != 0:
        return None, [f"main.py exited {proc.returncode}; see {log_path}"]
    return evaluate_and_verify(cell, plan, reevaluate=reevaluate)


def summarize(results: list[dict], failures: list[dict], plan: dict) -> dict:
    by_condition = {}
    for row in results:
        key = f"{row['model_profile']}|{row['architecture']}|{row['condition']}"
        bucket = by_condition.setdefault(key, {"trials": 0, "successes": 0,
                                               "prompt_tokens": 0, "injections": 0,
                                               "steps": 0, "total_tokens": 0,
                                               "planning_tokens": 0,
                                               "executor_tokens": 0,
                                               "replanning_tokens": 0})
        bucket["trials"] += 1
        bucket["successes"] += int(row["official_success"])
        bucket["prompt_tokens"] += row["prompt_tokens"]
        bucket["injections"] += row.get("injection_count") or 0
        bucket["steps"] += row.get("steps") or 0
        bucket["total_tokens"] += row.get("total_tokens") or 0
        bucket["planning_tokens"] += row.get("planning_tokens") or 0
        bucket["executor_tokens"] += row.get("executor_tokens") or 0
        bucket["replanning_tokens"] += row.get("replanning_tokens") or 0
    summary = {
        "schema": "agent_stress_plan_summary/1",
        "experiment_id": plan.get("experiment_id"),
        "plan_sha256": plan.get("plan_sha256"),
        "completed": len(results), "planned": plan.get("planned_trials"),
        "failures": failures, "by_condition": by_condition,
    }
    if plan.get("schema") == UNIFIED_SCHEMA:
        sustained = [row for row in results if row["condition"] == "sustained"]
        single = [row for row in results if row["condition"] == "single"]
        control = [row for row in results if row["condition"] == "control"]
        summary["unified_acceptance"] = {
            "max_steps": plan.get("max_steps"),
            "all_within_budget": all(
                (row.get("steps") or 0) <= plan.get("max_steps", 0)
                and (row.get("executor_calls") or 0) == (row.get("steps") or 0)
                for row in results),
            "control_injections": sum(row.get("injection_count") or 0 for row in control),
            "single_untriggered": [row["cell_key"] for row in single
                                   if not (row.get("injection_count") or 0)],
            "sustained_single_exposure": [row["cell_key"] for row in sustained
                                          if (row.get("injection_count") or 0) == 1],
            "sustained_untriggered": [row["cell_key"] for row in sustained
                                      if not (row.get("injection_count") or 0)],
            "repeated_exposure_tasks": sorted({row["task_id"] for row in sustained
                                               if row.get("repeated_exposure_covered")}),
            "repeated_exposure_cells": [row["cell_key"] for row in sustained
                                        if row.get("repeated_exposure_covered")],
        }
    if plan.get("schema") == QWEN_SCREENING_SCHEMA:
        by_task = {}
        for task_id, task in plan["tasks"].items():
            by_task[task_id] = {
                "site": task["site"],
                "template_id": task["intent_template_id"],
                "architectures": {arch: {"trials": 0, "successes": 0} for arch in ARCHITECTURES},
            }
        for row in results:
            bucket = by_task[str(row["task_id"])]["architectures"][row["architecture"]]
            bucket["trials"] += 1
            bucket["successes"] += int(row["official_success"])
        for task in by_task.values():
            buckets = task["architectures"].values()
            task["qwen_qualified"] = (
                all(bucket["successes"] >= 2 for bucket in buckets)
                if all(bucket["trials"] == 3 for bucket in buckets) else None
            )
        qualified = [int(task_id) for task_id, task in by_task.items() if task["qwen_qualified"]]
        summary["qwen_screening"] = {
            "by_task": by_task,
            "qualified_task_ids": qualified,
            "qualified_template_ids": sorted({by_task[str(i)]["template_id"] for i in qualified}),
        }
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--reevaluate", action="store_true")
    parser.add_argument("--job-timeout-minutes", type=float, default=45)
    args = parser.parse_args(argv)
    try:
        plan = load(args.plan)
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "blockers": [f"plan unreadable: {exc}"]}))
        return 2
    errors = validate_plan(plan)
    checks = preflight(plan)
    blockers = errors + checks.get("blockers", [])
    auth = {}
    if args.authorization is not None:
        auth, auth_errors = check_authorization(plan, args.authorization)
        blockers += auth_errors
    if args.check or not args.execute:
        print(json.dumps({"ok": not blockers, "stage": plan.get("schema"),
                          "planned_trials": plan.get("planned_trials"),
                          "blockers": blockers, "preflight": checks,
                          "paids_started": 0}, ensure_ascii=False, indent=2))
        return 0 if not blockers else 2
    if blockers:
        print(json.dumps({"ok": False, "blockers": blockers, "paids_started": 0},
                         ensure_ascii=False, indent=2))
        return 2
    if not auth:
        print(json.dumps({"ok": False, "blockers": ["--execute requires --authorization"]}))
        return 2
    run_root = args.run_root.resolve()
    progress_path = run_root / "progress.json"
    completed = {}
    failures = []
    resume_rejections = []
    if progress_path.exists():
        if not args.resume:
            print(json.dumps({"ok": False, "blockers": ["run root already has progress; use --resume"]}))
            return 2
        progress = load(progress_path)
        if progress.get("plan_sha256") != plan.get("plan_sha256"):
            print(json.dumps({"ok": False, "blockers": ["resume plan hash differs"]}))
            return 2
        cells_by_key = {cell["cell_key"]: cell for cell in plan["cells"]}
        for row in progress.get("results", []):
            cell = cells_by_key.get(row.get("cell_key"))
            verified, why = verify_completed(cell, plan) if cell else (None, ["unknown cell"])
            if verified is not None:
                completed[cell["cell_key"]] = verified
            else:
                resume_rejections.append({"cell_key": row.get("cell_key"), "errors": why})
        if resume_rejections:
            print(json.dumps({"ok": False, "resume_rejections": resume_rejections,
                              "paids_started": 0}, ensure_ascii=False))
            return 2
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
                                        encoding="utf-8")
    (run_root / "preflight.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2) + "\n",
                                              encoding="utf-8")
    (run_root / "authorization.json").write_text(json.dumps(auth, ensure_ascii=False, indent=2) + "\n",
                                                   encoding="utf-8")
    results = list(completed.values())
    total_tokens = sum(row["prompt_tokens"] for row in results)
    stop_before = auth["prompt_token_stop_before_next_trial"]
    stop_reason = None
    queue = [cell for cell in plan["cells"] if cell["cell_key"] not in completed]
    if args.limit:
        queue = queue[:args.limit]
    for index, cell in enumerate(queue, 1):
        if total_tokens >= stop_before:
            stop_reason = "token_stop_before_next_trial"
            break
        print(f"[{index}/{len(queue)}] {cell['cell_key']}", flush=True)
        try:
            row, why = run_cell(cell, plan, timeout=args.job_timeout_minutes * 60,
                                reevaluate=args.reevaluate)
        except Exception as exc:  # noqa: BLE001 -- record and stop the batch
            row, why = None, [str(exc)]
        if row is None:
            failures.append({"cell_key": cell["cell_key"], "errors": why,
                             "at": datetime.now(timezone.utc).isoformat()})
            print(f"    FAILED: {why}", flush=True)
        else:
            results.append(row)
            total_tokens += row["prompt_tokens"]
            print(f"    success={row['official_success']} steps={row['steps']} "
                  f"tokens={row['prompt_tokens']} injections={row['injection_count']}",
                  flush=True)
        (run_root / "progress.json").write_text(json.dumps({
            "schema": "agent_stress_plan_progress/1",
            "experiment_id": plan.get("experiment_id"),
            "plan_sha256": plan.get("plan_sha256"),
            "completed": len(results), "planned": plan.get("planned_trials"),
            "prompt_tokens_total": total_tokens,
            "prompt_token_stop_before_next_trial": stop_before,
            "stop_reason": stop_reason,
            "resume_rejections": resume_rejections,
            "failures": failures, "results": results,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if failures:
            break
        time.sleep(1)
    summary = summarize(results, failures, plan)
    summary.update({"prompt_tokens_total": total_tokens,
                    "prompt_token_stop_before_next_trial": stop_before,
                    "stop_reason": stop_reason,
                    "resume_rejections": resume_rejections})
    (run_root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                                            encoding="utf-8")
    print(json.dumps({"ok": not failures, "completed": len(results),
                      "prompt_tokens_total": total_tokens,
                      "stop_reason": stop_reason}, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
