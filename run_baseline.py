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
import time
import uuid

from standard_agent.core.graph import build_graph
from standard_agent.environment.browser import SyncBrowserEnv
from standard_agent.tools.web_tools import use_browser, use_simulation, reset_page_state
from standard_agent.environment.webarena_config import get_auto_login_headers
from standard_agent.config import settings

DEFAULT_DATASET = "/root/webarena-dataset/webarena-verified.json"

# WebArena 占位符 → 本项目站点地址（与 webarena_config.py 保持一致）
URL_MAP = {
    "__SHOPPING__": "http://localhost:7770",
    "__SHOPPING_ADMIN__": "http://localhost:7780/admin",
    "__REDDIT__": "http://localhost:9999",
    "__GITLAB__": "http://localhost:8023",
    "__MAP__": "http://localhost:3030",
    "__WIKIPEDIA__": "http://localhost:8888/wikipedia_en_all_maxi_2022-05/A/User:The_other_Kiwix_guy/Landing",
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


def get_expected(task):
    """提取预期答案（宽松匹配用）。"""
    expected = []
    for e in task.get("eval", []):
        exp = e.get("expected", {})
        data = exp.get("retrieved_data", [])
        if data:
            expected.extend(data)
    return expected


def run_one_task(task, max_steps, model_profile=None):
    """运行单个任务，返回结果 dict。"""
    task_id = task["task_id"]
    intent = task["intent"]
    start_url = resolve_start_url(task)
    expected = get_expected(task)
    profile = model_profile or settings.MODEL_PROFILE
    thread_id = f"base_{profile}_{task_id}_{uuid.uuid4().hex[:6]}"

    reset_page_state()
    site = task.get("sites", ["shopping"])[0]
    headers = get_auto_login_headers(site)
    env = SyncBrowserEnv(headless=True, extra_http_headers=headers)
    use_browser(env)
    t0 = time.time()
    result = {
        "task_id": task_id,
        "intent": intent,
        "start_url": start_url,
        "expected": expected,
        "thread_id": thread_id,
        "model_profile": profile,
        "success": False,
        "done": False,
        "answer": "",
        "steps": 0,
        "time_sec": 0.0,
        "error": None,
    }
    try:
        env.start()
        obs = env.goto(start_url)
        app = build_graph()
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
        }, {"configurable": {"thread_id": thread_id}})

        answer = str(state.get("answer", "") or "")
        done = bool(state.get("done", False))
        result.update({
            "done": done,
            "answer": answer,
            "steps": state.get("step_count", 0),
            "time_sec": round(time.time() - t0, 2),
        })
        # 宽松匹配：answer 包含任一 expected 值
        if done and expected:
            answer_lower = answer.lower()
            result["success"] = any(
                str(e).lower() in answer_lower for e in expected if e
            )
        elif done and not expected:
            # 无 expected 的任务，done 即视为完成（需人工核对）
            result["success"] = True
    except Exception as e:
        result["error"] = str(e)
        result["time_sec"] = round(time.time() - t0, 2)
    finally:
        try:
            env.stop()
        except Exception:
            pass
        use_simulation()

    return result


def main():
    parser = argparse.ArgumentParser(description="WebArena baseline runner")
    parser.add_argument("--task-ids", type=int, nargs="*", default=None)
    parser.add_argument("--sites", type=str, nargs="*", default=None,
                        choices=["shopping", "shopping_admin", "reddit", "gitlab"])
    parser.add_argument("--max-steps", type=int, default=10)
    parser.add_argument("--output", type=str, default="baseline_results.json")
    parser.add_argument("--model-profile", type=str, default=None,
                        help="Named model profile from .env")
    parser.add_argument("--all-models", action="store_true",
                        help="Run all MODEL_PROFILES and write one result file per profile")
    args = parser.parse_args()

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
    print(f"\n开始 baseline：profile={profile}, {len(tasks)} 个任务, max_steps={args.max_steps}\n")
    results = []
    for i, task in enumerate(tasks, 1):
        r = run_one_task(task, args.max_steps, profile)
        results.append(r)
        mark = "PASS" if r["success"] else "FAIL"
        print(f"[{i}/{len(tasks)}] task={r['task_id']} {mark} "
              f"done={r['done']} steps={r['steps']} "
              f"time={r['time_sec']}s")
        print(f"        intent: {r['intent'][:80]}")
        if r["answer"]:
            print(f"        answer: {r['answer'][:100]}")
        if r["error"]:
            print(f"        error : {r['error'][:100]}")
        print()

    with open(output, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    n_succ = sum(1 for r in results if r["success"])
    n_done = sum(1 for r in results if r["done"])
    avg_steps = sum(r["steps"] for r in results) / max(len(results), 1)
    avg_time = sum(r["time_sec"] for r in results) / max(len(results), 1)
    print("=" * 50)
    print(f"完成率: {n_done}/{len(results)}  |  宽松成功率: {n_succ}/{len(results)}")
    print(f"平均步数: {avg_steps:.1f}  |  平均耗时: {avg_time:.1f}s")
    print(f"结果已保存: {output}")


if __name__ == "__main__":
    main()
