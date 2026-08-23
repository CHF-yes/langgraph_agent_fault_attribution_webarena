#!/usr/bin/env python3
"""
WebArena Baseline 运行器 — 在无故障（control）条件下批量跑任务，收集结果。

用法:
    python run_baseline.py                          # 跑内置示例任务集
    python run_baseline.py --task-ids 0 21 27 132   # 指定任务 ID
    python run_baseline.py --sites shopping reddit  # 按站点筛选
    python run_baseline.py --max-steps 10 --output baseline_results.json

任务数据: WebArena-Verified (https://github.com/ServiceNow/webarena-verified)
默认从 /root/webarena-dataset/webarena-verified.json 读取，可用环境变量
WEBARENA_DATASET 覆盖。

评估: baseline 使用宽松字符串匹配（answer 是否包含任一 expected 值），
用于快速冒烟；正式实验请接入 WebArena-Verified 的确定性评估器。
"""

import argparse
import json
import os
import platform
import time
import uuid
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

from standard_agent.core.graph import build_graph
from standard_agent.environment.browser import SyncBrowserEnv
from standard_agent.tools.web_tools import use_browser, use_simulation, reset_page_state
from standard_agent.environment.webarena_config import (
    WEBARENA_HOST,
    get_auto_login_headers,
)
from standard_agent.config import settings
from standard_agent.evaluation import evaluate_answer
from standard_agent.webarena_verified import make_agent_response
from standard_agent.failure_classification import classify_trial

DEFAULT_DATASET = "/root/webarena-dataset/webarena-verified.json"

# WebArena 占位符 → 本项目站点地址（与 webarena_config.py 保持一致）
URL_MAP = {
    "__SHOPPING__": f"http://{WEBARENA_HOST}:7770",
    "__SHOPPING_ADMIN__": f"http://{WEBARENA_HOST}:7780/admin",
    "__REDDIT__": f"http://{WEBARENA_HOST}:9999",
    "__GITLAB__": f"http://{WEBARENA_HOST}:8023",
    "__MAP__": f"http://{WEBARENA_HOST}:3000",
    "__WIKIPEDIA__": f"http://{WEBARENA_HOST}:8888/wikipedia_en_all_maxi_2022-05/A/User:The_other_Kiwix_guy/Landing",
    "__CMS__": f"http://{WEBARENA_HOST}:8080",
}

# 内置示例任务（各站点代表性任务 ID）
DEFAULT_TASK_IDS = [0, 1, 21, 27, 30, 47, 132, 169]


def load_tasks(task_ids=None, sites=None):
    dataset_path = os.getenv("WEBARENA_DATASET", DEFAULT_DATASET)
    with open(dataset_path, "r", encoding="utf-8") as f:
        tasks = json.load(f)
    if task_ids:
        id_set = set(task_ids)
        tasks = [t for t in tasks if t["task_id"] in id_set]
    if sites:
        tasks = [t for t in tasks if any(s in sites for s in t.get("sites", []))]
    return tasks


def resolve_start_url(task):
    """把任务里的占位符 URL 替换为实际地址。"""
    urls = []
    for u in task.get("start_urls", []):
        for placeholder, real in URL_MAP.items():
            if u.startswith(placeholder):
                u = real + u[len(placeholder):]
                break
        urls.append(u)
    return urls[0] if urls else "about:blank"


def site_for_url(url: str, task: dict | None = None) -> str:
    """根据起始 URL 确定站点，避免盲目使用 sites[0]。"""
    if task:
        sites = task.get("sites", [])
        if len(sites) == 1:
            return sites[0]
    for placeholder, real in URL_MAP.items():
        if real.split("://", 1)[-1].split("/", 1)[0] in url:
            return placeholder.strip("_").lower()
    sites = (task or {}).get("sites", [])
    return sites[0] if sites else "shopping"


def get_expected(task):
    """提取预期答案（宽松匹配用）。"""
    expected = []
    for e in task.get("eval", []):
        exp = e.get("expected", {})
        data = exp.get("retrieved_data", [])
        if data:
            expected.extend(data)
    return expected


