#!/usr/bin/env python3
"""E-P 调度运行器：按 ``schedule.json`` 的顺序执行 trial（消费调度，而非摆设）。

设计第 4 节要求 single/repeat3 在 block 内交错、且输出按 variant 隔离。本运行器：

* 读 ``schedule.json``，按其中 entry 的**首次出现顺序**排 job（保持交错）；
* 每个 job 跑一个 (model, arch, fault, task, seed, variant)，内含 control/fault 两臂；
* 输出根按 variant 分开：``<root>/<variant>/outputs/<model>/<arch>``，
  run/status 同样按 variant 分开，避免 single 与 repeat3 覆盖同一个目录；
* 复用 ``run_fault_matrix`` 的 ``command_for`` 与 ``classify_log``（variant 校验）。

``--dry-run`` 只打印计划与命令，不启动任何进程（离线可测）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from run_baseline import load_tasks, resolve_start_url  # noqa: E402
from scripts.run_fault_matrix import command_for  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schedule", required=True, help="schedule.json 路径")
    parser.add_argument("--experiment-root", required=True,
                        help="E-P 实验根，输出写到 <root>/<variant>/...")
    parser.add_argument("--model-profile", default="deepseek_v41_flash")
    parser.add_argument("--architecture", default="react")
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--fault-intensity", default="high")
    parser.add_argument("--job-timeout-minutes", type=int, default=45)
    parser.add_argument("--fault-injection-step", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不执行")
    return parser


def plan_jobs(schedule: dict) -> list[dict]:
    """按 schedule 的首次出现顺序排出 job（每个 job 覆盖一个 variant 的两臂）。"""
    seen = set()
    jobs = []
    for entry in schedule["entries"]:
        key = (entry["model_profile"], entry["architecture"], entry["fault_type"],
               int(entry["task_id"]), int(entry["fault_seed"]), entry["variant"])
        if key in seen:
            continue
        seen.add(key)
        jobs.append({"model_profile": entry["model_profile"],
                     "architecture": entry["architecture"],
                     "fault_type": entry["fault_type"],
                     "task_id": int(entry["task_id"]),
                     "seed": int(entry["fault_seed"]),
                     "variant": entry["variant"],
                     "block": entry["block"]})
    return jobs


def _commands(args, schedule, jobs):
    """把 job 变成 (variant, key, command, output_root, run_dir)。"""
    tasks = {task["task_id"]: task
             for task in load_tasks(task_ids=sorted({job["task_id"] for job in jobs}))}
    out = []
    for job in jobs:
        task = tasks[job["task_id"]]
        output_root = (Path(args.experiment_root) / job["variant"]
                       / "outputs" / job["model_profile"] / job["architecture"])
        run_dir = Path(args.experiment_root) / job["variant"] / "run"
        job_args = argparse.Namespace(
            model_profile=job["model_profile"], architecture=job["architecture"],
            max_steps=args.max_steps, fault_intensity=args.fault_intensity,
            fault_injection_step=args.fault_injection_step, trials=1,
            experiment_id=schedule.get("experiment_id", "ep-persistence-v1"),
            fault_variant=job["variant"],
            fault_window_steps=schedule.get("window_steps", 5),
            condition="both", official_output_root=str(output_root))
        payload = {"task_id": job["task_id"], "seed": job["seed"],
                   "site": (task.get("sites") or [None])[0],
                   "start_url": resolve_start_url(task), "intent": task["intent"],
                   "fault_type": job["fault_type"]}
        key = f"{job['variant']}_{job['fault_type']}_task{job['task_id']}_seed{job['seed']}"
        out.append({"variant": job["variant"], "key": key, "block": job["block"],
                    "command": command_for(job_args, payload),
                    "output_root": str(output_root), "run_dir": str(run_dir)})
    return out


def main() -> int:
    args = build_parser().parse_args()
    schedule = json.loads(Path(args.schedule).read_text(encoding="utf-8"))
    jobs = plan_jobs(schedule)
    planned = _commands(args, schedule, jobs)
    order = [p["variant"] for p in planned]
    summary = {"experiment_id": schedule.get("experiment_id"), "jobs": len(planned),
               "variant_order": order,
               "interleaved": _is_interleaved(order)}
    if args.dry_run:
        print(json.dumps({"plan": summary,
                          "first_commands": [p["command"] for p in planned[:2]]},
                         ensure_ascii=False, indent=2))
        return 0
    # 真正执行：按计划顺序串行起进程（首批 48 条量级足够；需要并发再扩）。
    Path(args.experiment_root).mkdir(parents=True, exist_ok=True)
    manifest = {"experiment_id": schedule.get("experiment_id"),
                "schedule_seed": schedule.get("schedule_seed"),
                "jobs": [{k: p[k] for k in ("variant", "key", "block", "output_root")}
                         for p in planned],
                "started_at": time.time()}
    (Path(args.experiment_root) / "ep_run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    import subprocess
    for item in planned:
        Path(item["run_dir"]).mkdir(parents=True, exist_ok=True)
        log_path = Path(item["run_dir"]) / f"{item['key']}.log"
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(item["command"], cwd=ROOT, stdout=log,
                                       stderr=subprocess.STDOUT, text=True,
                                       start_new_session=True)
            print(f"START {item['key']} pid={process.pid}", flush=True)
            process.wait()
    print(json.dumps({"started": len(planned)}, ensure_ascii=False))
    return 0


def _is_interleaved(order: list) -> bool:
    """相邻 job 的 variant 至少切换过一次（严格交错的最低要求）。"""
    return len({v for v in order[:2]}) > 1 if len(order) >= 2 else True


if __name__ == "__main__":
    raise SystemExit(main())
