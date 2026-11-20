"""
ReAct Agent 节点 - 标准 ReAct 循环：Thought → Action → Observation → 循环。

节点签名: (state: AgentState, config: dict) -> dict
返回部分状态更新 dict。

标准 ReAct 范式（Yao et al., 2023）：
- Thought:  LLM 显式输出推理过程（AIMessage.content）
- Action:   LLM 调用一个工具（AIMessage.tool_calls）
- Observation: 工具执行结果（ToolMessage），由 run_tools 节点返回

本实现同时：
- 将每一步写入 state.action_history（归因分析）
- 将每一步持久化为 JSONL trace（agent/trace.py）
"""

import json
import os
import re
import time

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from standard_agent.core.plan_decision import normalize_decision
from standard_agent.core.state import AgentState
from standard_agent.core.trace import append_event, append_trace, make_entry
from standard_agent.config import settings
from standard_agent.submission_contract import (
    MAX_SUBMISSION_ATTEMPTS,
    contract_context,
    validate_submission,
)
from standard_agent.llm.provider import (
    create_llm,
    extract_served_metadata,
    get_llm_metadata,
)
from standard_agent.tools.web_tools import ALL_TOOLS, get_current_observation


# System prompt 模板（标准 ReAct 格式）
from standard_agent.core.execution import (
    MAX_STEPS_WARNING,
    THOUGHT_REMINDER_TEXT,
    build_system_prompt,
    has_thought_prefix,
    looks_like_action_intent,
    no_tool_call_update,
    resolve_observation,
    run_executor_turn,
    usage_delta,
)


def _with_thought_reminder(system_prompt: str) -> str:
    """Backward-compatible wrapper around the shared prompt helper."""
    from standard_agent.core.execution import with_thought_reminder
    return with_thought_reminder(system_prompt)


def _looks_like_action_intent(text: str) -> bool:
    return looks_like_action_intent(text)


def _react_full_messages(system_prompt: str, task: str, url: str, messages) -> list:
    """System + human + history; never a trailing SystemMessage."""
    content = (system_prompt if THOUGHT_REMINDER_TEXT in system_prompt
               else _with_thought_reminder(system_prompt))
    return [
        SystemMessage(content=content),
        HumanMessage(content=(f"Please continue completing this task: {task}\n"
                              f"Current page: {url}")),
        *list(messages),
    ]


def _retrieval_completeness_context(task: str) -> str:
    """Add conservative completeness guidance for likely multi-answer queries."""
    if os.getenv("RETRIEVAL_REPAIR", "0") != "1":
        return ""
    text = str(task or "").casefold()
    multi_answer = any(marker in text for marker in (
        "name(s)", "username(s)", "reviewer(s)", "(s)", "all ", "top ",
        "list of", "each", "any", "how many",
    ))
    if not multi_answer:
        return ""
    return (
        "\n## Retrieval Completeness\n"
        "This retrieval task may have multiple matching results. Do not stop "
        "after finding the first match. Inspect all visible result pages, "
        "pagination links, and relevant sections before calling stop. If a page "
        "has a pagination control, record the current page and visit every relevant "
        "page or explicitly verify that no next page exists. Deduplicate items and "
        "return every matching value as separate elements in the requested list/object format. "
        "If exhaustive inspection finds no match, explicitly state that no matching "
        "result was found.\n"
    )


def _output_format_context(task: str,
                           submission_task_type: str | None = None) -> str:
    """Require JSON only when the task requests it, or under the shared contract."""
    if os.getenv("SUBMISSION_CONTRACT", "0") == "1":
        return contract_context(submission_task_type)
    text = str(task or "").casefold()
    structured_markers = (
        "return a list", "return an object", "return the value as",
        "return true", "return false", "keys \\\"", "json",
        "how many", "what is the number", "what is the count",
    )
    if not any(marker in text for marker in structured_markers):
        return ""
    return (
        "\n## Final Answer Format\n"
        "The task explicitly requests structured output. When calling stop, "
        "put ONLY valid JSON in answer, with no prose, Markdown, explanation, "
        "or THOUGHT prefix. Use the exact requested list/object/scalar shape.\n"
    )