def run_one_task(task, max_steps, model_profile=None, architecture="react",
                 webarena_output_dir=None, official_eval=False, trial_id=1,
                 run_seed=None, evaluator_config=None, experiment_id="",
                 storage_state_dir=None):
    """运行单个任务，返回结果 dict。"""
    task_id = task["task_id"]
    intent = task["intent"]
    start_url = resolve_start_url(task)
    expected = get_expected(task)
    profile = model_profile or settings.MODEL_PROFILE
    thread_id = f"base_{profile}_{task_id}_trial{trial_id}_{uuid.uuid4().hex[:6]}"

    reset_page_state()
    site = site_for_url(start_url, task)
    headers = get_auto_login_headers(site)
    storage_state = None
    if storage_state_dir:
        candidate = Path(storage_state_dir) / f"{site}.json"
        if candidate.exists():
            storage_state = str(candidate)
    har_path = None
    if webarena_output_dir:
        task_dir = os.path.join(webarena_output_dir, f"task_{task_id}", f"trial_{trial_id}")
        os.makedirs(task_dir, exist_ok=True)
        har_path = os.path.join(task_dir, "network.har")
    env = SyncBrowserEnv(
        headless=True,
        extra_http_headers=headers,
        har_path=har_path,
        storage_state=storage_state,
    )
    use_browser(env)
    t0 = time.time()
    result = {
        "task_id": task_id,
        "intent": intent,
        "start_url": start_url,
        "expected": expected,
        "thread_id": thread_id,
        "model_profile": profile,
        "architecture": architecture,
        "trial_id": trial_id,
        "run_seed": run_seed,
        "experiment_id": experiment_id or thread_id,
        "python_version": platform.python_version(),
        "playwright_version": _package_version("playwright"),
        "langgraph_version": _package_version("langgraph"),
        "webarena_verified_version": _package_version("webarena-verified"),
        "dataset_path": os.getenv("WEBARENA_DATASET", DEFAULT_DATASET),
        "evaluator_config": str(evaluator_config) if evaluator_config else None,
        "storage_state": storage_state,
        "max_steps": max_steps,
        "failure_category": None,
        "completed": False,
        "llm_calls": 0,
        "planning_calls": 0,
        "executor_calls": 0,
        "success": False,
        "official_success": None,
        "official_score": None,
        "evaluator_status": None,
        "evaluator_assertions": [],
        "done": False,
        "answer": "",
        "steps": 0,
        "time_sec": 0.0,
        "error": None,
        "fault_layer": "none",
        "fault_type": "control",
        "fault_seed": None,
        "behavior_category": "",
        "tolerance_layer": "",
            "recovery_steps": 0,
            "action_history": [],
        "injection_log": [],
        "fallback_used": False,
        "fallback_count": 0,
        "fallback_events": [],
    }
    try:
        env.start()
        obs = env.goto(start_url)
        app = build_graph(architecture)
        state = app.invoke({
            "task": intent,
            "model_profile": profile,
            "url": obs.url,
            "page_content": obs.ax_tree_text,
            "step_count": 0,
            "max_steps": max_steps,
            "done": False,
            "answer": "",
            "action_history": [],
            "architecture": architecture,
            "plan": "",
            "plan_steps": [],
            "current_plan_step": 0,
            "plan_revision": 0,
            "replan_required": False,
            "llm_calls": 0,
            "planning_calls": 0,
            "executor_calls": 0,
            "replanning_calls": 0,
        }, {"configurable": {"thread_id": thread_id}})

        answer = str(state.get("answer", "") or "")
        done = bool(state.get("done", False))
        result.update({
            "done": done,
            "answer": answer,
            "steps": state.get("step_count", 0),
            "llm_calls": state.get("llm_calls", 0),
            "planning_calls": state.get("planning_calls", 0),
            "executor_calls": state.get("executor_calls", 0),
            "replanning_calls": state.get("replanning_calls", 0),
            "action_history": state.get("action_history", []),
            "fallback_used": bool(env.fallback_events),
            "fallback_count": len(env.fallback_events),
            "fallback_events": list(env.fallback_events),
            "time_sec": round(time.time() - t0, 2),
        })
        completed, success = evaluate_answer(done, answer, expected)
        result["completed"] = completed
        result["success"] = success
        if webarena_output_dir:
            task_dir = os.path.join(
                webarena_output_dir, f"task_{task_id}", f"trial_{trial_id}"
            )
            os.makedirs(task_dir, exist_ok=True)
            response_path = os.path.join(task_dir, "agent_response.json")
            with open(response_path, "w", encoding="utf-8") as response_file:
                json.dump(
                    make_agent_response(task, completed=completed, answer=answer),
                    response_file, ensure_ascii=False, indent=2,
                )
                response_file.write("\n")
    except Exception as e:
        result["error"] = str(e)
        result["failure_category"] = _classify_failure(result["error"], result)
        result["time_sec"] = round(time.time() - t0, 2)
    finally:
        try:
            env.stop()
        except Exception:
            pass
        use_simulation()

    if webarena_output_dir:
        task_dir = os.path.join(webarena_output_dir, f"task_{task_id}", f"trial_{trial_id}")
        response_path = os.path.join(task_dir, "agent_response.json")
        if not os.path.exists(response_path):
            os.makedirs(task_dir, exist_ok=True)
            with open(response_path, "w", encoding="utf-8") as response_file:
                json.dump(
                    make_agent_response(
                        task,
                        completed=result["completed"],
                        answer=result["answer"] or result["error"] or "",
                    ),
                    response_file, ensure_ascii=False, indent=2,
                )
                response_file.write("\n")

    if official_eval and webarena_output_dir:
        from standard_agent.webarena_evaluator import evaluate_task_safe

        task_dir = os.path.join(webarena_output_dir, f"task_{task_id}", f"trial_{trial_id}")
        response_path = os.path.join(task_dir, "agent_response.json")
        trace_path = os.path.join(task_dir, "network.har")
        official = evaluate_task_safe(
            task_id,
            agent_response_path=response_path,
            network_trace_path=trace_path if os.path.exists(trace_path) else None,
            config_path=evaluator_config,
        )
        result["official_success"] = official.get("official_success", False)
        result["official_score"] = official.get("score", official.get("official_score", 0.0))
        result["evaluator_status"] = official.get("status")
        result["evaluator_assertions"] = official.get("evaluators_results", [])
        result["evaluator_error"] = official.get("error_msg")
        result.update(classify_trial(result, task))

    return result


