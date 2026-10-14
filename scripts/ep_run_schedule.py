#!/usr/bin/env python3
"""E-P 调度运行器：消费 ``schedule.json``，**按 entry 逐条件**执行。

与上一版的区别（每一条都对应一次 review 发现的问题）：

* **保留调度的条件顺序**：不再用 ``--condition both`` 把 control/fault 合成一个
  job（那样永远是 control 先跑），而是按 schedule 里的 entry 顺序逐条执行；
* **日志含模型与架构**：``<root>/<variant>/run/<model>/<arch>/<key>.log``，
  两个架构不会互相覆盖；
* **超时与失败停止**：``wait(timeout=)`` 到点即杀；分类日志并写 status；
  infrastructure 错误累计达到阈值即停止本批并返回非零；
* **冻结门槛**：读 ``ep_persistence_frozen_v1.json``，commit 不符或工作区为脏即拒绝；
* **默认只出计划**：真实执行必须显式 ``--execute``。
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from run_baseline import load_tasks, resolve_start_url  # noqa: E402
from scripts.run_fault_matrix import classify_log, command_for  # noqa: E402
from standard_agent.trial_metadata import git_status  # noqa: E402

DEFAULT_FREEZE = ROOT / "docs/ep_persistence_frozen_v1.json"
STOP_AFTER_INFRASTRUCTURE_ERRORS = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schedule", required=True)
    parser.add_argument("--experiment-root", required=True)
    parser.add_argument("--model-profile", default="deepseek_v41_flash")
    parser.add_argument("--architecture", default="react")
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--fault-intensity", default="high")
    parser.add_argument("--job-timeout-minutes", type=int, default=45)
    parser.add_argument("--fault-injection-step", type=int, default=None)
    parser.add_argument("--freeze", default=str(DEFAULT_FREEZE))
    parser.add_argument("--allow-unfrozen", action="store_true",
                        help="跳过冻结门槛（仅调试用）")
    parser.add_argument("--execute", action="store_true",
                        help="真正执行；缺省只生成并打印计划")
    return parser


def plan_jobs(schedule: dict) -> list[dict]:
    """**按 entry** 排 job（不合并 condition），保留调度给出的交错与条件顺序。"""
    jobs = []
    for entry in schedule["entries"]:
        jobs.append({
            "model_profile": entry["model_profile"],
            "architecture": entry["architecture"],
            "fault_type": entry["fault_type"],
            "task_id": int(entry["task_id"]),
            "seed": int(entry["fault_seed"]),
            "variant": entry["variant"],
            "condition": entry["condition"],
            "block": entry["block"],
            "block_order": entry["block_order"],
        })
    return jobs


def job_key(job: dict) -> str:
    return (f"{job['variant']}_{job['condition']}_{job['fault_type']}"
            f"_task{job['task_id']}_seed{job['seed']}")


def log_dir(experiment_root, job: dict) -> Path:
    """日志目录含模型与架构，避免两个架构写同一个文件。"""
    return (Path(experiment_root) / job["variant"] / "run"
            / job["model_profile"] / job["architecture"])


def _commands(args, schedule, jobs):
    tasks = {task["task_id"]: task
             for task in load_tasks(task_ids=sorted({job["task_id"] for job in jobs}))}
    out = []
    for job in jobs:
        task = tasks[job["task_id"]]
        output_root = (Path(args.experiment_root) / job["variant"] / "outputs"
                       / job["model_profile"] / job["architecture"])
        job_args = argparse.Namespace(
            model_profile=job["model_profile"], architecture=job["architecture"],
            max_steps=args.max_steps, fault_intensity=args.fault_intensity,
            fault_injection_step=args.fault_injection_step, trials=1,
            experiment_id=schedule.get("experiment_id", "ep-persistence-v1"),
            fault_variant=job["variant"],
            fault_window_steps=schedule.get("window_steps", 5),
            condition=job["condition"], official_output_root=str(output_root))
        payload = {"task_id": job["task_id"], "seed": job["seed"],
                   "site": (task.get("sites") or [None])[0],
                   "start_url": resolve_start_url(task), "intent": task["intent"],
                   "fault_type": job["fault_type"]}
        out.append({"job": job, "key": job_key(job), "block": job["block"],
                    "command": command_for(job_args, payload),
                    "output_root": str(output_root),
                    "log_path": str(log_dir(args.experiment_root, job) / f"{job_key(job)}.log"),
                    "status_path": str(Path(args.experiment_root) / job["variant"] / "run"
                                       / job["model_profile"] / job["architecture"]
                                       / f"{job_key(job)}.status.json")})
    return out


def check_freeze(args) -> tuple[bool, str]:
    """冻结门槛：commit 必须一致且工作区不脏（可用 --allow-unfrozen 跳过）。"""
    if args.allow_unfrozen:
        return True, "skipped(--allow-unfrozen)"
    freeze_path = Path(args.freeze)
    if not freeze_path.exists():
        return False, f"缺少冻结清单 {freeze_path}"
    frozen = json.loads(freeze_path.read_text(encoding="utf-8"))
    status = git_status(ROOT)
    frozen_commit = str(frozen.get("git_commit") or "")
    if status.get("dirty"):
        return False, "工作区为脏（tracked 文件与 HEAD 不同）"
    # 冻结提交之后只允许冻结清单自身变化（典型的"提交代码→冻结→提交清单"两步）。
    # 任何其它改动都视为已偏离冻结版本。
    changed = subprocess.run(
        ["git", "diff", "--name-only", frozen_commit, "HEAD"],
        cwd=ROOT, capture_output=True, text=True).stdout.split()
    unexpected = [path for path in changed
                  if path != "docs/ep_persistence_frozen_v1.json"]
    if unexpected:
        return False, f"冻结后代码有改动: {unexpected[:3]}"
    return True, f"frozen@{frozen_commit[:12]}"


def run_planned(args, planned, freeze_note) -> int:
    manifest = {"experiment_id": None, "freeze": freeze_note,
                "jobs": [{k: item[k] for k in ("key", "block", "log_path", "status_path")}
                         for item in planned],
                "started_at": time.time()}
    Path(args.experiment_root).mkdir(parents=True, exist_ok=True)
    (Path(args.experiment_root) / "ep_run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    timeout_sec = args.job_timeout_minutes * 60
    infrastructure_errors = 0
    failures = 0
    for item in planned:
        job = item["job"]
        Path(item["log_path"]).parent.mkdir(parents=True, exist_ok=True)
        timed_out = False
        with open(item["log_path"], "w", encoding="utf-8") as log:
            process = subprocess.Popen(item["command"], cwd=ROOT, stdout=log,
                                       stderr=subprocess.STDOUT, text=True,
                                       start_new_session=True)
            print(f"START {item['key']} pid={process.pid}", flush=True)
            try:
                process.wait(timeout=timeout_sec)
            except subprocess.TimeoutExpired:
                timed_out = True
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
        text = Path(item["log_path"]).read_text(encoding="utf-8", errors="replace")
        status = classify_log(text,
                              expect_fault=(job["condition"] == "fault"),
                              fault_variant=job["variant"],
                              window_steps=job.get("window_steps", 5))
        status.update({"variant": job["variant"], "condition": job["condition"],
                       "model_profile": job["model_profile"],
                       "architecture": job["architecture"],
                       "returncode": process.returncode, "timed_out": timed_out})
        if timed_out:
            status["infrastructure_error"] = True
        Path(item["status_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(item["status_path"]).write_text(
            json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"DONE {item['key']} rc={process.returncode}", flush=True)
        if process.returncode not in (0, None) or status.get("infrastructure_error"):
            infrastructure_errors += 1 if status.get("infrastructure_error") else 0
            failures += 1
            if infrastructure_errors >= STOP_AFTER_INFRASTRUCTURE_ERRORS:
                print(f"STOP policy: {infrastructure_errors} infrastructure errors; "
                      "stopping this batch.", flush=True)
                return 1
    return 1 if failures else 0


def main() -> int:
    args = build_parser().parse_args()
    schedule = json.loads(Path(args.schedule).read_text(encoding="utf-8"))
    jobs = plan_jobs(schedule)
    planned = _commands(args, schedule, jobs)
    ok, note = check_freeze(args)
    plan = {"experiment_id": schedule.get("experiment_id"), "jobs": len(planned),
            "variant_order": [p["job"]["variant"] for p in planned],
            "condition_order": [p["job"]["condition"] for p in planned],
            "freeze": note, "freeze_ok": ok,
            "distinct_log_paths": len({p["log_path"] for p in planned}) == len(planned),
            "log_path_sample": planned[0]["log_path"] if planned else None}
    if not args.execute:
        print(json.dumps({"plan_only": True, **plan}, ensure_ascii=False, indent=2))
        return 0
    if not ok:
        print(f"error: 冻结门槛未通过（{note}）；加 --allow-unfrozen 才可调试执行",
              file=sys.stderr)
        return 2
    print(json.dumps({"plan": plan}, ensure_ascii=False))
    return run_planned(args, planned, note)


if __name__ == "__main__":
    raise SystemExit(main())