def _answer_contract_context(task: str,
                             submission_task_type: str | None = None) -> str:
    """The one answer contract both architectures receive."""
    return (_retrieval_completeness_context(task)
            + _output_format_context(task, submission_task_type))


def _react_system_prompt(task: str, url: str, page_content: str,
                         plan_context: str = "",
                         submission_task_type: str | None = None) -> str:
    """ReAct prompt: the shared browser contract plus an optional plan context."""
    return build_system_prompt(
        task, url, page_content, context=plan_context,
        submission_task_type=submission_task_type,
        answer_contract=_answer_contract_context(task, submission_task_type))


def _execution_context(current_step: dict,
                       finish_requested: bool = False) -> str:
    """The only executor-specific prompt fields: sub-goal and success condition."""
    if finish_requested:
        goal = "Finalise the task"
        success = ("The plan owner signalled completion. You must still call "
                   "stop(answer) with the final answer; the trial is not complete "
                   "until that submission is accepted.")
    else:
        goal = str(current_step.get("goal", ""))
        success = str(current_step.get("success_condition", ""))
    return ("\n## Execution Context\n"
            f"Current sub-goal: {goal}\n"
            f"Success condition: {success}\n")


def _plan_executor_prompt(task: str, current_step: dict, url: str,
                          page_content: str,
                          submission_task_type: str | None = None,
                          finish_requested: bool = False) -> str:
    """Plan-and-execute executor prompt: same contract, only sub-goal differs."""
    return build_system_prompt(
        task, url, page_content,
        context=_execution_context(current_step, finish_requested),
        submission_task_type=submission_task_type,
        answer_contract=_answer_contract_context(task, submission_task_type))


def _thread_id(config: RunnableConfig | None) -> str:
    """从 LangGraph config 中提取 thread_id。"""
    try:
        return (config or {}).get("configurable", {}).get("thread_id", "")
    except Exception:
        return ""


def _trace_provenance(thread_id: str, step: int, task: str, url: str,
                      response: object, model_profile: str, architecture: str,
                      phase: str, latency_s: float | None = None) -> None:
    """记录本次调用**端点实际返回的**模型标识。

    ``get_llm_metadata`` 只记录我们**请求**的模型 id，因而无法区分"旗舰模型作答"
    与"供应商静默路由到了更小的后端"——而这正是本项目已经踩过的坑：第三方 GPT 端点
    可能以次充好，DeepSeek 自身的 id 也会跨模型别名（``deepseek-chat`` /
    ``deepseek-reasoner`` 已于 2026-07-24 停服并改为报错；``deepseek-v4-pro`` 在
    2026-09 的下线反复中一度计划被静默重路由到 V4.1 Flash）。若产物只留下请求 id，
    一场"模型对比"有可能其实是同一个模型与它自己对比。

    该事件**无条件**发射（不像 ``TRACE_LLM_IO`` 默认关闭）：只在需要时才记录的
    来源信息，在需要它的时候是拿不到的。记录本身是诊断性的，任何异常都被吞掉，
    绝不能让一次 trial 因为记日志而失败。
    """
    try:
        requested = get_llm_metadata(model_profile).get("model", "")
        data = {
            **get_llm_metadata(model_profile),
            **extract_served_metadata(response, requested_model=requested),
            "architecture": architecture,
            "phase": phase,
        }
        if latency_s is not None:
            data["latency_s"] = round(latency_s, 3)
        append_event(thread_id, event="llm_provenance", step=step,
                     task=task, url=url, data=data)
    except Exception:
        # 来源信息是诊断用途，绝不能因为它让一次 trial 失败。
        pass