def _package_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _classify_failure(error: str, result: dict) -> str:
    """Classify non-success without confusing infrastructure with Agent faults."""
    text = (error or "").casefold()
    if any(marker in text for marker in ("502", "503", "bad gateway", "connection refused")):
        return "infrastructure_failure"
    if any(marker in text for marker in ("sign in", "login", "unauthorized", "forbidden")):
        return "auth_precondition_failure"
    if "invalidselectorerror" in text or "unable to locate element" in text:
        return "adapter_failure"
    if "max steps" in (result.get("answer") or "").casefold():
        return "budget_exhausted"
    if error:
        return "runtime_failure"
    return None


def main():
    parser = argparse.ArgumentParser(description="WebArena baseline runner")
    parser.add_argument("--task-ids", type=int, nargs="*", default=None)
    parser.add_argument("--sites", type=str, nargs="*", default=None,
                        choices=["shopping", "shopping_admin", "reddit", "gitlab",
                                 "map", "wikipedia", "cms"])
    parser.add_argument("--max-steps", type=int, default=10)
    parser.add_argument("--output", type=str, default="baseline_results.json")
    parser.add_argument("--model-profile", type=str, default=None,
                        help="Named model profile from .env")
    parser.add_argument(
        "--architecture", choices=["react", "plan_execute"], default="react",
        help="Agent architecture (default: react)",
    )
    parser.add_argument("--all-models", action="store_true",
                        help="Run all MODEL_PROFILES and write one result file per profile")
    parser.add_argument(
        "--webarena-output-dir", type=str, default=None,
        help="Write WebArena-Verified agent_response.json and network.har per task",
    )
    parser.add_argument(
        "--official-eval", action="store_true",
        help="Run the official WebArena-Verified evaluator after each task",
    )
    parser.add_argument("--trials", type=int, default=1,
                        help="Repeat every task this many times")
    parser.add_argument("--run-seed", type=int, default=42,
                        help="Recorded run seed; provider sampling is unchanged")
    parser.add_argument("--evaluator-config", type=str, default=None,
                        help="Fixed WebArena-Verified evaluator config JSON")
    parser.add_argument("--storage-state-dir", type=str, default=None,
                        help="Directory containing per-site storage state JSON files")
    parser.add_argument("--health-check", action="store_true",
                        help="Run site health checks before baseline execution")
    parser.add_argument("--retrieval-repair", action="store_true",
                        help="Enable retrieval-completeness prompt repair; off by default")
    args = parser.parse_args()

    if args.retrieval_repair:
        os.environ["RETRIEVAL_REPAIR"] = "1"

    if args.all_models:
        profiles = list(settings.get_model_profiles())
        if not profiles:
            parser.error("--all-models requires MODEL_PROFILES in .env")
        for profile in profiles:
            output = args.output.replace(".json", f"_{profile}.json")
            print(f"\n===== model profile: {profile} =====")
            run_profile(profile, args, output)
        return

    run_profile(args.model_profile, args, args.output)


