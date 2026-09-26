#!/usr/bin/env python3
"""
WebArena ReAct Agent - CLI 入口

支持两种模式：
1. 模拟模式（默认）：使用内存 PageState，无需浏览器，适合快速测试
2. 浏览器模式（--browser）：对接 Playwright 真实浏览器，操作 WebArena 站点

使用方式：
    # 模拟模式 - 快速测试
    python main.py --task "What is the price?" --page scenario.json

    # 浏览器模式 - 真实 WebArena
    python main.py --browser --site shopping --task "Find the price of MacBook Pro"

    # 交互模式
    python main.py
"""

import sys
import uuid
import json
import argparse
import os
import time

from standard_agent.config import settings
from standard_agent.evaluation import evaluate_answer, is_cap_exhausted
from standard_agent.core.graph import build_graph
from standard_agent.tools.web_tools import (
    use_browser, use_simulation, reset_page_state, set_page_state,
)
from standard_agent.environment.webarena_config import get_site_config, list_sites, get_task_prompt, get_auto_login_headers


WELCOME = r"""
╔══════════════════════════════════════════════╗
║       🌐 WebArena ReAct Agent  🌐           ║
║                                              ║
║  基于 LangGraph 的 WebArena 智能体            ║
║  简化版 ReAct · 9 工具 · 单节点自循环        ║
║                                              ║
║  输入 /help 查看帮助  /reset 重置  /exit 退出  ║
╚══════════════════════════════════════════════╝
"""

HELP_TEXT = """
📋 可用命令：
  /exit       退出程序
  /reset      重置会话和页面状态
  /help       显示此帮助
  /sites      列出所有 WebArena 站点
  /task       设置任务: /task 你的任务描述
  /pagefile   加载页面场景: /pagefile scenario.json
  /page       查看当前页面状态
  go          开始执行任务

🚀 浏览器模式：
  python main.py --browser --site shopping --task "Find a product"

📄 模拟模式：
  python main.py --task "What is the price?" --page scenario.json
"""