def agent_node(state: AgentState, config: RunnableConfig) -> dict:
    """ReAct executor: one shared execution turn per graph visit."""
    task = state.get("task", "")
    model_profile = state.get("model_profile", "")
    url = state.get("url", "about:blank")
    step_count = state.get("step_count", 0)
    max_steps = state.get("max_steps", 30)
    messages = state.get("messages", [])
    thread_id = _thread_id(config)
    obs = get_current_observation()
    current_url, page_content = resolve_observation(obs, state)
    plan = state.get("plan", "")
    architecture = state.get("architecture", "react")
    submission_task_type = state.get("submission_task_type")
    plan_context = (
        "\n## High-Level Plan\n"
        "Follow this plan as a guide, revising it when observations contradict it:\n"
        f"{plan}\n"
        if plan else ""
    )
    if state.get("done", False):
        answer = state.get("answer", "")
        append_trace(thread_id, make_entry(
            thread_id, step=step_count + 1, task=task, url=current_url,
            thought="Task marked done by stop tool.",
            action="stop", args={}, observation="", error=False,
            done=True, answer=answer,
            extra={"architecture": architecture, "phase": "executor"},
        ))
        return {
            "messages": [AIMessage(content=answer)],
            "step_count": step_count + 1,
            "done": True,
            "answer": answer,
            "url": current_url,
            "page_content": page_content,
        }
    if step_count >= max_steps:
        return _force_stop(
            messages, step_count, max_steps, thread_id, task, current_url, architecture
        )

    system_prompt = _react_system_prompt(
        task, current_url, page_content, plan_context=plan_context,
        submission_task_type=submission_task_type)
    turn_state = dict(state)
    turn_state.update(url=current_url, page_content=page_content,
                      step_count=step_count)
    turn = run_executor_turn(
        state=turn_state, config=config, system_prompt=system_prompt,
        label="ReAct", phase="executor",
        extra_trace={"plan_revision": state.get("plan_revision", 0)},
        llm_factory=create_llm)
    new_step_count = step_count + 1
    if turn.no_tool_call:
        update = no_tool_call_update(
            state=turn_state, config=config, turn=turn, label="ReAct",
            extra_trace={"plan_revision": state.get("plan_revision", 0)})
        update.update({
            "architecture": architecture,
            "page_content": page_content,
            "step_count": new_step_count,
        })
        return update
    return {
        "messages": [turn.response],
        "step_count": new_step_count,
        "url": current_url,
        "page_content": page_content,
        "architecture": architecture,
        "llm_calls": turn.llm_calls,
        "executor_calls": turn.executor_calls,
        "retry_calls": turn.retry_calls,
        "executor_tokens": int(state.get("executor_tokens", 0) or 0) + turn.total_tokens,
        "executor_time_sec": round(
            float(state.get("executor_time_sec", 0) or 0) + turn.duration_sec, 3),
    }


