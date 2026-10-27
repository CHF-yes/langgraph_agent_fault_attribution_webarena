#!/usr/bin/env python3
"""Prepare / preflight / run the independent 348-cell clean screening stage.

Default: plan only. --check-environment checks services/configuration without
calling a model. --execute requires a bound frozen authorization and fresh
preflight. This entry never enables faults and does not depend on H/C contracts.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import subprocess
import sys
from urllib.request import urlopen
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
EXPERIMENT = "agent-stress-v1-screening"
MODEL_PROFILES = ("deepseek_v41_flash", "qwen38_flash")
PORTS = {"shopping": 7770, "reddit": 9999, "gitlab": 8023}


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def file_sha(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except (OSError, TypeError):
        return None


def code_sha(root=ROOT):
    paths = [root / "main.py", root / "run_baseline.py", root / "requirements.txt"]
    for directory in ("standard_agent", "fault_injection", "scripts"):
        paths.extend((root / directory).rglob("*.py"))
    return digest({str(path.relative_to(root)): file_sha(path) for path in sorted(paths)})


def build_plan(candidates, matrix, dataset=None, *, host="localhost", schedule_seed=20261022,
               model_profile=None):
    tasks = candidates["tasks"]
    ids = [item["task_id"] for item in tasks]
    if len(ids) != len(set(ids)) or len(ids) != 58:
        raise ValueError("screening requires the 58 unique frozen candidates")
    stage = next(item for item in matrix["stages"] if item["id"] == "screening")
    if (stage["tasks"], stage["architectures"], stage["repetitions"],
        stage["conditions"], stage["max_steps"], stage["planned_trials"]) != (58, 2, 3, 1, 20, 348):
        raise ValueError("screening scope differs from 58 x 2 x 3 clean, 20 steps")
    stage_models = stage.get("model_profiles", matrix["scope"]["model_profiles"])
    if len(stage_models) != 1 or stage_models[0] not in MODEL_PROFILES:
        raise ValueError("screening requires one supported model profile")
    model_profile = model_profile or stage_models[0]
    if model_profile not in MODEL_PROFILES:
        raise ValueError("unsupported screening model profile")
    if matrix["scope"]["architectures"] != ["react", "plan_execute"]:
        raise ValueError("screening architectures differ from the proposal")
    definitions = None
    if dataset is not None:
        rows = load(dataset)
        definitions = {row["task_id"]: row for row in rows}
        if len(definitions) != len(rows):
            raise ValueError("duplicate task IDs in dataset")
    cells = []
    for item in tasks:
        task_id, site = item["task_id"], item["site"]
        if site not in PORTS:
            raise ValueError(f"task {task_id}: unsupported site")
        url = None
        if definitions is not None:
            task = definitions.get(task_id)
            if (task is None or task.get("intent") != item["intent"]
                    or task.get("intent_template_id") != item["intent_template_id"]
                    or task.get("sites") != [site] or not task.get("eval")):
                raise ValueError(f"task {task_id}: dataset identity/intent/template/site/evaluator mismatch")
            urls = task.get("start_urls") or []
            if not urls:
                raise ValueError(f"task {task_id}: no start URL")
            url = urls[0].replace(f"__{site.upper()}__", f"http://{host}:{PORTS[site]}")
            parsed = urlsplit(url)
            if ("__" in url or parsed.scheme != "http" or parsed.hostname != host.lower()
                    or parsed.port != PORTS[site] or parsed.username or parsed.password):
                raise ValueError(f"task {task_id}: start URL does not match the bound site/host")
        for architecture in ("react", "plan_execute"):
            for seed in (1, 2, 3):
                cells.append({"key": f"{model_profile}_task{task_id}_{architecture}_seed{seed}",
                              "task_id": task_id, "site": site, "start_url": url,
                              "intent": item["intent"], "architecture": architecture,
                              "model_profile": model_profile, "seed": seed,
                              "max_steps": 20, "condition": "control"})
    random.Random(schedule_seed).shuffle(cells)
    plan = {"schema": "agent_stress_screening_plan/2", "experiment_id": f"{EXPERIMENT}-{model_profile}",
            "stage": "screening", "planned_trials": len(cells), "host": host,
            "model_profile": model_profile,
            "eligibility_applies_to_model_profile": model_profile,
            "downstream_proposal_model_profiles": matrix["scope"]["model_profiles"],
            "cross_model_eligibility_transfer_allowed": False,
            "schedule_seed": schedule_seed, "candidates_sha256": digest(candidates),
            "dataset_sha256": file_sha(dataset), "cells": cells,
            "faults_enabled": False, "requires_HC_runtime": False}
    plan["plan_sha256"] = digest(plan)
    return plan


def preflight(plan, config_path):
    """Read-only environment checks. Never constructs an LLM or prints credentials."""
    checks = {"dataset_bound": plan.get("dataset_sha256") is not None,
              "evaluator_config_readable": file_sha(config_path) is not None}
    details = {}
    try:
        config = load(config_path)
        configured_dataset = Path(config.get("test_data_file", ""))
        if not configured_dataset.is_absolute():
            configured_dataset = ROOT / configured_dataset
        checks["evaluator_dataset_binding"] = bool(plan.get("dataset_sha256")) and file_sha(configured_dataset) == plan["dataset_sha256"]
        environments = config.get("environments") or {}
        checks["evaluator_site_bindings"] = all(
            f"http://{plan['host']}:{PORTS[site]}" in (environments.get(f"__{site.upper()}__") or {}).get("urls", [])
            for site in sorted({cell["site"] for cell in plan["cells"]}))
    except (OSError, ValueError, TypeError):
        checks["evaluator_dataset_binding"] = checks["evaluator_site_bindings"] = False
    for name in ("langgraph", "langchain_openai", "playwright", "dotenv"):
        checks[f"dependency:{name}"] = importlib.util.find_spec(name) is not None
    try:
        from standard_agent.stage_c_pipeline import current_evaluator_version
        version = current_evaluator_version()
        checks["native_evaluator_sources"] = bool(version.get("package_sources"))
    except ImportError:
        version = {}
        checks["native_evaluator_sources"] = False
    try:
        from standard_agent.config import settings
        profile = settings.get_model_profile(plan["model_profile"])
        checks["model_profile_configured"] = profile.name == plan["model_profile"] and profile.validate()
        # Public profile identity only; API keys and endpoint URLs are never emitted.
        details["model_identity_sha256"] = digest({"profile": profile.name, "model": profile.model,
                                                   "base_url": profile.base_url,
                                                   "temperature": profile.temperature,
                                                   "thinking": profile.thinking,
                                                   "request_timeout": profile.request_timeout,
                                                   "max_retries": profile.max_retries})
    except (ImportError, ValueError):
        checks["model_profile_configured"] = False
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            checks["chromium_installed"] = Path(playwright.chromium.executable_path).is_file()
    except Exception:
        checks["chromium_installed"] = False
    for site in sorted({cell["site"] for cell in plan["cells"]}):
        try:
            with urlopen(f"http://{plan['host']}:{PORTS[site]}/", timeout=3) as response:
                checks[f"site:{site}"] = response.status == 200
        except Exception:
            checks[f"site:{site}"] = False
    return {"stage": "screening", "model_profile": plan["model_profile"], "checks": checks,
            "blockers": [name for name, passed in checks.items() if not passed],
            "environment_ready": all(checks.values()), "evaluator_version": version,
            **details, "paid_trials_executed": 0,
            "access_scope": "site-level only; task/account availability is measured during screening"}


def authorization_template(plan, report, config_path):
    return {"status": "candidate", "approved_for_paid_runs": False,
            "experiment_id": plan["experiment_id"], "stage": "screening",
            "model_profile": plan["model_profile"],
            "plan_sha256": plan["plan_sha256"], "code_sha256": code_sha(),
            "dataset_sha256": plan["dataset_sha256"],
            "evaluator_config_sha256": file_sha(config_path),
            "evaluator_package_sha256": report.get("evaluator_version", {}).get("package_sha256"),
            "model_identity_sha256": report.get("model_identity_sha256"),
            "max_trials": 348, "max_steps": 20, "job_timeout_seconds": 2700,
            "prompt_token_stop_before_next_trial": None,
            "approval_consumed": False,
            "note": "Candidate only. Fill a positive prompt-token limit and authorize explicitly. No H/C approval is inherited."}


def check_authorization(plan, frozen, report, config_path):
    errors = []
    if digest({key: value for key, value in plan.items() if key != "plan_sha256"}) != plan.get("plan_sha256"):
        errors.append("plan content differs from its hash")
    if frozen.get("status") != "frozen" or frozen.get("approved_for_paid_runs") is not True:
        errors.append("screening batch is not frozen and approved")
    if frozen.get("approval_consumed") is not False:
        errors.append("authorization already consumed or state missing")
    if (plan.get("model_profile") not in MODEL_PROFILES
            or {cell.get("model_profile") for cell in plan["cells"]} != {plan.get("model_profile")}
            or report.get("model_profile") != plan.get("model_profile")):
        errors.append("screening plan/cells/preflight model profile mismatch")
    expected = authorization_template(plan, report, config_path)
    for key in ("experiment_id", "stage", "model_profile", "plan_sha256", "code_sha256", "dataset_sha256",
                "evaluator_config_sha256", "evaluator_package_sha256", "model_identity_sha256",
                "max_trials", "max_steps", "job_timeout_seconds"):
        if frozen.get(key) != expected[key] or expected[key] is None:
            errors.append(f"authorization mismatch or unbound: {key}")
    limit = frozen.get("prompt_token_stop_before_next_trial")
    if type(limit) is not int or limit < 1:
        errors.append("positive batch prompt-token stop limit required")
    if not report.get("environment_ready"):
        errors.append("screening environment is not ready")
    if not plan.get("dataset_sha256") or any(not cell.get("start_url") for cell in plan["cells"]):
        errors.append("dataset and start URLs must be bound")
    return errors


def command(cell, output, python=sys.executable, *, experiment_id=EXPERIMENT):
    return [python, "main.py", "--benchmark", "--condition", "control",
            "--experiment-id", experiment_id, "--site", cell["site"],
            "--url", cell["start_url"], "--task", cell["intent"],
            "--task-id", str(cell["task_id"]), "--model-profile", cell["model_profile"],
            "--architecture", cell["architecture"], "--fault-seed", str(cell["seed"]),
            "--trials", "1", "--max-steps", "20", "--webarena-output-dir", str(output)]


def trace_prompt_tokens(record):
    trace = (record.get("paths") or {}).get("trace")
    if not trace:
        raise ValueError("missing bound trace path")
    path = Path(trace)
    if not path.is_absolute():
        path = ROOT / path
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    calls = [event for event in events if event.get("event") == "llm_provenance"]
    if not calls or any(type(event.get("prompt_tokens")) is not int or event["prompt_tokens"] < 0
                        for event in calls):
        raise ValueError("missing prompt usage; no next trial may start")
    return sum(event["prompt_tokens"] for event in calls)


def execute(plan, frozen, report, config_path, output_root, *, dataset, authorization_path,
            artifact_checker=None, evaluator=None):
    errors = check_authorization(plan, frozen, report, config_path)
    if errors:
        raise ValueError("; ".join(errors))
    run_root = Path(output_root)
    if run_root.exists():
        raise ValueError("run root already exists; outputs must never be overwritten")
    # A durable claim adjacent to the authorization prevents reuse with another run root.
    with Path(str(authorization_path) + ".claimed").open("x") as claim:
        json.dump({"run_root": str(run_root.resolve()), "plan_sha256": plan["plan_sha256"]}, claim)
    # Exclusive directory claim prevents concurrent launches and accidental replay.
    run_root.mkdir(parents=True, exist_ok=False)
    (run_root / "authorization.json").write_text(json.dumps(frozen, indent=2))
    if artifact_checker is None or evaluator is None:
        from standard_agent.stage_c_pipeline import evaluate_trial, arm_artifacts_ok
        artifact_checker = artifact_checker or arm_artifacts_ok
        evaluator = evaluator or evaluate_trial
    os.environ["WEBARENA_DATASET"] = str(Path(dataset).resolve())
    completed, prompt_tokens = [], 0
    for cell in plan["cells"]:
        if prompt_tokens >= frozen["prompt_token_stop_before_next_trial"]:
            raise RuntimeError("batch prompt-token limit reached; no next trial started")
        root = run_root / cell["key"]
        root.mkdir()
        env = dict(os.environ, WEBARENA_DATASET=str(Path(dataset).resolve()), WEBARENA_HOST=plan["host"])
        with (root / "trial.log").open("x") as log:
            proc = subprocess.run(command(cell, root / "outputs", experiment_id=plan["experiment_id"]), cwd=ROOT, env=env,
                                  stdout=log, stderr=subprocess.STDOUT,
                                  timeout=frozen["job_timeout_seconds"])
        arm = root / "outputs" / str(cell["task_id"]) / f"control_seed_{cell['seed']}"
        expect = {key: cell[key] for key in ("task_id", "architecture", "model_profile", "max_steps", "condition")}
        expect.update(experiment_id=plan["experiment_id"], fault_seed=cell["seed"],
                      fault_intensity="off", fault_type="control")
        _, problems = artifact_checker(arm, expect=expect)
        if proc.returncode != 0 or problems:
            raise RuntimeError(f"{cell['key']}: execution/artifact error; stopped without automatic rerun")
        record = load(arm / "trial_record.json")
        if record.get("injection_count") != 0:
            raise RuntimeError(f"{cell['key']}: control contamination")
        evaluation = evaluator(arm, task_id=cell["task_id"], config_path=config_path)
        if evaluation.get("evaluation_status") != "native":
            raise RuntimeError(f"{cell['key']}: native grading unavailable")
        # Actual usage is read from the bound trace; unknown usage stops the batch.
        prompt_tokens += trace_prompt_tokens(record)
        completed.append({"key": cell["key"], "model_profile": cell["model_profile"],
                          "task_id": cell["task_id"], "architecture": cell["architecture"],
                          "seed": cell["seed"], "official_success": evaluation.get("official_success")})
        (run_root / "progress.json").write_text(json.dumps({"completed": completed,
            "experiment_id": plan["experiment_id"], "model_profile": plan["model_profile"],
            "prompt_tokens": prompt_tokens, "auto_retry": False}, indent=2))
    return {"completed_trials": len(completed), "prompt_tokens": prompt_tokens}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=os.getenv("WEBARENA_DATASET", "/root/webarena-dataset/webarena-verified.json"))
    parser.add_argument("--host", default=os.getenv("WEBARENA_HOST", "localhost"))
    parser.add_argument("--evaluator-config", default="experiments/webarena_local_config.json")
    parser.add_argument("--model-profile", choices=MODEL_PROFILES,
                        help="Override the screening-stage profile; default comes from the matrix stage.")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--check-environment", action="store_true")
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--run-root", type=Path)
    args = parser.parse_args(argv)
    try:
        dataset = args.dataset if file_sha(args.dataset) else None
        plan = build_plan(load(ROOT / "docs/故障压力测试候选任务.json"),
                          load(ROOT / "docs/故障压力测试矩阵草案.json"), dataset, host=args.host,
                          model_profile=args.model_profile)
        report = preflight(plan, args.evaluator_config) if args.check_environment or args.execute else {
            "stage": "screening", "model_profile": plan["model_profile"],
            "environment_ready": False, "checks": {},
            "blockers": ["environment checks not requested"], "paid_trials_executed": 0}
        if args.output_dir:
            args.output_dir.mkdir(parents=True, exist_ok=True)
            for name, content in (("plan.json", plan), ("preflight.json", report),
                                  ("authorization.candidate.json", authorization_template(plan, report, args.evaluator_config))):
                path = args.output_dir / name
                with path.open("x", encoding="utf-8") as stream:
                    json.dump(content, stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
        if args.execute:
            if not args.authorization or not args.run_root or dataset is None:
                raise ValueError("execution requires --authorization, --run-root and a readable --dataset")
            result = execute(plan, load(args.authorization), report, args.evaluator_config,
                             args.run_root, dataset=dataset, authorization_path=args.authorization)
            print(json.dumps(result))
        else:
            print(json.dumps({"planned_trials": plan["planned_trials"],
                "model_profile": plan["model_profile"], "experiment_id": plan["experiment_id"],
                "dataset_bound": bool(plan["dataset_sha256"]), "environment": report,
                "paid_trials_executed": 0, "faults_enabled": False}, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
