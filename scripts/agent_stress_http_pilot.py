#!/usr/bin/env python3
"""18-trial Qwen Flash + ReAct service-level HTTP fault pilot (ep-http-service-v1).

Phases:
  plan       build and freeze the 18-cell plan (3 tasks x 2 seeds x 3 conditions)
  probe      run the no-model fixed-action injection verification (9 probes)
  run        execute cells (all by default, or --block N)
  report     build the 6-row paired table and condition totals

Every executed cell is scored with the native WebArena evaluator; fault arms must
carry a valid service episode (endpoint identity, 10-step window, evidence hash).
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

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from fault_injection.config import FaultConfig
from fault_injection.http_service import (
    EXPERIMENT_ID, TARGET_POLICY_VERSION, ServiceEndpoint, classify_service_episode,
    protocol_metadata,
)
from run_baseline import load_tasks, resolve_start_url
from standard_agent.stage_c_pipeline import current_evaluator_version
from standard_agent.webarena_evaluator import evaluate_task_safe
from standard_agent.webarena_verified import load_task_definition

load_dotenv(ROOT / ".env")

DATASET = Path(os.getenv("WEBARENA_DATASET",
                         "/data/webarena/webarena-dataset/webarena-verified.json"))
CONFIG = ROOT / "experiments/webarena_local_config.local.json"
AUTH_DIR = ROOT / "experiments/auth_20261120"
PROVENANCE = ROOT / "experiments/agent_stress_v1/execution_prep_20261116/model_provenance.json"
OUT = ROOT / "experiments/agent_stress_v1/http_pilot_20261120"
MODEL = "qwen38_flash"
ARCH = "react"
MAX_STEPS = 20
WINDOW_STEPS = 10
SCHEDULE_SEED = 20261120
SEEDS = (1, 2)
SWITCHES = {"SUBMISSION_CONTRACT": "1", "RETRIEVAL_REPAIR": "1"}
CONDITIONS = {
    "control": {"run_condition": "control", "variant": "single", "intensity": "off"},
    "single": {"run_condition": "fault", "variant": "single", "intensity": "high"},
    "sustained": {"run_condition": "fault", "variant": "persistent", "intensity": "high"},
}
TASKS = {
    228: {
        "site": "shopping",
        "endpoint": "http://localhost:7770/catalogsearch/result",
        "target_first": "http://localhost:7770/catalogsearch/result/?q=sephora",
        "target_changed": "http://localhost:7770/catalogsearch/result/?q=sephora&p=2",
        "other": [
            "http://localhost:7770/customer/account/",
            "http://localhost:7770/beauty-personal-care.html",
            "http://localhost:7770/",
        ],
        "alternative_note": "购物站目录页/商品页与 /search/ajax/suggest 不受该端点故障影响",
    },
    28: {
        "site": "reddit",
        "endpoint": "http://localhost:9999/f/WorcesterMA",
        "target_first": "http://localhost:9999/f/WorcesterMA",
        "target_changed": "http://localhost:9999/f/WorcesterMA/new",
        "other": [
            "http://localhost:9999/forums/all",
            "http://localhost:9999/forums",
            "http://localhost:9999/",
        ],
        "alternative_note": "论坛列表 /forums/all 与其他板块页不受 /f/WorcesterMA 端点故障影响",
    },
    314: {
        "site": "gitlab",
        "endpoint": "http://localhost:8023/primer/design/-/graphs",
        "target_first": "http://localhost:8023/primer/design/-/graphs/main",
        "target_changed": "http://localhost:8023/primer/design/-/graphs/main?format=json",
        "other": [
            "http://localhost:8023/primer/design/-/tree/main",
            "http://localhost:8023/api/v4/projects/180/repository/branches",
            "http://localhost:8023/dashboard/projects",
        ],
        "alternative_note": "项目树页与 REST /api/v4 接口不受 /-/graphs 端点故障影响",
    },
}


def now():
    return datetime.now(timezone.utc).isoformat()


def file_sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def load(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def git_head() -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                                   text=True).strip()


def code_fingerprint() -> str:
    paths = [ROOT / "main.py", ROOT / "run_baseline.py"]
    for directory in ("standard_agent", "fault_injection", "scripts"):
        paths.extend((ROOT / directory).rglob("*.py"))
    return digest({str(p.relative_to(ROOT)): file_sha(p) for p in sorted(paths)})


def model_identity() -> dict:
    report = {item["profile"]: item for item in load(PROVENANCE)}
    item = report[MODEL]
    if not item.get("verdict_ok"):
        raise ValueError("qwen38_flash provenance is not OK")
    return {
        "requested_model": item["requested_model"],
        "served_models": sorted({r["served_model"] for r in item.get("runs", [])
                                 if r.get("served_model")}),
        "system_fingerprints": sorted({r["system_fingerprint"] for r in item.get("runs", [])
                                       if r.get("system_fingerprint")}),
        "probe_report": str(PROVENANCE.relative_to(ROOT)),
        "probe_report_sha256": file_sha(PROVENANCE),
    }


def task_table() -> dict:
    tasks = {t["task_id"]: t for t in load_tasks(task_ids=list(TASKS))}
    table = {}
    for tid, spec in TASKS.items():
        task = tasks[tid]
        definition = load_task_definition(tid)
        start_url = resolve_start_url(task)
        if not start_url.startswith("http"):
            raise ValueError(f"task {tid}: start URL not resolved: {start_url!r}")
        table[tid] = {
            "task_id": tid,
            "site": spec["site"],
            "start_url": start_url,
            "intent": task["intent"],
            "sites": task.get("sites"),
            "task_type": (definition.get("eval") or [{}])[0].get("expected", {}).get("task_type"),
            "endpoint": spec["endpoint"],
            "target_first": spec["target_first"],
            "target_changed": spec["target_changed"],
            "other": spec["other"],
            "alternative_note": spec["alternative_note"],
        }
    return table


def arm_dir(cell: dict) -> Path:
    label = "control" if cell["condition"] == "control" else "web_http_error"
    return (ROOT / cell["output_dir"] / "outputs" /
            f"web_http_error_task{cell['task_id']}_seed{cell['seed']}" /
            str(cell["task_id"]) / f"{label}_seed_{cell['seed']}")


def cell_command(cell: dict) -> list[str]:
    out = ROOT / cell["output_dir"] / "outputs" / (
        f"web_http_error_task{cell['task_id']}_seed{cell['seed']}")
    return [
        sys.executable, "main.py", "--benchmark",
        "--site", cell["site"], "--url", cell["start_url"],
        "--task", cell["intent"], "--task-id", str(cell["task_id"]),
        "--model-profile", MODEL, "--architecture", ARCH,
        "--max-steps", str(MAX_STEPS), "--trials", "1",
        "--fault-type", "web_http_error",
        "--fault-intensity", cell["fault_intensity"],
        "--fault-seed", str(cell["seed"]),
        "--experiment-id", EXPERIMENT_ID,
        "--fault-variant", cell["fault_variant"],
        "--fault-window-steps", str(WINDOW_STEPS),
        "--fault-target-endpoint", cell["endpoint"],
        "--condition", cell["run_condition"],
        "--webarena-output-dir", str(out),
        "--storage-state-dir", str(AUTH_DIR),
        "--submission-contract",
    ]


def build_plan(out: Path = OUT) -> dict:
    tasks = task_table()
    blocks = [(tid, seed) for tid in sorted(TASKS) for seed in SEEDS]
    random.Random(SCHEDULE_SEED).shuffle(blocks)
    cells = []
    for block_index, (tid, seed) in enumerate(blocks, 1):
        order = list(CONDITIONS)
        random.Random(SCHEDULE_SEED + block_index).shuffle(order)
        for position, condition in enumerate(order, 1):
            spec = CONDITIONS[condition]
            key = f"qwen38_flash_task{tid}_react_seed{seed}_{condition}"
            cell = {
                "cell_key": key,
                "block": block_index,
                "order_in_block": position,
                "task_id": tid,
                "site": tasks[tid]["site"],
                "start_url": tasks[tid]["start_url"],
                "intent": tasks[tid]["intent"],
                "model_profile": MODEL,
                "architecture": ARCH,
                "seed": seed,
                "fault_seed": seed,
                "condition": condition,
                "run_condition": spec["run_condition"],
                "fault_variant": spec["variant"],
                "fault_intensity": spec["intensity"],
                "fault_type": "web_http_error",
                "experiment_id": EXPERIMENT_ID,
                "max_steps": MAX_STEPS,
                "window_steps": WINDOW_STEPS if condition != "control" else None,
                "endpoint": tasks[tid]["endpoint"],
                "output_dir": str((out / "cells" / key).relative_to(ROOT)),
            }
            cell["arm_dir"] = str(arm_dir(cell).relative_to(ROOT))
            cell["command"] = cell_command(cell)
            cells.append(cell)
    plan = {
        "schema": "agent_stress_http_pilot_plan/1",
        "experiment_id": "agent-stress-v1-http-pilot-18",
        "protocol_experiment_id": EXPERIMENT_ID,
        "created_at": now(),
        "status": "frozen",
        "approved_for_paid_runs": True,
        "approval_note": ("User requested the 18-trial Qwen Flash + ReAct service-level "
                          "HTTP pilot: 3 tasks x 2 seeds x 3 conditions, 20 steps."),
        "tasks": {str(k): v for k, v in tasks.items()},
        "endpoints": {str(k): v["endpoint"] for k, v in TASKS.items()},
        "conditions": list(CONDITIONS),
        "condition_order": "pre-randomised within each block; recorded in order_in_block",
        "window_steps": WINDOW_STEPS,
        "window_definition": "first exposure plus the following 9 proxy steps",
        "max_steps": MAX_STEPS,
        "model_profiles": [MODEL],
        "architectures": [ARCH],
        "seeds": list(SEEDS),
        "blocks": [{"block": i, "task_id": tid, "seed": seed,
                    "order": [c["condition"] for c in cells if c["block"] == i]}
                   for i, (tid, seed) in enumerate(blocks, 1)],
        "cells": cells,
        "planned_trials": len(cells),
        "switches": SWITCHES,
        "storage_state_dir": str(AUTH_DIR.relative_to(ROOT)),
        "storage_state_sha256": {site: file_sha(AUTH_DIR / f"{site}.json")
                                 for site in ("shopping", "gitlab", "reddit")},
        "login_state": {
            "shopping": "logged in (My Account / Sign Out verified)",
            "gitlab": "logged in (dashboard projects verified)",
            "reddit": "anonymous by design (no-login empty state; task 28 is public read)",
        },
        "model_identity": model_identity(),
        "dataset_path": str(DATASET),
        "dataset_sha256": file_sha(DATASET),
        "evaluator": current_evaluator_version(),
        "code": {"head": git_head(), "fingerprint_sha256": code_fingerprint()},
        "faults_enabled": True,
    }
    plan["plan_sha256"] = digest(plan)
    return plan


def plan_errors(plan: dict) -> list[str]:
    errors = []
    if plan.get("plan_sha256") != digest({k: v for k, v in plan.items()
                                          if k != "plan_sha256"}):
        errors.append("plan_sha256 mismatch")
    cells = plan.get("cells") or []
    if len(cells) != 18 or plan.get("planned_trials") != 18:
        errors.append("pilot must contain exactly 18 cells")
    expected = {(tid, seed, condition) for tid in TASKS for seed in SEEDS
                for condition in CONDITIONS}
    actual = {(c["task_id"], c["seed"], c["condition"]) for c in cells}
    if actual != expected:
        errors.append("cell grid differs from 3 tasks x 2 seeds x 3 conditions")
    if {c["condition"] for c in cells} != set(CONDITIONS):
        errors.append("conditions must be control/single/sustained")
    for c in cells:
        if c["max_steps"] != MAX_STEPS:
            errors.append(f"{c['cell_key']}: max_steps != {MAX_STEPS}")
        if c["condition"] == "control":
            if c["fault_intensity"] != "off":
                errors.append(f"{c['cell_key']}: control must be intensity=off")
        else:
            if c["fault_intensity"] != "high" or c["window_steps"] != WINDOW_STEPS:
                errors.append(f"{c['cell_key']}: fault arm protocol mismatch")
        if cell_command(c) != c["command"]:
            errors.append(f"{c['cell_key']}: stored command mismatch")
    for block in {c["block"] for c in cells}:
        block_cells = [c for c in cells if c["block"] == block]
        if len(block_cells) != 3:
            errors.append(f"block {block} is not 3 cells")
        if sorted(c["order_in_block"] for c in block_cells) != [1, 2, 3]:
            errors.append(f"block {block} order is not 1..3")
        if len({(c["task_id"], c["seed"]) for c in block_cells}) != 1:
            errors.append(f"block {block} mixes task/seed")
    if plan.get("dataset_sha256") != file_sha(DATASET):
        errors.append("dataset hash changed")
    if plan.get("evaluator") != current_evaluator_version():
        errors.append("evaluator fingerprint changed")
    if plan.get("code", {}).get("fingerprint_sha256") != code_fingerprint():
        errors.append("code fingerprint changed")
    for site, expected_sha in (plan.get("storage_state_sha256") or {}).items():
        if file_sha(AUTH_DIR / f"{site}.json") != expected_sha:
            errors.append(f"{site} storage state changed")
    return errors


def trace_path_for(record: dict) -> Path:
    return ROOT / (record.get("paths", {}).get("trace") or "")


def trace_actions(path: Path) -> list[dict]:
    rows = []
    seen = set()
    try:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                action = event.get("action")
                if not action or event.get("event") in {"llm_request", "llm_provenance"}:
                    continue
                key = (event.get("step"), action, json.dumps(event.get("args"), sort_keys=True))
                if key in seen:
                    continue
                seen.add(key)
                rows.append({"step": event.get("step"), "action": action,
                             "args": event.get("args"), "url": event.get("url")})
    except OSError:
        pass
    return rows


def evaluate_cell(cell: dict, force: bool = False) -> dict:
    arm = arm_dir(cell)
    response = arm / "agent_response.json"
    har = arm / "network.har"
    if not response.is_file():
        raise RuntimeError(f"missing agent_response.json in {arm}")
    evaluation_path = arm / "official_eval.json"
    if force and evaluation_path.is_file():
        evaluation_path.unlink()
    if not evaluation_path.is_file():
        evaluation = evaluate_task_safe(
            cell["task_id"], agent_response_path=response,
            network_trace_path=har if har.is_file() else None, config_path=CONFIG)
        evaluation["_evaluation_binding"] = {
            "agent_response_sha256": file_sha(response),
            "network_har_sha256": file_sha(har) if har.is_file() else None,
            "config_sha256": file_sha(CONFIG),
            "evaluator_adapter_version": "stage-c-eval-adapter/1",
        }
        dump(evaluation_path, evaluation)
    return load(evaluation_path)


def record_cell(cell: dict, timeout_minutes: float) -> dict:
    arm = arm_dir(cell)
    arm.parent.mkdir(parents=True, exist_ok=True)
    log_path = ROOT / cell["output_dir"] / "trial.log"
    env = os.environ.copy()
    env.update(SWITCHES)
    started = time.time()
    completed = subprocess.run(cell["command"], cwd=ROOT, env=env, text=True,
                               capture_output=True, timeout=timeout_minutes * 60)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text((completed.stdout or "") +
                        ("\n[stderr]\n" + completed.stderr if completed.stderr else ""),
                        encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(f"main.py exited {completed.returncode}; see {log_path}")
    evaluation = evaluate_cell(cell, force=True)
    record = load(arm / "trial_record.json")
    evidence_path = arm / "fault_evidence.json"
    evidence = load(evidence_path) if evidence_path.is_file() else {}
    verdict = classify_service_episode(
        evidence, expected_variant=cell["fault_variant"],
        expected_window=WINDOW_STEPS) if evidence else {
            "valid": False, "errors": ["missing fault_evidence.json"], "count": 0}
    errors = list(verdict["errors"])
    if evidence:
        if record.get("run_config", {}).get("fault_evidence_sha256") != file_sha(evidence_path):
            errors.append("fault evidence hash mismatch")
        if record.get("injection_count") != verdict["count"]:
            errors.append("trial injection_count mismatch")
        if (evidence.get("snapshot") or {}).get("target") and \
                evidence["snapshot"]["target"].get("path") != \
                ServiceEndpoint.from_url(cell["endpoint"]).path:
            errors.append("locked endpoint differs from plan")
    if cell["condition"] == "control":
        if verdict["count"] or any(e.get("delivered") for e in evidence.get("exposures", [])):
            errors.append("control contamination")
    else:
        if verdict["count"] == 0:
            errors.append("fault arm did not trigger")
    exposures = evidence.get("exposures") or []
    injection_steps = [e.get("proxy_step") for e in evidence.get("injections") or []]
    error_types = sorted({e.get("error_type") for e in evidence.get("injections") or []})
    target_urls = sorted({e.get("raw_url") for e in exposures
                          if e.get("fault_delivered") and e.get("raw_url")})
    actions = trace_actions(trace_path_for(record))
    post_injection_urls = []
    if injection_steps:
        first = min(injection_steps)
        endpoint = ServiceEndpoint.from_url(cell["endpoint"])
        for action in actions:
            if (action.get("step") or 0) > first and action.get("url"):
                url = action["url"]
                matched, _ = endpoint.matches(url)
                if not matched:
                    post_injection_urls.append({"step": action["step"], "url": url})
    return {
        "cell_key": cell["cell_key"],
        "block": cell["block"],
        "order_in_block": cell["order_in_block"],
        "task_id": cell["task_id"],
        "site": cell["site"],
        "seed": cell["seed"],
        "condition": cell["condition"],
        "steps": record.get("steps"),
        "completed": bool(record.get("completed")),
        "cap_exhausted": bool(record.get("cap_exhausted")),
        "official_success": bool(evaluation.get("official_success")),
        "official_status": evaluation.get("status"),
        "injection_count": record.get("injection_count"),
        "injection_steps": injection_steps,
        "error_types": error_types,
        "target_urls": target_urls,
        "fault_triggered": verdict["count"] > 0,
        "fault_episode_valid": verdict["valid"],
        "protocol_errors": errors,
        "duration_sec": round(time.time() - started, 1),
        "detour_candidate": bool(post_injection_urls),
        "post_injection_urls": post_injection_urls[:8],
        "trace": str(trace_path_for(record).relative_to(ROOT)),
        "agent_response": record.get("paths", {}).get("agent_response"),
        "official_eval": str((arm / "official_eval.json").relative_to(ROOT)),
        "trial_record": str((arm / "trial_record.json").relative_to(ROOT)),
        "fault_evidence": str(evidence_path.relative_to(ROOT)) if evidence_path.is_file() else None,
        "log": str(log_path.relative_to(ROOT)),
    }


def run_cells(plan: dict, run_root: Path, *, block: int | None = None,
              limit: int | None = None, resume: bool = True,
              timeout_minutes: float = 45) -> dict:
    errors = plan_errors(plan)
    if errors:
        raise SystemExit("plan validation failed: " + "; ".join(errors))
    run_root.mkdir(parents=True, exist_ok=True)
    progress_path = run_root / "progress.json"
    progress = load(progress_path) if progress_path.is_file() else {
        "schema": "agent_stress_http_pilot_progress/1", "results": [], "failures": []}
    done = {row["cell_key"] for row in progress["results"]}
    cells = [c for c in plan["cells"] if block is None or c["block"] == block]
    cells.sort(key=lambda c: (c["block"], c["order_in_block"]))
    queue = []
    for cell in cells:
        arm = arm_dir(cell)
        if resume and cell["cell_key"] in done and (arm / "official_eval.json").is_file():
            continue
        queue.append(cell)
    if limit:
        queue = queue[:limit]
    print(json.dumps({"queued": len(queue), "completed": len(done)}, ensure_ascii=False))
    for index, cell in enumerate(queue, 1):
        print(f"[{index}/{len(queue)}] block {cell['block']} order {cell['order_in_block']} "
              f"{cell['cell_key']}", flush=True)
        try:
            row = record_cell(cell, timeout_minutes)
        except Exception as exc:  # noqa: BLE001
            progress["failures"].append({"cell_key": cell["cell_key"],
                                         "error": str(exc), "at": now()})
            print(f"    FAILED: {exc}", flush=True)
        else:
            progress["results"] = [r for r in progress["results"]
                                   if r["cell_key"] != row["cell_key"]] + [row]
            progress["failures"] = [f for f in progress["failures"]
                                    if f["cell_key"] != row["cell_key"]]
            print(f"    success={row['official_success']} steps={row['steps']} "
                  f"injections={row['injection_count']} steps={row['injection_steps']} "
                  f"types={row['error_types']} valid={row['fault_episode_valid']}",
                  flush=True)
        progress["completed"] = len(progress["results"])
        progress["planned"] = len(plan["cells"])
        progress["updated_at"] = now()
        dump(progress_path, progress)
        time.sleep(1)
    summary = {
        "schema": "agent_stress_http_pilot_summary/1",
        "experiment_id": plan["experiment_id"],
        "planned": len(plan["cells"]),
        "completed": len(progress["results"]),
        "failures": progress["failures"],
        "results": sorted(progress["results"], key=lambda r: (r["block"], r["order_in_block"])),
    }
    dump(run_root / "summary.json", summary)
    return summary


def build_report(run_root: Path) -> dict:
    summary = load(run_root / "summary.json")
    rows = summary["results"]
    by_key = {r["cell_key"]: r for r in rows}
    paired = []
    for tid in sorted(TASKS):
        for seed in SEEDS:
            cells = {cond: by_key.get(f"qwen38_flash_task{tid}_react_seed{seed}_{cond}")
                     for cond in CONDITIONS}
            if not all(cells.values()):
                continue
            entry = {"task_id": tid, "site": TASKS[tid]["site"], "seed": seed,
                     "conditions": {}}
            for cond, row in cells.items():
                entry["conditions"][cond] = {
                    "official_success": row["official_success"],
                    "official_status": row["official_status"],
                    "injection_steps": [s - 1 for s in row["injection_steps"]],
                    "injection_proxy_steps": row["injection_steps"],
                    "injection_count": row["injection_count"],
                    "steps": row["steps"],
                    "detour_candidate": row["detour_candidate"],
                    "error_types": row["error_types"],
                    "trace": row["trace"],
                    "official_eval": row["official_eval"],
                }
            control = cells["control"]["steps"]
            entry["extra_steps"] = {cond: (cells[cond]["steps"] - control)
                                    for cond in ("single", "sustained")}
            entry["clean_success_to_fault_fail"] = [
                cond for cond in ("single", "sustained")
                if cells["control"]["official_success"] and not cells[cond]["official_success"]]
            entry["fault_success_clean_fail"] = [
                cond for cond in ("single", "sustained")
                if not cells["control"]["official_success"] and cells[cond]["official_success"]]
            paired.append(entry)
    totals = {}
    for cond in CONDITIONS:
        cond_rows = [r for r in rows if r["condition"] == cond]
        totals[cond] = {
            "trials": len(cond_rows),
            "successes": sum(r["official_success"] for r in cond_rows),
            "extra_steps_vs_clean": sum(
                r["steps"] - by_key.get(f"qwen38_flash_task{r['task_id']}_react_seed{r['seed']}_control",
                                        {}).get("steps", r["steps"])
                for r in cond_rows) if cond != "control" else 0,
            "repeat_exposures": sum(1 for r in cond_rows if r["injection_count"] >= 2),
            "triggered": sum(1 for r in cond_rows if r["fault_triggered"]),
        }
    report = {
        "schema": "agent_stress_http_pilot_report/1",
        "experiment_id": summary["experiment_id"],
        "completed": summary["completed"],
        "failures": summary["failures"],
        "totals": totals,
        "paired": paired,
        "clean_to_fault_fail_blocks": sum(1 for e in paired if e["clean_success_to_fault_fail"]),
        "fault_to_clean_fail_blocks": sum(1 for e in paired if e["fault_success_clean_fail"]),
        "sustained_repeat_exposure_cells": [
            r["cell_key"] for r in rows
            if r["condition"] == "sustained" and r["injection_count"] >= 2],
        "extra_step_share": {
            cond: sorted(({"task_id": e["task_id"], "seed": e["seed"],
                           "extra_steps": e["extra_steps"][cond]}
                          for e in paired), key=lambda x: -x["extra_steps"])
            for cond in ("single", "sustained")},
    }
    dump(run_root / "report.json", report)
    return report


def probe_one(task_id: int, condition: str, out_dir: Path) -> dict:
    from standard_agent.environment.browser import SyncBrowserEnv
    from fault_injection.injector import FaultInjector
    from standard_agent.environment.webarena_config import get_auto_login_headers

    spec = TASKS[task_id]
    task = task_table()[task_id]
    cond = CONDITIONS[condition]
    endpoint = ServiceEndpoint.from_url(spec["endpoint"])
    config = FaultConfig.single_fault(
        "web_http_error", intensity=cond["intensity"], seed=1,
        experiment_id=EXPERIMENT_ID, fault_variant=cond["variant"],
        fault_window_steps=WINDOW_STEPS, fault_target_endpoint=endpoint.spec())
    env = SyncBrowserEnv(headless=True,
                         extra_http_headers=get_auto_login_headers(task["site"]),
                         storage_state=str(AUTH_DIR / f"{task['site']}.json"))
    steps = []

    def text(obs) -> str:
        return getattr(obs, "ax_tree_text", "") or ""

    def note(label, obs, injector):
        content = text(obs)
        steps.append({
            "label": label,
            "url": getattr(obs, "url", None),
            "step": injector._step_counter,
            "fault_page": ("500 Internal Server Error" in content or
                           "503 Service Unavailable" in content),
            "chars": len(content),
        })

    env.start()
    try:
        injector = FaultInjector(env, config)
        bootstrap = env.goto(task["start_url"])
        injector.arm_http_fault()
        note("bootstrap", bootstrap, injector)

        actions = [
            ("target_first", spec["target_first"]),
            ("target_changed", spec["target_changed"]),
            ("other_endpoint", spec["other"][0]),
            ("filler_1", spec["other"][1]),
            ("filler_2", spec["other"][2]),
            ("filler_3", spec["other"][0]),
            ("filler_4", spec["other"][1]),
            ("filler_5", spec["other"][2]),
            ("filler_6", spec["other"][0]),
            ("filler_7", spec["other"][1]),
            ("target_after_window", spec["target_first"]),
        ]
        for label, url in actions:
            note(label, injector.goto(url), injector)
        evidence = {
            "experiment_id": EXPERIMENT_ID,
            "task_id": task_id,
            "condition": condition,
            "endpoint": endpoint.spec(),
            "window_steps": WINDOW_STEPS,
            "variant": cond["variant"],
            "intensity": cond["intensity"],
            "steps": steps,
            "exposures": injector.get_exposure_log(),
            "episode": {
                "snapshot": config.episode.snapshot(),
                "injections": [e for e in config.episode.events
                               if e.get("event") == "injection"],
                "events": config.episode.events,
            },
            "injection_log": injector.get_injection_log(),
        }
        evidence["verdict"] = classify_service_episode(
            {"experiment_id": EXPERIMENT_ID, "variant": cond["variant"],
             "window_steps": WINDOW_STEPS, **evidence["episode"]},
            expected_variant=cond["variant"], expected_window=WINDOW_STEPS)
    finally:
        env.stop()
    out = out_dir / f"task{task_id}_{condition}.json"
    dump(out, evidence)
    return evidence


def probe_summary(probes: list[dict]) -> dict:
    rows = []
    for e in probes:
        steps = {s["label"]: s for s in e["steps"]}
        first = steps.get("target_first", {})
        changed = steps.get("target_changed", {})
        other = steps.get("other_endpoint", {})
        after = steps.get("target_after_window", {})
        rows.append({
            "task_id": e["task_id"], "condition": e["condition"],
            "injections": len(e["episode"]["injections"]),
            "first_fault_page": first.get("fault_page"),
            "changed_query_fault_page": changed.get("fault_page"),
            "other_endpoint_clean": not other.get("fault_page"),
            "post_window_clean": not after.get("fault_page"),
            "verdict": e["verdict"],
        })
    return {"schema": "agent_stress_http_pilot_probe_summary/1", "rows": rows}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["plan", "probe", "run", "report"])
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--block", type=int)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--job-timeout-minutes", type=float, default=45)
    args = parser.parse_args(argv)
    run_root = args.run_root or args.out

    if args.phase == "plan":
        plan = build_plan(args.out)
        dump(args.out / "plan.json", plan)
        dump(args.out / "authorization.json", {
            "schema": "agent_stress_http_pilot_authorization/1",
            "experiment_id": plan["experiment_id"],
            "approved_for_paid_runs": True,
            "approved_at": now(),
            "approved_by": "user request in session",
            "scope": {"tasks": sorted(TASKS), "seeds": list(SEEDS),
                      "conditions": list(CONDITIONS), "max_steps": MAX_STEPS,
                      "window_steps": WINDOW_STEPS, "trials": 18},
            "plan_sha256": plan["plan_sha256"],
        })
        print(json.dumps({"planned": plan["planned_trials"],
                          "blocks": plan["blocks"]}, ensure_ascii=False, indent=2))
        return 0
    if args.phase == "probe":
        probes = []
        for tid in sorted(TASKS):
            for condition in CONDITIONS:
                probes.append(probe_one(tid, condition, args.out / "probe"))
        summary = probe_summary(probes)
        dump(args.out / "probe" / "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    if args.phase == "run":
        plan = load(args.out / "plan.json")
        summary = run_cells(plan, run_root, block=args.block, limit=args.limit,
                            resume=not args.no_resume,
                            timeout_minutes=args.job_timeout_minutes)
        print(json.dumps({"completed": summary["completed"],
                          "failures": len(summary["failures"])}, ensure_ascii=False))
        return 0 if not summary["failures"] else 1
    report = build_report(run_root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