def planner_node(state: AgentState, config: RunnableConfig) -> dict:
    """Generate an executable ordered plan for the plan-and-execute graph."""
    task = state.get("task", "")
    model_profile = state.get("model_profile", "")
    url = state.get("url", "about:blank")
    architecture = state.get("architecture", "react")
    obs = get_current_observation()
    url = obs["url"] if obs.get("url") and obs["url"] != "about:blank" else url
    page_content = obs["page_content"] or state.get("page_content", "(empty page)")
    thread_id = _thread_id(config)
    prompt = SystemMessage(content=(
        "You are the planner in a plan-and-execute web automation agent.\n"
        "Create an executable ordered plan. Return ONLY valid JSON with this shape:\n"
        '{"steps":[{"id":1,"goal":"...","success_condition":"..."}]}\n'
        "Each step must describe one meaningful sub-goal, not a tool call. "
        "Do not execute tools or provide the final answer. Re-plan from the "
        "current page when the previous plan is stale.\n\n"
        f"Task: {task}\nURL: {url}\nAccessibility Tree:\n{page_content}\n"
        f"Previous plan:\n{state.get('plan', '')}\n"
        f"Completed plan step: {state.get('current_plan_step', 0)}"
    ))
    llm = create_llm(profile_name=model_profile)
    request_step = state.get("step_count", 0)
    if settings.TRACE_LLM_IO:
        planner_request = {
            "architecture": "plan_execute",
            "phase": "planner",
            "message_roles": ["system"],
            "message_count": 1,
            **get_llm_metadata(model_profile),
        }
        if settings.TRACE_PROMPTS:
            planner_request["messages"] = [{
                "role": "system",
                "content": prompt.content,
            }]
        append_event(
            thread_id,
            event="llm_request",
            step=request_step,
            task=task,
            url=url,
            data=planner_request,
        )
    _t0 = time.time()
    try:
        plan_response = llm.invoke([prompt])
    except Exception as exc:
        append_event(
            thread_id,
            event="llm_error",
            step=request_step,
            task=task,
            url=url,
            data={
                "architecture": "plan_execute",
                "phase": "planner",
                "error": str(exc),
                **get_llm_metadata(model_profile),
            },
        )
        raise
    planning_time = time.time() - _t0
    _trace_provenance(thread_id, request_step, task, url, plan_response,
                      model_profile, "plan_execute", "planner", planning_time)
    planning_tokens = usage_delta(plan_response)
    raw_plan = str(getattr(plan_response, "content", "") or "").strip()
    plan_steps = _parse_plan_steps(raw_plan)
    plan = json.dumps({"steps": plan_steps}, ensure_ascii=False)
    if settings.TRACE_LLM_IO:
        append_event(
            thread_id,
            event="llm_response",
            step=request_step,
            task=task,
            url=url,
            data={
                "architecture": "plan_execute",
                "phase": "planner",
                "content": raw_plan,
                **get_llm_metadata(model_profile),
            },
        )
    append_event(
        thread_id,
        event="plan_generated",
        step=0,
        task=task,
        url=url,
        data={
            "architecture": "plan_execute",
            "phase": "planner",
            "plan": plan,
            "plan_steps": plan_steps,
            "response": raw_plan,
            **get_llm_metadata(model_profile),
        },
    )
    return {
        "plan": plan,
        "plan_steps": plan_steps,
        "current_plan_step": 0,
        "plan_revision": state.get("plan_revision", 0) + 1,
        "replan_required": False,
        "llm_calls": state.get("llm_calls", 0) + 1,
        "planning_calls": state.get("planning_calls", 0) + 1,
        "planning_tokens": int(state.get("planning_tokens", 0) or 0)
        + planning_tokens["total_tokens"],
        "planning_time_sec": round(
            float(state.get("planning_time_sec", 0) or 0) + planning_time, 3),
    }


def _parse_plan_steps(raw_plan: str) -> list[dict]:
    """Parse planner JSON while tolerating a fenced response from older models."""
    candidate = raw_plan.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", candidate, re.DOTALL | re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1).strip()
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        return [{"id": 1, "goal": candidate[:500], "success_condition": "Task is complete"}]
    steps = payload.get("steps", []) if isinstance(payload, dict) else []
    normalized = []
    for index, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            continue
        goal = str(step.get("goal", "")).strip()
        if not goal:
            continue
        normalized.append({
            "id": index,
            "goal": goal[:500],
            "success_condition": str(step.get("success_condition", "")).strip()[:500],
        })
    return normalized or [{"id": 1, "goal": "Complete the task", "success_condition": "Task is complete"}]


