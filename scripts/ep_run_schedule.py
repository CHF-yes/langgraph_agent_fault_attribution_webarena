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
from fault_injection.config import resolve_injection_step  # noqa: E402
from scripts.run_fault_matrix import classify_log, command_for  # noqa: E402
from standard_agent.stage_c_pipeline import job_arms_ok  # noqa: E402
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
    parser.add_argument("--resume", action="store_true",
                        help="续跑：跳过已跑完（非基础设施/机制错误）的 job")
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


def check_freeze(args, schedule: dict | None = None) -> tuple[bool, str]:
    """冻结门槛：代码版本、工作区、数据集与实际运行配置都必须吻合。

    任一环节**无法验证**（git 不可读、命令非零退出、数据集读不到）都判为不通过，
    绝不"读不到就当通过"。
    """
    if args.allow_unfrozen:
        return True, "skipped(--allow-unfrozen)"
    freeze_path = Path(args.freeze)
    if not freeze_path.exists():
        return False, f"缺少冻结清单 {freeze_path}"
    frozen = json.loads(freeze_path.read_text(encoding="utf-8"))
    frozen_commit = str(frozen.get("git_commit") or "")
    if not frozen_commit:
        return False, "冻结清单缺少 git_commit"

    status = git_status(ROOT)
    if status.get("dirty") is not False:
        # dirty 为 True 或**未知**（None）都不放行。
        return False, f"工作区状态不可确认或不干净: dirty={status.get('dirty')!r}"
    diff = subprocess.run(["git", "diff", "--name-only", frozen_commit, "HEAD"],
                          cwd=ROOT, capture_output=True, text=True)
    if diff.returncode != 0:
        return False, f"git diff 失败（rc={diff.returncode}），无法确认冻结版本"
    # 冻结提交之后只允许**记录类**文件变化：冻结清单本身，以及
    # experiments/ep_persistence_v1/ 下的 JSON 记录（验收/成本等）。任何代码、
    # 配置或产物契约的改动都视为偏离冻结版本。
    def _allowed(path: str) -> bool:
        return (path == "docs/ep_persistence_frozen_v1.json"
                or (path.startswith("experiments/ep_persistence_v1/")
                    and path.endswith(".json")))

    unexpected = [path for path in diff.stdout.split() if not _allowed(path)]
    if unexpected:
        return False, f"冻结后代码有改动: {unexpected[:3]}"

    # 数据集哈希
    dataset = os.getenv("WEBARENA_DATASET")
    expected_sha = frozen.get("dataset_sha256")
    if expected_sha:
        if not dataset or not Path(dataset).exists():
            return False, "无法读取数据集，不能校验数据集哈希"
        from standard_agent.stage_c_pipeline import _sha256_file
        actual = _sha256_file(Path(dataset))
        if actual != expected_sha:
            return False, f"数据集哈希不符（{str(actual)[:12]} != {expected_sha[:12]}）"

    # 运行参数必须与冻结清单一致：否则"换了 max_steps/强度/注入步"也会显示冻结通过。
    for field, actual in (("max_steps", getattr(args, "max_steps", None)),
                          ("fault_intensity", getattr(args, "fault_intensity", None)),
                          ("fault_injection_step", getattr(args, "fault_injection_step", None))):
        if field in frozen and frozen.get(field) != actual:
            return False, (f"运行参数 {field}={actual!r} 与冻结清单 "
                           f"{frozen.get(field)!r} 不符")

    # 运行配置：实验身份、窗口、目标策略版本
    from fault_injection.targets import TARGET_POLICY_VERSION
    if TARGET_POLICY_VERSION != frozen.get("target_policy_version"):
        return False, (f"目标策略版本不符（{TARGET_POLICY_VERSION} != "
                       f"{frozen.get('target_policy_version')}）")
    if schedule is not None:
        if schedule.get("experiment_id") != frozen.get("experiment_id"):
            return False, "调度 experiment_id 与冻结清单不符"
        if schedule.get("window_steps") not in (None, frozen.get("window_steps")):
            return False, ("调度窗口步数与冻结清单不符"
                           f"（{schedule.get('window_steps')} != {frozen.get('window_steps')}）")
        if set(schedule.get("variants") or []) != set(frozen.get("variants") or {}):
            return False, "调度 variants 与冻结清单不符"
    return True, f"frozen@{frozen_commit[:12]}"


def _has_run_artifacts(root: Path) -> bool:
    """是否存在**真实运行产物**（logs/status/trial 记录）。

    根级预置 JSON（schedule、验收清单等）不算占用；只看 `run/` 与 `outputs/` 下的产物，
    这样"已有上次日志但没 status"也会被拦住，而不是当成空目录重跑覆盖。
    """
    if not root.exists():
        return False
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        parts = path.parts
        if "run" in parts or "outputs" in parts:
            return True
    return False


