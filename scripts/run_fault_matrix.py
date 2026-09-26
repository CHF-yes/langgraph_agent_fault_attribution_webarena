#!/usr/bin/env python3
"""Run a bounded, stoppable control/fault matrix with isolated processes."""

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from run_baseline import load_tasks, resolve_start_url
from standard_agent.stage_c_pipeline import job_arms_ok
from standard_agent.trial_metadata import PAIRING_SCHEME, TRIAL_RECORD_SCHEMA_VERSION
from standard_agent.webarena_verified import dataset_path
from fault_injection.config import (
    injection_step_mode,
    injection_step_units,
    resolve_injection_step,
)

# Two-model 16-task main matrix. Keep aligned with
# docs/task_manifest_public16.json. Pass --task-ids with that manifest's
# eight validation IDs when running the third-model Pro matrix.
BASELINE_TASK_IDS = [
    21, 22, 24, 25,      # shopping review retrieval
    27, 28, 30, 66,      # reddit post retrieval
    132, 133, 134, 308,  # gitlab public repository retrieval
    102, 118, 258, 274,  # public navigation
]


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--trials", type=int, default=2)
    parser.add_argument("--fault-type", default="web_dom_missing")
    parser.add_argument("--fault-types", nargs="+", default=None,
                        help="Run multiple fault types in one matrix")
    parser.add_argument("--fault-intensity", default="high")
    parser.add_argument("--fault-seed", type=int, default=1)
    parser.add_argument("--fault-injection-step", type=int, default=None)
    parser.add_argument("--model-profile", default="qwen38_flash")
    parser.add_argument("--architecture", default="react")
    parser.add_argument("--task-ids", type=int, nargs="*", default=None)
    parser.add_argument("--resume", action="store_true",
                        help="Skip jobs with an existing status JSON in output-dir")
    parser.add_argument("--job-timeout-minutes", type=int, default=45,
                        help="Stop the matrix if one job exceeds this duration")
    parser.add_argument("--official-output-root", default=None,
                        help="Root for per-job HAR/agent_response/evaluator outputs")
    return parser.parse_args()