def plan_executor_node(state: AgentState, config: RunnableConfig) -> dict:
    """Plan-and-execute executor: the same turn contract as ReAct."""
    task = state.get("task", "")
    model_profile = state.get("model_profile", "")
    architecture = state.get("architecture", "plan_execute")
    step_count = state.get("step_count", 0)
    max_steps = state.get("max_steps", 30)
    messages = state.get("messages", [])
    thread_id = _thread_id(config)
    obs = get_current_observation()
    url, page_content = resolve_observation(obs, state)
    plan_steps = state.get("plan_steps", [])
    current = state.get("current_plan_step", 0)
    submission_task_type = state.get("submission_task_type")
    finish_requested = bool(state.get("finish_requested", False))
    current_step = plan_steps[current] if current < len(plan_steps) else {
        "id": current + 1, "goal": "Complete the task",
        "success_condition": "Task is complete",
    }
    if state.get("done", False):
        return {"done": True}
    if step_count >= max_steps:
        return _force_stop(
            messages, step_count, max_steps, thread_id, task, url, architecture
        )

    system_prompt = _plan_executor_prompt(
        task, current_step, url, page_content,
        submission_task_type=submission_task_type,
        finish_requested=finish_requested)
    turn_state = dict(state)
    turn_state.update(url=url, page_content=page_content, step_count=step_count)
    turn = run_executor_turn(
        state=turn_state, config=config, system_prompt=system_prompt,
        label="Plan-Execute", phase="executor",
        extra_trace={"plan_step": current, "plan_revision": state.get("plan_revision", 0),
                     "finish_requested": finish_requested},
        llm_factory=create_llm)
    new_step = step_count + 1
    if turn.no_tool_call:
        update = no_tool_call_update(
            state=turn_state, config=config, turn=turn, label="Plan-Execute",
            extra_trace={"plan_step": current,
                         "plan_revision": state.get("plan_revision", 0)})
        update.update({"architecture": architecture, "step_count": new_step,
                       "page_content": page_content})
        return update
    return {
        "messages": [turn.response],
        "step_count": new_step,
        "url": url,
        "page_content": page_content,
        "architecture": architecture,
        "llm_calls": turn.llm_calls,
        "executor_calls": turn.executor_calls,
        "retry_calls": turn.retry_calls,
        "executor_tokens": int(state.get("executor_tokens", 0) or 0) + turn.total_tokens,
        "executor_time_sec": round(
            float(state.get("executor_time_sec", 0) or 0) + turn.duration_sec, 3),
    }


def replanner_node(state: AgentState, config: RunnableConfig) -> dict:
    """Evaluate the latest tool result and advance, revise, or finish the plan."""
    if state.get("done", False):
        return {"replan_required": "end"}
    task = state.get("task", "")
    model_profile = state.get("model_profile", "")
    architecture = state.get("architecture", "plan_execute")
    thread_id = _thread_id(config)
    step_count = state.get("step_count", 0)
    current = state.get("current_plan_step", 0)
    plan_steps = state.get("plan_steps", [])
    messages = list(state.get("messages", []))
    latest = str(getattr(messages[-1], "content", "") if messages else "")[:3000]
    # Replanning is an LLM decision, not an unconditional loop back.
    prompt = SystemMessage(content=(
        "You are the replanner for a web automation agent. Return ONLY JSON: "
        '{"decision":"continue"|"replan"|"finish","reason":"..."}.\n'
        "Choose continue when the current sub-goal succeeded and another plan "
        "step remains. Choose finish only when the overall task is complete. "
        "Choose replan when the current plan is stale, failed, or exhausted.\n"
        f"Task: {task}\nPlan: {state.get('plan', '')}\n"
        f"Current plan step index: {current}/{len(plan_steps)}\n"
        f"Latest tool result: {latest}"
    ))
    llm = create_llm(profile_name=model_profile)
    _t0 = time.time()
    response = llm.invoke([prompt])
    replanning_time = time.time() - _t0
    _trace_provenance(thread_id, step_count, task, state.get("url", "about:blank"),
                      response, model_profile, architecture, "replanner",
                      replanning_time)
    replanning_tokens = usage_delta(response)
    raw = str(getattr(response, "content", "") or "")
    # Normalise before routing. Reading the field verbatim meant a model that
    # answered "next" instead of "continue" fell through to an unconditional
    # replan, charging its phrasing to the architecture. See
    # standard_agent/core/plan_decision.py for the full rationale.
    decision_result = normalize_decision(raw)
    decision = decision_result.decision
    append_event(thread_id, event="replan_decision", step=step_count,
                 task=task, url=state.get("url", "about:blank"), data={
                     "architecture": architecture, "phase": "replanner",
                     "decision": decision,
                     "decision_matched_by": decision_result.matched_by,
                     "decision_raw": decision_result.raw_value,
                     "decision_json_found": decision_result.json_found,
                     "contract_violation": decision_result.contract_violation,
                     "reason": raw[:500],
                 })
    if decision_result.contract_violation:
        append_event(
            thread_id,
            event="replanner_contract_violation",
            step=step_count,
            task=task,
            url=state.get("url", "about:blank"),
            data={
                "architecture": architecture,
                "phase": "replanner",
                "rule": "decision_must_be_continue_replan_or_finish",
                "raw_value": decision_result.raw_value,
                "matched_by": decision_result.matched_by,
                "routed_to": decision,
            },
        )
    calls = state.get("llm_calls", 0) + 1
    replanning_calls = state.get("replanning_calls", 0) + 1
    accounting = {
        "llm_calls": calls,
        "replanning_calls": replanning_calls,
        "replanning_tokens": int(state.get("replanning_tokens", 0) or 0)
        + replanning_tokens["total_tokens"],
        "replanning_time_sec": round(
            float(state.get("replanning_time_sec", 0) or 0) + replanning_time, 3),
    }
    if latest.startswith("[Error] Submission rejected"):
        # A rejected stop is feedback, not completion. Stay on the atomic goal
        # so the executor can correct the format without a replan or advance.
        return {"replan_required": False, "finish_requested": False, **accounting}
    if decision == "finish":
        # The replanner may only request wrap-up. Completion still requires a
        # valid stop(answer) submission from the executor.
        return {"finish_requested": True, "replan_required": False, **accounting}
    if decision == "continue":
        return {"current_plan_step": current + 1, "replan_required": False,
                "finish_requested": False, **accounting}
    return {"replan_required": True, "finish_requested": False, **accounting}


