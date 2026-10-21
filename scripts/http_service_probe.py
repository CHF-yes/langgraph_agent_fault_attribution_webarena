#!/usr/bin/env python3
"""Plan/run one service-level HTTP four-arm block. No execution without --execute.

Fault object: a frozen search endpoint (scheme + host + port + path segment); query
values and extra parameters never affect matching. This is a development probe for
`ep-http-service-v1`, separate from v2 and from E-P v1. Existing artifacts are never
overwritten and resume is unsupported.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fault_injection.config import FaultConfig
from fault_injection.http_service import (
    DEFAULT_WINDOW_STEPS, EXPERIMENT_ID, ServiceEndpoint,
    classify_service_episode, protocol_metadata,
)
from run_baseline import load_tasks, resolve_start_url
from scripts.ep_run_schedule import _has_run_artifacts, job_key, log_dir, run_planned
from scripts.run_fault_matrix import command_for
from standard_agent.ep_schedule import build_schedule
from standard_agent.stage_c_pipeline import (
    EVALUATOR_ADAPTER_VERSION, current_evaluator_version,
)
from standard_agent.trial_metadata import git_status
from fault_injection.http_service import TARGET_POLICY_VERSION


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-root", required=True)
    parser.add_argument("--endpoint", required=True,
                        help="search endpoint URL, e.g. http://localhost:7770/catalogsearch/result/")
    parser.add_argument("--task-id", type=int, default=118)
    parser.add_argument("--model-profile", default="deepseek_v41_flash")
    parser.add_argument("--architecture", choices=["react", "plan_execute"], default="react")
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--fault-window-steps", type=int, default=DEFAULT_WINDOW_STEPS)
    parser.add_argument("--fault-seed", type=int, default=1)
    parser.add_argument("--schedule-seed", type=int, default=20261021)
    parser.add_argument("--job-timeout-minutes", type=int, default=45)
    parser.add_argument("--freeze", default=str(ROOT / "docs/ep_http_service_v1_frozen.json"),
                        help="候选/正式冻结清单；--execute 必须通过其校验")
    parser.add_argument("--execute", action="store_true")
    return parser


def _sha256(path: str | None):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except (OSError, TypeError):
        return None


def _record_only(path: str) -> bool:
    return (path == "docs/ep_http_service_v1_frozen.json"
            or (path.startswith("experiments/ep_http_service_v1/")
                and (path.endswith(".json") or path.endswith(".md"))))


def check_freeze(args, endpoint) -> tuple[bool, str]:
    """执行前冻结校验：未批准、版本/参数/数据不符都必须在起子进程之前拒绝。"""
    freeze_path = Path(args.freeze)
    if not freeze_path.exists():
        return False, f"缺少冻结清单 {freeze_path}"
    frozen = json.loads(freeze_path.read_text(encoding="utf-8"))
    if frozen.get("experiment_id") != EXPERIMENT_ID:
        return False, "冻结清单 experiment_id 不符"
    if frozen.get("status") != "frozen":
        return False, f"冻结清单状态为 {frozen.get('status')!r}，不是 frozen"
    if frozen.get("approved_for_paid_runs") is not True:
        return False, "冻结清单未批准付费运行（approved_for_paid_runs != true）"
    if frozen.get("target_policy_version") != TARGET_POLICY_VERSION:
        return False, "目标策略版本不符"
    if (frozen.get("endpoint") or {}).get("scheme") != endpoint.scheme \
            or (frozen.get("endpoint") or {}).get("host") != endpoint.host \
            or (frozen.get("endpoint") or {}).get("port") != endpoint.port \
            or (frozen.get("endpoint") or {}).get("path") != endpoint.path:
        return False, "endpoint 与冻结清单不符"
    if frozen.get("window_steps") != args.fault_window_steps:
        return False, f"window_steps 不符（{args.fault_window_steps} != {frozen.get('window_steps')}）"
    if frozen.get("max_steps") != args.max_steps:
        return False, f"max_steps 不符（{args.max_steps} != {frozen.get('max_steps')}）"
    if frozen.get("fault_intensity") != "high":
        return False, "fault_intensity 与协议默认不符"
    if frozen.get("fault_types") != ["web_http_error"]:
        return False, "fault_types 与冻结清单不符"
    if frozen.get("evaluator_adapter_version") != EVALUATOR_ADAPTER_VERSION:
        return False, "evaluator adapter 版本不符"
    current_eval = current_evaluator_version()
    frozen_eval = frozen.get("evaluator_version") or {}
    if frozen_eval.get("package_sha256") != current_eval.get("package_sha256"):
        return False, "evaluator 源码指纹不符"
    dataset = os.getenv("WEBARENA_DATASET")
    if frozen.get("dataset_sha256") and _sha256(dataset) != frozen.get("dataset_sha256"):
        return False, "数据集哈希不符（或无法读取数据集）"

    status = git_status(ROOT)
    if status.get("dirty") is not False:
        return False, f"工作区状态不可确认或不干净: dirty={status.get('dirty')!r}"
    frozen_commit = str(frozen.get("git_commit") or "")
    if not frozen_commit:
        return False, "冻结清单缺少 git_commit"
    diff = subprocess.run(["git", "diff", "--name-only", frozen_commit, "HEAD"],
                          cwd=ROOT, capture_output=True, text=True)
    if diff.returncode != 0:
        return False, f"git diff 失败（rc={diff.returncode}）"
    unexpected = [path for path in diff.stdout.split() if not _record_only(path)]
    if unexpected:
        return False, f"冻结后代码有改动: {unexpected[:3]}"
    return True, f"frozen@{frozen_commit[:12]}"


def build_plan(args):
    endpoint = ServiceEndpoint.from_url(args.endpoint)
    config = FaultConfig.single_fault(
        "web_http_error", intensity="high", seed=args.fault_seed,
        experiment_id=EXPERIMENT_ID, fault_variant="persistent",
        fault_window_steps=args.fault_window_steps,
        fault_target_endpoint=endpoint.spec())
    if args.max_steps < 1 or args.job_timeout_minutes < 1:
        raise ValueError("max_steps and job_timeout_minutes must be positive")
    root = Path(args.experiment_root).resolve()
    for forbidden in ("ep_persistence_v1", "ep_http_persistence_v2"):
        if root.is_relative_to((ROOT / "experiments" / forbidden).resolve()):
            raise ValueError(f"service v1 outputs must not be written inside {forbidden}")
    tasks = load_tasks(task_ids=[args.task_id])
    if len(tasks) != 1 or tasks[0]["task_id"] != args.task_id:
        raise ValueError(f"Task {args.task_id} was not found uniquely in the dataset")
    task = tasks[0]
    sites = task.get("sites") or []
    if not sites:
        raise ValueError("Task has no site")
    schedule = build_schedule(
        models=[args.model_profile], architectures=[args.architecture],
        faults=["web_http_error"], tasks=[args.task_id], seeds=[args.fault_seed],
        experiment_id=EXPERIMENT_ID, variants=("single", "persistent"),
        schedule_seed=args.schedule_seed)
    schedule.update(window_steps=args.fault_window_steps,
                    protocol=protocol_metadata(config),
                    endpoint=endpoint.spec())
    planned = []
    for entry in schedule["entries"]:
        job = {**entry, "seed": entry["fault_seed"], "window_steps": args.fault_window_steps}
        run_dir = log_dir(root, job)
        output_root = root / job["variant"] / "outputs" / args.model_profile / args.architecture
        child_args = argparse.Namespace(
            model_profile=args.model_profile, architecture=args.architecture,
            max_steps=args.max_steps, fault_intensity="high", fault_injection_step=None,
            experiment_id=EXPERIMENT_ID, fault_variant=job["variant"],
            fault_window_steps=args.fault_window_steps,
            fault_target_url=None, fault_target_endpoint=args.endpoint,
            condition=job["condition"], official_output_root=str(output_root))
        command = command_for(child_args, {
            **job, "site": sites[0], "start_url": resolve_start_url(task),
            "intent": task["intent"]})
        planned.append({"job": job, "key": job_key(job), "block": job["block"],
                        "command": command,
                        "log_path": str(run_dir / f"{job_key(job)}.log"),
                        "status_path": str(run_dir / f"{job_key(job)}.status.json"),
                        "trial_dir": str(output_root
                                         / f"web_http_error_task{args.task_id}_seed{args.fault_seed}"
                                         / str(args.task_id)
                                         / (f"control_seed_{args.fault_seed}"
                                            if job["condition"] == "control"
                                            else f"web_http_error_seed_{args.fault_seed}"))})
    return schedule, planned, endpoint


def validate_trial_artifacts(item, args):
    from standard_agent.stage_c_pipeline import arm_artifacts_ok
    job = item["job"]
    arm = Path(item["trial_dir"])
    expected = {"experiment_id": EXPERIMENT_ID, "fault_variant": job["variant"],
                "model_profile": job["model_profile"], "architecture": job["architecture"],
                "task_id": job["task_id"], "fault_seed": job["seed"],
                "fault_type": "web_http_error", "condition": job["condition"],
                "max_steps": args.max_steps,
                "fault_intensity": "off" if job["condition"] == "control" else "high"}
    _, errors = arm_artifacts_ok(arm, expect=expected)
    try:
        record = json.loads((arm / "trial_record.json").read_text())
        raw = (arm / "fault_evidence.json").read_bytes()
        data = json.loads(raw)
        config = record["run_config"]
        verdict = classify_service_episode(data, expected_variant=job["variant"],
                                           expected_window=args.fault_window_steps)
        errors.extend(verdict["errors"])
        if config.get("fault_evidence_sha256") != hashlib.sha256(raw).hexdigest():
            errors.append("fault evidence hash mismatch")
        if record.get("injection_count") != verdict["count"]:
            errors.append("trial injection_count mismatch")
        if job["condition"] == "control" and verdict["count"]:
            errors.append("control contamination")
        endpoint = ServiceEndpoint.from_url(args.endpoint)
        expected_protocol = protocol_metadata(FaultConfig(
            experiment_id=EXPERIMENT_ID, fault_variant=job["variant"],
            fault_window_steps=args.fault_window_steps,
            fault_target_endpoint=endpoint.spec()))
        if any(config.get(key) != value for key, value in expected_protocol.items()):
            errors.append("protocol metadata mismatch")
        if config.get("injection_step") is not None:
            errors.append("unexpected fixed injection_step")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(f"missing or invalid protocol artifacts: {exc}")
    return errors


def main():
    args = build_parser().parse_args()
    try:
        schedule, planned, endpoint = build_plan(args)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    summary = {"experiment_id": EXPERIMENT_ID, "development_probe": True,
               "frozen": False, "plan_only": not args.execute, "trials": len(planned),
               "endpoint": endpoint.spec(), "protocol": schedule["protocol"],
               "commands": [p["command"] for p in planned]}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not args.execute:
        return 0
    root = Path(args.experiment_root)
    ok, freeze_note = check_freeze(args, endpoint)
    if not ok:
        print(f"error: 冻结校验未通过：{freeze_note}", file=sys.stderr)
        return 2
    if _has_run_artifacts(root):
        print("error: output directory already contains run artifacts; use a fresh directory",
              file=sys.stderr)
        return 3
    root.mkdir(parents=True, exist_ok=True)
    (root / "schedule.json").write_text(json.dumps(schedule, ensure_ascii=False, indent=2) + "\n",
                                       encoding="utf-8")
    args.resume = False
    return run_planned(args, planned, freeze_note, EXPERIMENT_ID,
                       verify_trial=validate_trial_artifacts)


if __name__ == "__main__":
    raise SystemExit(main())