def design_fingerprint(args, fault_types, injection_steps, task_ids) -> str:
    """指纹化本次正式运行的设计，用于阻止把旧产物当成同一试验恢复。

    历史产物（control_seed_0、无 trial_record.json）与本次运行共享
    ``{fault}_task{id}_seed{seed}.status.json`` 这个键格式。若只按文件名判断
    "已完成"，指向旧输出目录的 ``--resume`` 会静默跳过本该重跑的格子。因此把
    与结论相关的设计要素（模型、架构、步数、故障集、注入步、重复数、任务集、
    配对方案与记录 schema）哈希进 manifest 和每个 status；恢复时只接受指纹一致的。
    """
    payload = {
        "pairing_scheme": PAIRING_SCHEME,
        "trial_record_schema": TRIAL_RECORD_SCHEMA_VERSION,
        "model_profile": args.model_profile,
        "architecture": args.architecture,
        "max_steps": args.max_steps,
        "fault_types": sorted(fault_types),
        "fault_intensity": args.fault_intensity,
        "injection_steps": {key: injection_steps[key] for key in sorted(injection_steps)},
        "trials_per_task": args.trials,
        "task_ids": sorted({int(task_id) for task_id in task_ids}),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def task_jobs(args):
    task_ids = args.task_ids if args.task_ids is not None else BASELINE_TASK_IDS
    tasks = load_tasks(task_ids=task_ids)
    if args.task_ids is not None:
        order = {task_id: index for index, task_id in enumerate(args.task_ids)}
        tasks.sort(key=lambda task: order[task["task_id"]])
    jobs = []
    fault_types = args.fault_types or [args.fault_type]
    for fault_type in fault_types:
      for task in tasks:
        site = (task.get("sites") or [None])[0]
        start_url = resolve_start_url(task)
        for seed_index in range(args.trials):
            seed = args.fault_seed + seed_index
            jobs.append({
                "task_id": task["task_id"],
                "seed": seed,
                "site": site,
                "start_url": start_url,
                "intent": task["intent"],
                "fault_type": fault_type,
            })
    return jobs


def command_for(args, job):
    # job 里通常带 fault_type；没有时才回落到 args，避免在 job 有值时仍去读
    # 一个可能不存在的属性（旧写法 job.get(k, args.k) 会立即求值 args.k）。
    fault_type = job.get("fault_type") or getattr(args, "fault_type", None)
    # Single source of truth: Stage C pins the step per fault (agent_param_error
    # on the first parameter action, others on the second step); an explicit
    # --fault-injection-step is a Stage E sweep and is passed through as given.
    injection_step = resolve_injection_step(
        fault_type, getattr(args, "fault_injection_step", None)
    )
    command = [
        sys.executable, "main.py", "--benchmark",
        "--site", job["site"], "--url", job["start_url"],
        "--task", job["intent"],
        "--task-id", str(job["task_id"]),
        "--model-profile", args.model_profile,
        "--architecture", args.architecture,
        "--max-steps", str(args.max_steps), "--trials", "1",
        "--fault-type", fault_type,
        "--fault-intensity", args.fault_intensity,
        "--fault-seed", str(job["seed"]),
        "--fault-injection-step", str(injection_step),
    ]
    if getattr(args, "official_output_root", None):
        output = Path(args.official_output_root) / (
            f"{fault_type}_task{job['task_id']}_seed{job['seed']}"
        )
        command += ["--webarena-output-dir", str(output)]
    return command


def classify_log(text):
    infrastructure_patterns = (
        r"Traceback \(most recent call last\)",
        r"(?:HTTP|status|status_code|response[_ ]code)[^\n]{0,24}(?:429|502|503)",
        r"(?:429|502|503) (?:Too Many Requests|Bad Gateway|Service Unavailable)",
        r"Connection refused",
        r"net::ERR_[A-Z_]+",
        r"EPIPE",
        r"write after end",
        r"Target (?:page|context|browser) has been closed",
        r"Browser has been closed",
    )
    task_error = ("浏览器模式出错", "❌ steps=", "ERROR")
    fault_lines = re.findall(r"injection_count=(\d+) fault_seed=(\d+) faults=\[(.*?)\]", text)
    fault_records = [
        (int(count), int(seed), faults)
        for count, seed, faults in fault_lines
        if seed != 0
    ]
    fault_counts = [count for count, _, faults in fault_records if faults.strip()]
    fault_labels = [faults for _, _, faults in fault_records]
    fault_triggered = any(count == 1 for count in fault_counts)
    fault_invalid = not fault_counts or any(count != 1 for count in fault_counts)
    infrastructure_error = any(
        re.search(pattern, text, re.IGNORECASE)
        for pattern in infrastructure_patterns
    )
    # A synthetic 500/503 is the expected payload of web_http_error, not an
    # external service failure. Keep genuine connection/API errors detectable.
    if fault_triggered and any("web_http_error" in label for label in fault_labels):
        infrastructure_error = False
    return {
        "infrastructure_error": infrastructure_error,
        "task_error": any(marker in text for marker in task_error),
        "fault_missing": fault_invalid,
        "fault_count": fault_counts[-1] if fault_counts else 0,
        "fault_triggered": fault_triggered,
        "fault_invalid": fault_invalid,
    }


def stop_all(active):
    for process, _, _ in active.values():
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    for _, log_file, _ in active.values():
        log_file.close()
    deadline = time.time() + 10
    while active and time.time() < deadline:
        for key, (process, _, _) in list(active.items()):
            if process.poll() is not None:
                active.pop(key, None)
        time.sleep(0.1)
    for process, _, _ in active.values():
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def main():
    args = parse_args()
    if args.workers < 1 or args.trials < 1:
        raise SystemExit("workers and trials must be positive")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    jobs = task_jobs(args)
    fault_types = args.fault_types or [args.fault_type]
    injection_steps = {
        fault_type: resolve_injection_step(fault_type, args.fault_injection_step)
        for fault_type in fault_types
    }
    # Record the step on every job so a status/record file is self-describing
    # even when the manifest is separated from the logs.
    for job in jobs:
        job["injection_step"] = injection_steps[job.get("fault_type", args.fault_type)]
        job["injection_step_units"] = injection_step_units(
            job.get("fault_type", args.fault_type)
        )
    fingerprint = design_fingerprint(args, fault_types, injection_steps,
                                     [job["task_id"] for job in jobs])
    stale_status_files = []
    resume_rerun = []
    resume_skipped = []
    if args.resume:
        completed_keys = set()
        for path in output_dir.glob("*.status.json"):
            try:
                status = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            # 只认本次设计的指纹；旧产物（例如 control_seed_0 时代、无
            # trial_record.json）指纹不同，必须重跑而不是被跳过。
            if status.get("design_fingerprint") != fingerprint:
                stale_status_files.append(path.name)
                continue
            if not (status.get("returncode") == 0
                    and not status.get("infrastructure_error")
                    and not status.get("fault_invalid")):
                continue
            # 日志分类通过还不够：必须两臂的 agent_response / network.har /
            # trial_record 都完整且与当前设计一致，才允许跳过这个格子。
            key = path.name.removesuffix(".status.json")
            job = next((item for item in jobs if (
                f"{item['fault_type']}_task{item['task_id']}_seed{item['seed']}") == key), None)
            if job is None:
                continue
            job_dir = (Path(args.official_output_root)
                       / f"{job['fault_type']}_task{job['task_id']}_seed{job['seed']}"
                       if getattr(args, "official_output_root", None) else None)
            if job_dir is None:
                resume_rerun.append({"key": key, "why": "no --official-output-root to verify"})
                continue
            ok, why = job_arms_ok(
                job_dir, fault_type=job["fault_type"], task_id=job["task_id"],
                seed=job["seed"], model_profile=args.model_profile,
                architecture=args.architecture, max_steps=args.max_steps,
                injection_step=injection_steps[job["fault_type"]],
                fault_intensity=args.fault_intensity,
                dataset_path=dataset_path(),
            )
            if ok:
                completed_keys.add(key)
                resume_skipped.append(key)
            else:
                resume_rerun.append({"key": key, "why": "; ".join(why)[:500]})
        jobs = [
            job for job in jobs
            if f"{job['fault_type']}_task{job['task_id']}_seed{job['seed']}" not in completed_keys
        ]
        for key in stale_status_files:
            print(f"RESUME: ignored stale status file {key} (design fingerprint differs)",
                  flush=True)
        for item in resume_rerun:
            print(f"RESUME: rerun {item['key']} ({item['why']})", flush=True)
    manifest = {
        "design_fingerprint": fingerprint,
        "pairing_scheme": PAIRING_SCHEME,
        "trial_record_schema": TRIAL_RECORD_SCHEMA_VERSION,
        "resume_ignored_stale_status_files": stale_status_files,
        "resume_skipped_keys": resume_skipped,
        "resume_rerun": resume_rerun,
        "model_profile": args.model_profile,
        "architecture": args.architecture,
        "max_steps": args.max_steps,
        "fault_types": fault_types,
        "fault_intensity": args.fault_intensity,
        "fault_injection_steps": injection_steps,
        "injection_step_mode": injection_step_mode(args.fault_injection_step),
        "injection_step_units": {
            fault_type: injection_step_units(fault_type) for fault_type in fault_types
        },
        "workers": args.workers,
        "trials_per_task": args.trials,
        "total_jobs": len(jobs),
        "total_trials_including_control": len(jobs) * 2,
        "task_ids": sorted({job["task_id"] for job in jobs}),
        "stop_policy": {
            "infrastructure_errors": 2,
            "fault_missing_in_last_8": 2,
            "task_errors": "record_only",
        },
        "jobs": jobs,
        "resume": args.resume,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    pending = deque(jobs)
    active = {}
    completed = 0
    infrastructure_errors = 0
    recent = deque(maxlen=8)
    stopped = False
    started_at = {}
    job_timeout_sec = args.job_timeout_minutes * 60

    while pending or active:
        while pending and len(active) < args.workers and not stopped:
            job = pending.popleft()
            key = f"{job['fault_type']}_task{job['task_id']}_seed{job['seed']}"
            log_path = output_dir / f"{key}.log"
            log_file = log_path.open("w", encoding="utf-8")
            process = subprocess.Popen(
                command_for(args, job), cwd=ROOT, stdout=log_file,
                stderr=subprocess.STDOUT, text=True,
                start_new_session=True,
            )
            active[key] = (process, log_file, job)
            started_at[key] = time.time()
            print(f"START {key} pid={process.pid}", flush=True)

        for key, (process, log_file, job) in list(active.items()):
            if process.poll() is None and time.time() - started_at[key] > job_timeout_sec:
                print(f"TIMEOUT {key}; recording timeout and continuing.", flush=True)
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                time.sleep(0.2)
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                log_file.close()
                result = {
                    "job": job,
                    "design_fingerprint": fingerprint,
                    "returncode": process.returncode,
                    "infrastructure_error": True,
                    "task_error": False,
                    "fault_missing": True,
                    "fault_count": 0,
                    "fault_triggered": False,
                    "fault_invalid": True,
                    "timed_out": True,
                }
                (output_dir / f"{key}.status.json").write_text(
                    json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                active.pop(key)
                started_at.pop(key, None)
                completed += 1
                infrastructure_errors += 1
                continue
            if process.poll() is None:
                continue
            log_file.close()
            text = (output_dir / f"{key}.log").read_text(encoding="utf-8", errors="replace")
            status = classify_log(text)
            result = {"job": job, "design_fingerprint": fingerprint,
                      "returncode": process.returncode, **status, "timed_out": False}
            (output_dir / f"{key}.status.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            active.pop(key)
            started_at.pop(key, None)
            completed += 1
            recent.append(status)
            infrastructure_errors += int(status["infrastructure_error"])
            recent_missing = sum(item["fault_missing"] for item in recent)
            print(
                f"DONE {key} rc={process.returncode} "
                f"injection={'yes' if status['fault_triggered'] else 'no'} "
                f"infra={status['infrastructure_error']} task_error={status['task_error']}",
                flush=True,
            )
            if infrastructure_errors >= 2 or recent_missing >= 2:
                print("STOP policy threshold reached; terminating active jobs.", flush=True)
                stopped = True
                pending.clear()
                stop_all(active)
                break
        if active or (pending and not stopped):
            time.sleep(0.5)

    summary = {
        "completed": completed,
        "scheduled": len(jobs),
        "stopped": stopped,
        "infrastructure_errors": infrastructure_errors,
        "remaining_jobs": len(pending),
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 2 if stopped else 0


if __name__ == "__main__":
    raise SystemExit(main())