def run_profile(model_profile, args, output):

    tasks = load_tasks(task_ids=args.task_ids, sites=args.sites)
    if not tasks:
        print("没有匹配的任务")
        return
    # 未指定 task-ids 时用内置示例集；按给定顺序输出
    if args.task_ids is None and args.sites is None:
        id_order = {tid: i for i, tid in enumerate(DEFAULT_TASK_IDS)}
        tasks = [t for t in tasks if t["task_id"] in id_order]
        tasks.sort(key=lambda t: id_order[t["task_id"]])

    profile = model_profile or settings.MODEL_PROFILE
    if args.health_check:
        from scripts.health_check import check_all_sites
        health = check_all_sites(
            require_auth=bool(args.storage_state_dir),
            storage_state_dir=args.storage_state_dir,
            sites=args.sites or sorted({site for task in tasks for site in task.get("sites", [])}),
        )
        if not health["ok"]:
            raise RuntimeError(f"Health check failed: {json.dumps(health, ensure_ascii=False)}")
    print(f"\n开始 baseline：profile={profile}, {len(tasks)} 个任务, max_steps={args.max_steps}\n")
    if args.trials < 1:
        raise ValueError("--trials must be >= 1")
    results = []
    experiment_id = f"baseline_{profile}_{args.architecture}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    for trial_id in range(1, args.trials + 1):
        for i, task in enumerate(tasks, 1):
            r = run_one_task(
                task, args.max_steps, profile, args.architecture,
                args.webarena_output_dir, args.official_eval, trial_id,
                args.run_seed, args.evaluator_config, experiment_id,
                args.storage_state_dir,
            )
            results.append(r)
        if args.official_eval:
            mark = "OFFICIAL_PASS" if r.get("official_success") else "OFFICIAL_FAIL"
        else:
            mark = "PASS" if r["success"] else "FAIL"
        print(f"[{i}/{len(tasks)}] task={r['task_id']} {mark} "
              f"done={r['done']} steps={r['steps']} "
              f"time={r['time_sec']}s")
        print(f"        intent: {r['intent'][:80]}")
        if r["answer"]:
            print(f"        answer: {r['answer'][:100]}")
        if r["error"]:
            print(f"        error : {r['error'][:100]}")
        if args.official_eval:
            print(f"        official: status={r.get('evaluator_status')} "
                  f"score={r.get('official_score')} success={r.get('official_success')}")
        print()

    with open(output, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    summary_output = output.replace(".json", "_summary.csv")
    _write_summary_csv(summary_output, results)

    n_succ = sum(1 for r in results if r["success"])
    n_done = sum(1 for r in results if r["completed"])
    n_official = sum(1 for r in results if r.get("official_success") is True)
    avg_steps = sum(r["steps"] for r in results) / max(len(results), 1)
    avg_time = sum(r["time_sec"] for r in results) / max(len(results), 1)
    print("=" * 50)
    print(f"完成率: {n_done}/{len(results)}  |  宽松成功率: {n_succ}/{len(results)}")
    if args.official_eval:
        print(f"官方成功率: {n_official}/{len(results)}")
    print(f"平均步数: {avg_steps:.1f}  |  平均耗时: {avg_time:.1f}s")
    print(f"结果已保存: {output}")
    print(f"汇总表已保存: {summary_output}")


def _write_summary_csv(path, results):
    import csv
    from collections import defaultdict

    groups = defaultdict(list)
    for result in results:
        groups[result["task_id"]].append(result)
    fields = ["task_id", "site", "trials", "valid_trials",
              "evaluator_error_count", "official_success_rate",
              "mean_official_score", "mean_steps", "mean_llm_calls",
              "mean_time_sec", "fallback_trials", "fallback_count",
              "tool_error_rate"]
    with open(path, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for task_id, rows in groups.items():
            actions = [a for row in rows for a in row.get("action_history", [])]
            valid_rows = [
                row for row in rows
                if str(row.get("evaluator_status") or "").casefold()
                in {"success", "failure"}
            ]
            evaluator_errors = [
                row for row in rows
                if str(row.get("evaluator_status") or "").casefold() == "error"
            ]
            writer.writerow({
                "task_id": task_id,
                "site": site_for_url(rows[0]["start_url"]),
                "trials": len(rows),
                "valid_trials": len(valid_rows),
                "evaluator_error_count": len(evaluator_errors),
                "official_success_rate": round(
                    sum(bool(r.get("official_success")) for r in valid_rows)
                    / max(len(valid_rows), 1), 4
                ),
                "mean_official_score": round(
                    sum((r.get("official_score") or 0) for r in valid_rows)
                    / max(len(valid_rows), 1), 4
                ),
                "mean_steps": round(sum(r["steps"] for r in rows) / len(rows), 4),
                "mean_llm_calls": round(sum(r["llm_calls"] for r in rows) / len(rows), 4),
                "mean_time_sec": round(sum(r["time_sec"] for r in rows) / len(rows), 4),
                "fallback_trials": sum(bool(r.get("fallback_used")) for r in rows),
                "fallback_count": sum(r.get("fallback_count", 0) for r in rows),
                "tool_error_rate": round(sum(bool(a.get("error")) for a in actions) / max(len(actions), 1), 4),
            })


if __name__ == "__main__":
    main()