def main():
    parser = argparse.ArgumentParser(
        description="WebArena ReAct Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Simulation mode
  python main.py --task "Find the price" --page scenario.json

  # Real browser mode (WebArena)
  python main.py --browser --site shopping --task "Find MacBook Pro price"
  python main.py --browser --site reddit --task "Find top post in r/python"
  python main.py --browser --site gitlab --task "List open issues"

  # List available sites
  python main.py --list-sites
        """,
    )
    parser.add_argument("--task", type=str, help="Task description")
    parser.add_argument("--url", type=str, default="about:blank",
                        help="Starting URL (simulation mode)")
    parser.add_argument("--page", type=str,
                        help="Page scenario JSON file (simulation mode)")
    parser.add_argument("--max-steps", type=int, default=None,
                        help="Maximum steps before forced stop")
    parser.add_argument("--browser", action="store_true",
                        help="Enable real browser mode (requires Playwright)")
    parser.add_argument("--site", type=str,
                        help="WebArena site name (e.g. shopping, reddit, gitlab)")
    parser.add_argument("--headed", action="store_true",
                        help="Show browser window (browser mode)")
    parser.add_argument("--list-sites", action="store_true",
                        help="List all available WebArena sites")
    parser.add_argument("--list-models", action="store_true",
                        help="List configured model profiles without calling an API")
    parser.add_argument("--slow-mo", type=int, default=0,
                        help="Slow down browser actions by N ms")
    parser.add_argument("--model-profile", type=str, default=None,
                        help="Named model profile from .env, e.g. glm52 or gemini")
    parser.add_argument(
        "--architecture", choices=["react", "plan_execute"], default="react",
        help="Agent architecture (default: react)",
    )

    # ---- 故障注入参数 ----
    fault_group = parser.add_argument_group("Fault Injection (故障注入)")
    fault_group.add_argument("--fault-intensity", type=str, default="off",
                             choices=["off", "low", "medium", "high"],
                             help="Fault intensity level (default: off)")
    fault_group.add_argument("--fault-seed", type=int, default=42,
                             help="Random seed for reproducible faults (default: 42)")
    fault_group.add_argument("--fault-injection-step", type=int, default=None,
                             help="Inject the selected fault exactly once at this action step")
    benchmark_group = parser.add_argument_group("benchmark outputs")
    benchmark_group.add_argument("--webarena-output-dir", default=None,
                                 help="Write per-trial agent_response.json, network.har, and evaluator result")
    benchmark_group.add_argument("--task-id", type=int, default=None,
                                 help="Official WebArena task id for benchmark output/evaluation")
    fault_group.add_argument("--fault-web-timeout", action="store_true",
                             help="Inject network timeout delays")
    fault_group.add_argument("--fault-web-http-error", action="store_true",
                             help="Inject 500/503 HTTP errors")
    fault_group.add_argument("--fault-web-dom-missing", action="store_true",
                             help="Inject DOM element loss")
    fault_group.add_argument("--fault-web-popup", action="store_true",
                             help="Inject popup/alert blocking")
    fault_group.add_argument("--fault-gitlab-ci-offline", action="store_true",
                             help="Inject GitLab CI Runner offline")
    fault_group.add_argument("--fault-gitlab-permission", action="store_true",
                             help="Inject GitLab 403 permission denied")
    fault_group.add_argument("--fault-gitlab-conflict", action="store_true",
                             help="Inject GitLab merge conflict")
    fault_group.add_argument("--fault-gitlab-quota", action="store_true",
                             help="Inject GitLab quota exceeded")
    fault_group.add_argument("--fault-agent-state-misjudge", action="store_true",
                             help="Inject agent state misjudgment")
    fault_group.add_argument("--fault-agent-param-error", action="store_true",
                             help="Inject agent tool parameter errors")
    fault_group.add_argument("--fault-all", action="store_true",
                             help="Enable all fault types at once")

    # ---- Benchmark 参数 ----
    bench_group = parser.add_argument_group("Benchmark (对照实验)")
    bench_group.add_argument("--benchmark", action="store_true",
                             help="Run A/B benchmark comparing control vs fault")
    bench_group.add_argument("--trials", type=int, default=3,
                              help="Trials per config (default: 3)")
    bench_group.add_argument(
        "--expected-answer", action="append", default=[],
        help="Expected answer substring; repeat for acceptable alternatives",
    )
    bench_group.add_argument(
        "--fault-type", choices=[
            "web_timeout", "web_http_error", "web_dom_missing", "web_popup_block",
            "gitlab_ci_offline", "gitlab_permission", "gitlab_conflict", "gitlab_quota",
            "agent_state_misjudge", "agent_param_error",
        ], default=None,
        help="Run one named fault only; equivalent to enabling its fault flag",
    )
    bench_group.add_argument("--condition", choices=["control", "fault"], default=None)

    args = parser.parse_args()

    # --list-sites
    if args.list_sites:
        print("\n📋 Available WebArena Sites:\n")
        for s in list_sites():
            print(f"  {s['key']:20s} → {s['name']}")
            print(f"  {'':20s}   {s['url']}\n")
        return

    if args.list_models:
        profiles = settings.get_model_profiles()
        print("\nConfigured model profiles:\n")
        for name, profile in profiles.items():
            print(f"  {name:12s} model={profile.model} base_url={profile.base_url} "
                  f"api_key={'set' if profile.api_key else 'missing'}")
        if not profiles:
            print("  (none; configure MODEL_PROFILES in .env)")
        return

    # 检查配置
    if not settings.validate(args.model_profile):
        print("\n⚠️  请先配置 .env 文件")
        return

    # ---- Benchmark 模式 ----
    if args.benchmark:
        return run_benchmark(args)

    # ---- 浏览器模式 ----
    if args.browser:
        return run_browser_mode(args)

    # ---- 模拟模式：直接执行任务 ----
    if args.task:
        app = build_graph(args.architecture)
        run_simulation_task(app, args)
        return

    # ---- 交互模式 ----
    app = build_graph(args.architecture)
    run_interactive(app, args.architecture)


# ============================================================
# 浏览器模式
# ============================================================

def run_browser_mode(args):
    """使用 Playwright 浏览器运行 WebArena 任务。"""
    from standard_agent.environment.browser import SyncBrowserEnv, HAS_PLAYWRIGHT

    if not HAS_PLAYWRIGHT:
        print("\n❌ Playwright 未安装。请运行：")
        print("   pip install playwright")
        print("   playwright install chromium\n")
        return

    # 获取站点配置（--url 提供时跳过站点查询）
    site_config = None
    site_url = args.url

    if args.site:
        try:
            site_config = get_site_config(args.site)
            site_url = site_config["base_url"]
        except KeyError as e:
            print(f"\n❌ {e}\n")
            print("   查看所有站点: python main.py --list-sites\n")
            return
    elif not args.url or args.url == "about:blank":
        print("\n⚠️  请指定 --site 或 --url")
        print("   --site shopping|reddit|gitlab|map|wikipedia|cms")
        print("   --url https://example.com\n")
        return

    if not args.task:
        print(f"\n⚠️  请指定任务: --task \"your task description\"\n")
        return

    # 每个 CLI 任务都从独立的模拟工具状态开始；浏览器本身仍由 env 管理。
    reset_page_state()

    # 启动浏览器
    label = args.site or args.url
    print(f"\n🌐 启动浏览器... (target={label}, headed={args.headed})")
    headers = get_auto_login_headers(args.site) if args.site else {}
    env = SyncBrowserEnv(headless=not args.headed, slow_mo=args.slow_mo,
                         extra_http_headers=headers)

    # ---- 故障注入 ----
    injector = _build_fault_injector(args, env)

    try:
        env.start()
        if injector:
            use_browser(injector)
            print(f"✅ 浏览器已启动 (故障注入: intensity={injector.config.intensity})")
        else:
            use_browser(env)
            print(f"✅ 浏览器已启动")

        # 导航到目标页面
        print(f"📍 导航到: {site_url}")
        if injector:
            obs = injector.goto(site_url)
        else:
            obs = env.goto(site_url)
        print(f"📄 页面已加载，{len(obs.element_map)} 个元素")

        # 构建 task prompt（有站点配置时附加站点提示）
        if site_config:
            enhanced_task = get_task_prompt(args.site, args.task)
        else:
            enhanced_task = args.task

        # 执行
        app = build_graph(args.architecture)
        max_steps = args.max_steps or settings.MAX_STEPS
        tid = str(uuid.uuid4())[:8]

        print(f"\n{'='*50}")
        print(f"📋 Task: {args.task}")
        if site_config:
            print(f"🏠 Site: {site_config['name']}")
        else:
            print(f"🏠 URL: {site_url}")
        print(f"🔢 Max Steps: {max_steps}")
        print(f"{'='*50}\n")

        result = app.invoke({
            "task": enhanced_task,
            "model_profile": args.model_profile or settings.MODEL_PROFILE,
            "url": obs.url,
            "page_content": obs.ax_tree_text,
            "step_count": 0,
            "max_steps": max_steps,
            "done": False,
            "answer": "",
            "action_history": [],
            "architecture": args.architecture,
            "plan": "",
            "plan_steps": [],
            "current_plan_step": 0,
            "plan_revision": 0,
            "replan_required": False,
            "llm_calls": 0,
            "planning_calls": 0,
            "executor_calls": 0,
            "replanning_calls": 0,
        }, {"configurable": {"thread_id": tid}})

        # 输出结果
        _print_result(result)

        # 故障注入报告
        if injector:
            injector.print_report()

    except Exception as e:
        print(f"\n❌ 浏览器模式出错: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("\n🔒 关闭浏览器...")
        env.stop()
        use_simulation()
        print("✅ 浏览器已关闭")


# ============================================================
# 模拟模式
# ============================================================

def run_simulation_task(app, args):
    """模拟模式：从 JSON 场景文件或命令行参数执行任务。"""
    if args.page:
        with open(args.page, "r") as f:
            scenario = json.load(f)
        set_page_state(
            url=scenario.get("url", "about:blank"),
            content=scenario.get("content", ""),
            elements=scenario.get("elements", {}),
        )
        start_url = scenario.get("url", "about:blank")
        page_content = scenario.get("content", "")
    else:
        start_url = args.url
        page_content = ""

    max_steps = args.max_steps or settings.MAX_STEPS

    print(f"\n📋 Task: {args.task}")
    print(f"📍 URL: {start_url}")
    print(f"🔢 Max Steps: {max_steps}\n")

    result = app.invoke({
        "task": args.task,
        "model_profile": args.model_profile or settings.MODEL_PROFILE,
        "url": start_url,
        "page_content": page_content,
        "step_count": 0,
        "max_steps": max_steps,
        "done": False,
        "answer": "",
        "action_history": [],
        "architecture": args.architecture,
        "plan": "",
        "plan_steps": [],
        "current_plan_step": 0,
        "plan_revision": 0,
        "replan_required": False,
        "llm_calls": 0,
        "planning_calls": 0,
        "executor_calls": 0,
        "replanning_calls": 0,
    }, {"configurable": {"thread_id": str(uuid.uuid4())[:8]}})

    _print_result(result)


# ============================================================
# 交互模式
# ============================================================

def run_interactive(app, architecture="react"):
    """交互式 CLI 模式。"""
    print(WELCOME)
    use_simulation()
    thread_id = str(uuid.uuid4())[:8]
    config = {"configurable": {"thread_id": thread_id}}
    task = ""
    page_file = None

    print(f"✅ 已就绪 (会话: {thread_id})")
    print("💡 设置任务: /task 你的任务 | 加载页面: /pagefile scenario.json")
    print("💡 输入 go 开始执行\n")

    while True:
        try:
            user_input = input("🌐 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 再见！")
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            parts = user_input.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1] if len(parts) > 1 else ""

            if cmd == "/exit":
                print("👋 再见！")
                break
            elif cmd == "/reset":
                thread_id = str(uuid.uuid4())[:8]
                config = {"configurable": {"thread_id": thread_id}}
                reset_page_state()
                task = ""
                page_file = None
                print(f"🔄 已重置\n")
                continue
            elif cmd == "/help":
                print(HELP_TEXT)
                continue
            elif cmd == "/sites":
                for s in list_sites():
                    print(f"  {s['key']:20s} → {s['name']} ({s['url']})")
                continue
            elif cmd == "/task":
                task = arg
                print(f"📋 任务: {task}")
                continue
            elif cmd == "/pagefile":
                page_file = arg
                print(f"📄 页面文件: {page_file}")
                continue
            elif cmd == "/page":
                from standard_agent.tools.web_tools import get_page_state
                ps = get_page_state()
                print(f"  URL: {ps.url}\n  Elements: {len(ps.elements)}")
                for eid, desc in list(ps.elements.items())[:20]:
                    print(f"    [{eid}] {desc}")
                continue
            else:
                print(f"❓ 未知: {user_input}")
                continue

        if user_input.lower() == "go":
            if not task:
                print("⚠️  请先用 /task 设置任务")
                continue
            if page_file:
                try:
                    with open(page_file, "r") as f:
                        scenario = json.load(f)
                    set_page_state(
                        url=scenario.get("url", "about:blank"),
                        content=scenario.get("content", ""),
                        elements=scenario.get("elements", {}),
                    )
                except Exception as e:
                    print(f"❌ 加载失败: {e}")
                    continue

            result = app.invoke({
                "task": task,
                "model_profile": settings.MODEL_PROFILE,
                "url": getattr(get_page_state(), "url", "about:blank"),
                "page_content": getattr(get_page_state(), "content", ""),
                "step_count": 0,
                "max_steps": settings.MAX_STEPS,
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
            }, config)
            _print_result(result)
            continue

        # 非命令输入：引导用户设置任务（避免以不完整的 ReAct 状态调用 LLM）
        if not task:
            print("⚠️  请先用 /task 设置任务，然后输入 go 开始执行")
            print("💡  例如: /task Find the price of MacBook Pro")
        else:
            print(f"📋  当前任务: {task}")
            print("💡  输入 go 开始执行，或 /task 更换任务")
            print("💡  /pagefile scenario.json 可加载页面场景")


# ============================================================
# 工具函数
# ============================================================

def _print_result(result: dict):
    """打印任务执行结果。"""
    print(f"\n{'='*50}")
    completed = result.get("completed")
    if completed is None:
        done = result.get("done", False)
        answer = result.get("answer", "")
        completed = bool(done) and "reached max steps" not in str(answer).casefold()
    steps = result.get("step_count", 0)
    answer = result.get("answer", "")

    status = "✅ 完成" if completed else "⚠️ 未完成"
    print(f"{status} (步数: {steps})")
    if answer:
        print(f"📝 答案: {answer}")
    print(f"{'='*50}\n")


def _build_fault_injector(args, env):
    """
    根据 CLI 参数构建 FaultInjector。

    Returns:
        FaultInjector 实例，或 None（如果未启用故障注入）
    """
    if args.fault_intensity == "off":
        return None

    from fault_injection import FaultInjector

    config = _build_fault_config(args)
    return FaultInjector(env, config)


# ============================================================
# Benchmark 模式
# ============================================================

def run_benchmark(args):
    """运行 A/B 对照实验。"""
    from standard_agent.environment.browser import SyncBrowserEnv, HAS_PLAYWRIGHT
    from fault_injection import FaultConfig, FaultInjector, BenchmarkRunner, TrialResult

    if not HAS_PLAYWRIGHT:
        print("\n❌ Playwright 未安装")
        return

    if not args.task:
        print("\n⚠️  请指定 --task")
        return

    url = args.url
    if args.site and url == "about:blank":
        from standard_agent.environment.webarena_config import get_site_config
        url = get_site_config(args.site)["base_url"]

    if not url or url == "about:blank":
        print("\n⚠️  请指定 --url 或 --site")
        return

    # The official evaluator scores the response against the dataset's own
    # `eval` contract (task type, expected status, results schema). Building a
    # response from a placeholder task would silently turn a navigation task
    # into a retrieval task, so the definition is resolved once, up front, and
    # the run refuses to write responses without it.
    task_definition = None
    if args.task_id is not None:
        from standard_agent.webarena_verified import (
            TaskDefinitionNotFound, load_task_definition,
        )
        try:
            task_definition = load_task_definition(args.task_id)
        except TaskDefinitionNotFound as exc:
            print(f"\n❌ 无法加载任务 {args.task_id} 的官方 eval 定义，"
                  f"拒绝生成无法评分的 agent_response：{exc}")
            return
        from standard_agent.webarena_verified import task_type_for
        print(f"\n任务 {args.task_id} 官方类型: {task_type_for(task_definition)}")

    def run_one_trial(task_id: str, task_desc: str, page_url: str,
                      fault_config: FaultConfig, trial_idx: int) -> TrialResult:
        """执行单次 Agent 任务并返回 TrialResult。"""
        import uuid
        from standard_agent.core.graph import build_graph
        from standard_agent.tools.web_tools import use_browser, use_simulation
        from standard_agent.trial_metadata import (
            arm_dir_name, build_trial_record, trial_file_stem, write_trial_record,
        )

        reset_page_state()
        headers = get_auto_login_headers(args.site) if args.site else {}
        condition = "control" if fault_config.fault_label == "control" else "fault"
        # Both arms of a pair carry the matrix fault type; the arm is
        # distinguished by `condition`, not by the injected label.
        record_fault_type = args.fault_type or fault_config.fault_label
        model_profile = args.model_profile or settings.MODEL_PROFILE
        stem = trial_file_stem(
            model_profile=model_profile, architecture=args.architecture,
            fault_type=record_fault_type, task_id=args.task_id,
            seed=fault_config.seed, condition=condition, replicate=trial_idx,
        )
        # 每次 trial 一个 run id：trace 落在独立目录，重跑同一格不会续写旧 trace。
        run_id = f"{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
        os.environ["TRACE_RUN_ID"] = run_id
        from standard_agent.core.trace import prepare_trace
        prepare_trace(stem)
        output_dir = None
        if args.webarena_output_dir and args.task_id is not None:
            output_dir = os.path.join(
                args.webarena_output_dir, str(args.task_id),
                arm_dir_name(fault_label=fault_config.fault_label,
                             seed=fault_config.seed),
            )
            os.makedirs(output_dir, exist_ok=True)
        har_path = os.path.join(output_dir, "network.har") if output_dir else None
        env = SyncBrowserEnv(headless=True, extra_http_headers=headers, har_path=har_path)
        t0 = time.time()

        try:
            env.start()
            proxy = FaultInjector(env, fault_config)
            use_browser(proxy)

            # 导航
            proxy.goto(page_url)

            # 运行 agent
            app = build_graph(args.architecture)
            initial_obs = proxy.get_obs()
            result = app.invoke({
                "task": task_desc,
                "model_profile": model_profile,
                "url": initial_obs.url,
                "page_content": initial_obs.ax_tree_text,
                "step_count": 0,
                "max_steps": args.max_steps or settings.MAX_STEPS,
                "done": False,
                "answer": "",
                "action_history": [],
                "architecture": args.architecture,
                "plan": "",
                "plan_steps": [],
                "current_plan_step": 0,
                "plan_revision": 0,
                "replan_required": False,
                "llm_calls": 0,
                "planning_calls": 0,
                "executor_calls": 0,
                "replanning_calls": 0,
            }, {"configurable": {"thread_id": stem}})

            elapsed = time.time() - t0
            done = result.get("done", False)
            steps = result.get("step_count", 0)
            answer = result.get("answer", "")

            # 先统一计算完成状态；是否正确由 BenchmarkRunner 的 evaluator 决定。
            completed, _ = evaluate_answer(done, answer, [])
            success = completed
            # 步数耗尽：答案串是 harness 合成的诊断，必须走 error_details，
            # 不能当成检索答案提交（否则预算产物会被评分成检索结果）。
            cap_exhausted = is_cap_exhausted(done, answer)
            diagnostic = (f"max_steps exhausted after {steps} steps; "
                          f"no answer produced" if cap_exhausted else None)

            log = proxy.get_injection_log()
            total_delay = sum(
                e.get("detail", {}).get("delay_sec", 0) for e in log
            )
            print(
                f"        injection_count={len(log)} "
                f"fault_seed={fault_config.seed} "
                f"faults={[entry.get('fault') for entry in log]} "
                f"steps={[entry.get('step') for entry in log]}"
            )
            for entry in log:
                print(f"        injection={entry}")

            trial = TrialResult(
                task_id=task_id,
                config_label=fault_config.intensity,
                success=success,
                completed=completed,
                architecture=args.architecture,
                llm_calls=result.get("llm_calls", 0),
                planning_calls=result.get("planning_calls", 0),
                executor_calls=result.get("executor_calls", 0),
                replanning_calls=result.get("replanning_calls", 0),
                steps=steps,
                time_sec=elapsed,
                answer=answer,
                injection_count=len(log),
                total_delay_sec=total_delay,
                action_history=result.get("action_history", []),
                injection_log=list(log),
                model_profile=model_profile,
                trial_id=stem,
                condition=condition,
                replicate=trial_idx,
            )
            if output_dir and args.task_id is not None:
                from standard_agent.webarena_verified import write_agent_response
                response_path = write_agent_response(
                    os.path.join(output_dir, "agent_response.json"),
                    task_definition, completed=completed, answer=answer,
                    diagnostic=diagnostic,
                )
                from standard_agent.core.trace import get_trace_path
                trace_path = get_trace_path(stem)
                record = build_trial_record(
                    model_profile=model_profile, architecture=args.architecture,
                    fault_type=record_fault_type, task_id=args.task_id,
                    seed=fault_config.seed, condition=condition, replicate=trial_idx,
                    run_config={
                        "run_id": run_id,
                        "task_id": args.task_id, "site": args.site, "url": page_url,
                        "max_steps": args.max_steps or settings.MAX_STEPS,
                        "fault_intensity": fault_config.intensity,
                        "fault_label": fault_config.fault_label,
                        "injection_step": fault_config.injection_step,
                        "trial_index": trial_idx,
                    },
                    paths={
                        "agent_response": str(response_path),
                        "network_har": str(har_path) if har_path else None,
                        "trace": str(trace_path),
                    },
                    completed=completed, success=success,
                    injection_count=len(log), error=None,
                    steps=steps, cap_exhausted=cap_exhausted,
                    llm_calls=result.get("llm_calls", 0),
                )
                write_trial_record(os.path.join(output_dir, "trial_record.json"), record)
                trial.trace_path = str(trace_path)
                trial.trial_record_path = os.path.join(output_dir, "trial_record.json")
            return trial

        except Exception as e:
            return TrialResult(
                task_id=task_id,
                config_label=fault_config.intensity,
                success=False,
                completed=False,
                architecture=args.architecture,
                llm_calls=0,
                planning_calls=0,
                executor_calls=0,
                replanning_calls=0,
                steps=0,
                time_sec=time.time() - t0,
                error=str(e),
            )
        finally:
            env.stop()
            use_simulation()

    def evaluate_trial(trial: TrialResult) -> bool:
        """评估 CLI 传入的答案期望值。"""
        _, success = evaluate_answer(
            trial.completed, trial.answer, args.expected_answer
        )
        return success

    # 只有显式提供 expected-answer 时才启用答案正确性评估；否则 success
    # 保持为正常完成率，避免伪造“正确答案”指标。
    runner = BenchmarkRunner(
        run_fn=run_one_trial,
        evaluator=evaluate_trial if args.expected_answer else None,
    )

    if args.condition != "fault":
        # Control 与故障臂共用同一次运行的 seed：control 不注入任何故障，
        # seed 只用于配对标识，因此两臂能用同一个 pair key 对上。
        runner.add_config("control", FaultConfig(intensity="off", seed=args.fault_seed))

    # 实验组
    exp_label = f"fault_{args.fault_intensity}"
    exp_config = _build_fault_config(args)
    if args.condition != "control":
        runner.add_config(exp_label, exp_config)

    # 任务
    runner.add_task("task", args.task, url)

    # 运行
    print(f"\n🧪 开始对照实验: control vs {exp_label}")
    print(f"   任务: {args.task}")
    print(f"   URL: {url}")
    print(f"   每组 {args.trials} 次试验\n")
    if args.condition != "control" and args.fault_type:
        from fault_injection.config import injection_step_mode, injection_step_units
        step = exp_config.injection_step
        print(f"   注入步: {step} ({injection_step_units(args.fault_type)}), "
              f"口径={injection_step_mode(args.fault_injection_step)}\n")
    if args.expected_answer:
        print(f"   评估模式: 答案匹配 ({len(args.expected_answer)} 个可接受答案)\n")
    else:
        print("   评估模式: completion rate（未提供 --expected-answer）\n")
    report = runner.run(trials_per_config=args.trials)
    runner.print_report(report)


def _build_fault_config(args) -> "FaultConfig":
    """从 CLI args 构建 FaultConfig（不含代理包装）。"""
    from fault_injection import FaultConfig
    from fault_injection.config import resolve_injection_step

    if args.fault_type:
        # A named single fault is a formal single-injection arm. With no
        # explicit --fault-injection-step it follows the frozen Stage C
        # contract (agent_param_error -> 1st parameter action, everything else
        # -> 2nd execution step). Stage E is the stage that sweeps the step and
        # passes an explicit value; the matrix records which mode was used.
        step = resolve_injection_step(args.fault_type, args.fault_injection_step)
        return FaultConfig.single_fault(
            args.fault_type, intensity=args.fault_intensity, seed=args.fault_seed,
            injection_step=step,
        )

    if args.fault_all:
        return FaultConfig(
            intensity=args.fault_intensity, seed=args.fault_seed,
            injection_step=args.fault_injection_step,
            web_timeout=True, web_http_error=True,
            web_dom_missing=True, web_popup_block=True,
            gitlab_ci_offline=True, gitlab_permission=True,
            gitlab_conflict=True, gitlab_quota=True,
            agent_state_misjudge=True, agent_param_error=True,
        )
    return FaultConfig(
        intensity=args.fault_intensity, seed=args.fault_seed,
        injection_step=args.fault_injection_step,
        web_timeout=args.fault_web_timeout,
        web_http_error=args.fault_web_http_error,
        web_dom_missing=args.fault_web_dom_missing,
        web_popup_block=args.fault_web_popup,
        gitlab_ci_offline=args.fault_gitlab_ci_offline,
        gitlab_permission=args.fault_gitlab_permission,
        gitlab_conflict=args.fault_gitlab_conflict,
        gitlab_quota=args.fault_gitlab_quota,
        agent_state_misjudge=args.fault_agent_state_misjudge,
        agent_param_error=args.fault_agent_param_error,
    )


if __name__ == "__main__":
    main()