def run_tools(state: AgentState, config: RunnableConfig) -> dict:
    """
    工具执行节点：执行 LLM 请求的工具调用，并记录 action_history + JSONL trace。

    替代内置 ToolNode，行为完全一致，只是额外写入归因所需数据。
    """
    from langgraph.prebuilt import ToolNode

    tool_node = ToolNode(ALL_TOOLS)
    result = tool_node.invoke(state)

    msgs = list(state.get("messages", []))
    ai_msg = None
    for m in reversed(msgs):
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            ai_msg = m
            break

    tool_msgs = list(result.get("messages", []))
    tool_calls = getattr(ai_msg, "tool_calls", None) or []
    step_count = state.get("step_count", 0)
    thread_id = _thread_id(config)
    task = state.get("task", "")
    url = state.get("url", "about:blank")
    architecture = state.get("architecture", "react")
    contract_enabled = os.getenv("SUBMISSION_CONTRACT", "0") == "1"
    submission_task_type = state.get("submission_task_type")
    rejections = int(state.get("submission_rejections", 0) or 0)
    previous_rejections = rejections
    correction_pending = False
    thought_present = has_thought_prefix(getattr(ai_msg, "content", ""))
    plan_step = state.get("current_plan_step", 0)
    new_tool_msgs = list(tool_msgs)

    history_entries = []
    done = False
    protocol_failed = False
    answer = ""
    # 一条 AI message 可能携带多个 tool_call，LangGraph 按顺序返回 ToolMessage
    for index, (tc, tm) in enumerate(zip(tool_calls, tool_msgs)):
        action_name = tc.get("name", "")
        args = tc.get("args", {})
        obs_text = tm.content if tm else ""
        error = isinstance(obs_text, str) and obs_text.startswith("[Error]")
        rejected = False

        if (action_name == "stop" and contract_enabled
                and submission_task_type is not None):
            candidate = str(args.get("answer", ""))
            check = validate_submission(candidate, task_type=submission_task_type)
            if not check.ok:
                rejected = True
                rejections += 1
                if rejections >= MAX_SUBMISSION_ATTEMPTS:
                    protocol_failed = True
                    obs_text = (
                        f"[Error] Submission protocol failure: {check.error}. "
                        f"The corrected submission was still invalid after "
                        f"{rejections} attempts; the trial is ending."
                    )
                else:
                    correction_pending = True
                    obs_text = (
                        f"[Error] Submission rejected: {check.error}. "
                        "The task is NOT complete. Call stop again with a corrected answer. "
                        "The current plan step has not advanced."
                    )
                error = True
                if tm is not None:
                    try:
                        new_tool_msgs[index] = tm.model_copy(
                            update={"content": obs_text})
                    except AttributeError:
                        new_tool_msgs[index] = ToolMessage(
                            content=obs_text,
                            tool_call_id=getattr(tm, "tool_call_id", ""),
                            name=getattr(tm, "name", None),
                        )
                append_event(
                    thread_id, event="submission_rejected", step=step_count,
                    task=task, url=url,
                    data={
                        "architecture": architecture,
                        "phase": "executor",
                        "attempt": rejections,
                        "answer": candidate,
                        "error": check.error,
                    },
                )
                if protocol_failed:
                    append_event(
                        thread_id, event="submission_contract_failed",
                        step=step_count, task=task, url=url,
                        data={
                            "architecture": architecture,
                            "phase": "executor",
                            "attempt": rejections,
                            "answer": candidate,
                            "error": check.error,
                        },
                    )
            else:
                answer = check.normalized

        history_entries.append({
            "step": step_count,
            "thought": getattr(ai_msg, "content", "") or "",
            "thought_present": thought_present,
            "action": action_name,
            "args": args,
            "observation": str(obs_text)[:2000],
            "error": error,
            "plan_step": plan_step,
        })

        if settings.TRACE_CONSOLE:
            print(f"[ReAct][step={step_count}] ACTION {action_name} args={args}")
            print(f"[ReAct][step={step_count}] OBSERVATION {str(obs_text)[:1000]}")

        append_trace(thread_id, make_entry(
            thread_id, step=step_count, task=task, url=url,
            thought=getattr(ai_msg, "content", "") or "",
            action=action_name, args=args,
            observation=str(obs_text), error=error,
            extra={"architecture": architecture, "phase": "executor",
                   "thought_present": thought_present,
                   "plan_step": plan_step,
                   "submission_rejections": rejections},
        ))

        if action_name == "stop" and not rejected:
            done = True
            if not (contract_enabled and submission_task_type is not None):
                answer = str(args.get("answer", ""))

    update = {
        **result,
        "messages": new_tool_msgs,
        "action_history": history_entries,
    }
    if rejections != int(state.get("submission_rejections", 0) or 0):
        update["submission_rejections"] = rejections
    if protocol_failed:
        # The second invalid submission terminates the trial as an explicit
        # protocol failure; it is not a valid answer and must not be scored.
        update["done"] = True
        update["answer"] = ""
        update["error"] = "submission_contract_failed"
        update["termination"] = "submission_contract_failed"
        update["submission_correction_pending"] = False
        update["finish_requested"] = False
    elif done:
        update["done"] = True
        update["answer"] = answer
        update["termination"] = "stop"
        update["submission_correction_pending"] = False
        update["finish_requested"] = False
    else:
        update["submission_correction_pending"] = correction_pending
        if rejections == previous_rejections:
            update["finish_requested"] = False
    return update


def _force_stop(messages: list, step: int, max_steps: int, thread_id: str,
                 task: str, url: str, architecture: str = "react") -> dict:
    """达到最大步数时，追加一条强制 stop 消息并记录 trace。"""
    warning = SystemMessage(content=MAX_STEPS_WARNING.format(max_steps=max_steps))
    answer = f"Reached max steps ({max_steps})"
    append_trace(thread_id, make_entry(
        thread_id, step=step, task=task, url=url,
        thought="", action="force_stop", args={},
        observation="", error=True, done=True, answer=answer,
        extra={
            "architecture": architecture,
            "phase": "executor",
            "event": "max_steps",
        },
    ))
    return {
        "messages": list(messages) + [warning],
        "step_count": step,
        "done": True,
        "answer": answer,
        "termination": "cap_exhausted",
        "cap_exhausted": True,
        "finish_requested": False,
        "submission_correction_pending": False,
    }
