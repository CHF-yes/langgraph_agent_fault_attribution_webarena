#!/usr/bin/env python3
"""Run a bounded, stoppable control/fault matrix with isolated processes."""

import argparse
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

# Frozen no-login Stage C sample. Keep this list aligned with
# docs/task_manifest_noauth4.json. Map is excluded because the experiment
# server cannot provision its external data volumes.
BASELINE_TASK_IDS = [
    118, 124,  # shopping: one navigate and one retrieve task
    27,        # reddit: public retrieval task
    102,       # gitlab: public navigation task
]


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--fault-type", default="web_dom_missing")
    parser.add_argument("--fault-types", nargs="+", default=None,
                        help="Run multiple fault types in one matrix")
    parser.add_argument("--fault-intensity", default="high")
    parser.add_argument("--fault-seed", type=int, default=1)
    parser.add_argument("--fault-injection-step", type=int, default=None)
    parser.add_argument("--model-profile", default="gpt54")
    parser.add_argument("--architecture", default="react")
    parser.add_argument("--task-ids", type=int, nargs="*", default=None)
    parser.add_argument("--resume", action="store_true",
                        help="Skip jobs with an existing status JSON in output-dir")
    parser.add_argument("--job-timeout-minutes", type=int, default=45,
                        help="Stop the matrix if one job exceeds this duration")
    parser.add_argument("--official-output-root", default=None,
                        help="Root for per-job HAR/agent_response/evaluator outputs")
    return parser.parse_args()


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
    fault_type = job.get("fault_type", args.fault_type)
    injection_step = args.fault_injection_step
    if injection_step is None:
        injection_step = 1 if fault_type == "agent_param_error" else 2
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
        fault_type: (
            args.fault_injection_step
            if args.fault_injection_step is not None
            else (1 if fault_type == "agent_param_error" else 2)
        )
        for fault_type in fault_types
    }
    if args.resume:
        completed_keys = set()
        for path in output_dir.glob("*.status.json"):
            try:
                status = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if (
                status.get("returncode") == 0
                and not status.get("infrastructure_error")
                and not status.get("fault_invalid")
            ):
                completed_keys.add(path.name.removesuffix(".status.json"))
        jobs = [
            job for job in jobs
            if f"{job['fault_type']}_task{job['task_id']}_seed{job['seed']}" not in completed_keys
        ]
    manifest = {
        "model_profile": args.model_profile,
        "architecture": args.architecture,
        "max_steps": args.max_steps,
        "fault_types": fault_types,
        "fault_intensity": args.fault_intensity,
        "fault_injection_steps": injection_steps,
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
            result = {"job": job, "returncode": process.returncode, **status,
                      "timed_out": False}
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