def _block_dir(root: Path, job: dict) -> Path:
    return (root / job["variant"] / "outputs" / job["model_profile"] / job["architecture"]
            / f"{job['fault_type']}_task{job['task_id']}_seed{job['seed']}")


def _block_artifacts_ok(root: Path, job: dict, args, experiment_id: str | None = None) -> bool:
    """整块（control+fault）产物完整且身份/配置一致，才允许跳过。

    注入步必须与**实际运行相同地解析**：`--fault-injection-step` 缺省时，HTTP/DOM 会
    解析成 2、参数故障解析成 1；若直接传 None 去比对记录，完整的产物也会被误判不一致
    而再次付费重跑。
    """
    ok, _ = job_arms_ok(_block_dir(root, job), fault_type=job["fault_type"],
                        task_id=job["task_id"], seed=job["seed"],
                        model_profile=job["model_profile"],
                        architecture=job["architecture"], max_steps=args.max_steps,
                        injection_step=resolve_injection_step(
                            job["fault_type"], args.fault_injection_step),
                        fault_intensity=args.fault_intensity,
                        dataset_path=os.getenv("WEBARENA_DATASET"),
                        condition_scope="both",
                        experiment_id=experiment_id,
                        fault_variant=job["variant"])
    return ok


def _resumable(status: dict) -> bool:
    """已有 status 是否可跳过：跑完且既非基础设施错误也非机制错误。"""
    return bool(status.get("returncode") == 0
                and not status.get("infrastructure_error")
                and not status.get("mechanism_error"))


def run_planned(args, planned, freeze_note, experiment_id: str | None = None,
                verify_trial=None) -> int:
    root = Path(args.experiment_root)
    if args.resume and not _has_run_artifacts(root):
        print(f"error: {root} 没有可续跑的运行产物；全新目录请去掉 --resume",
              file=sys.stderr)
        return 3
    if _has_run_artifacts(root) and not args.resume:
        print(f"error: {root} 已存在运行产物（logs/status/trials）；"
              "首批要求空目录；如需续跑请显式加 --resume", file=sys.stderr)
        return 3
    skipped = 0
    if _has_run_artifacts(root) and args.resume:
        # 按 block 判定，且**两臂都要**：两臂 status 正常（跑完、非基础设施、非机制错误）
        # 且两臂产物完整一致，才允许跳过。只检查首先遇到的那一臂，可能把另一臂的机制
        # 错误一起跳过。
        blocks: dict = {}
        for item in planned:
            job = item["job"]
            key = (job["variant"], job["model_profile"], job["architecture"],
                   job["fault_type"], job["task_id"], job["seed"])
            blocks.setdefault(key, []).append(item)
        for key, items in blocks.items():
            ok = _block_artifacts_ok(root, items[0]["job"], args, experiment_id)
            if ok:
                for item in items:
                    status_path = Path(item["status_path"])
                    if not status_path.exists():
                        ok = False
                        break
                    try:
                        if not _resumable(json.loads(status_path.read_text(encoding="utf-8"))):
                            ok = False
                            break
                    except json.JSONDecodeError:
                        ok = False
                        break
            for item in items:
                if ok:
                    item["_skip"] = True
            skipped += len(items) if ok else 0
    manifest = {"experiment_id": experiment_id, "freeze": freeze_note, "resumed_skipped": skipped,
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
        if item.get("_skip"):
            print(f"SKIP {item['key']} (resume: already complete)", flush=True)
            continue
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
                              window_steps=job.get("window_steps", 5),
                              experiment_id=experiment_id)
        status.update({"variant": job["variant"], "condition": job["condition"],
                       "model_profile": job["model_profile"],
                       "architecture": job["architecture"],
                       "returncode": process.returncode, "timed_out": timed_out})
        if timed_out:
            status["infrastructure_error"] = True
        if verify_trial is not None:
            artifact_errors = verify_trial(item, args)
            if artifact_errors:
                status["mechanism_error"] = True
                status["artifact_errors"] = artifact_errors
        Path(item["status_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(item["status_path"]).write_text(
            json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"DONE {item['key']} rc={process.returncode}", flush=True)
        if process.returncode not in (0, None) or status.get("infrastructure_error"):
            infrastructure_errors += 1 if status.get("infrastructure_error") else 0
            failures += 1
        if status.get("mechanism_error"):
            # 缺机制日志/次数越界/重复 action_uid 等属实现错误：**立即停止本批**，
            # 而不是记一笔继续跑（那会把坏数据当正常结果收下来）。
            print(f"STOP policy: mechanism error on {item['key']}; stopping this batch.",
                  flush=True)
            return 2
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
    ok, note = check_freeze(args, schedule)
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
    return run_planned(args, planned, note, schedule.get("experiment_id"))


if __name__ == "__main__":
    raise SystemExit(main())
