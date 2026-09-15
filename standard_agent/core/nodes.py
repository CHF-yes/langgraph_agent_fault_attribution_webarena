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

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from standard_agent.core.state import AgentState
from standard_agent.core.trace import append_event, append_trace, make_entry
from standard_agent.config import settings
from standard_agent.llm.provider import create_llm, get_llm_metadata
from standard_agent.tools.web_tools import ALL_TOOLS, get_current_observation


# System prompt 模板（标准 ReAct 格式）
SYSTEM_PROMPT = """You are a standard ReAct (Reasoning + Acting) web automation agent completing tasks on real websites.

## Your Task
{task}

## Current Page
- URL: {url}
- Accessibility Tree (AX Tree):
{page_content}
{plan_context}
{retrieval_context}
{output_format_context}

## Available Actions
You can use the following tools to interact with the page:
- **click(element_id)**: Click an element by its [id=xxx] from the AX Tree.
- **type_text(element_id, text)**: Type text into an input field (click it first).
- **scroll(direction)**: Scroll "up" or "down" to reveal more content.
- **goto(url)**: Navigate to a different URL.
- **go_back()**: Go back to the previous page.
- **go_forward()**: Go forward to the next page.
- **select_option(element_id, option)**: Select an option from a dropdown.
- **hover(element_id)**: Hover over an element.
- **stop(answer)**: Call this when you have completed the task. Provide the answer.

## ReAct Protocol (MANDATORY)
You MUST follow this exact loop on EVERY step:

1. **THOUGHT (REQUIRED, NEVER SKIP)**: Before calling any tool, you MUST
   write your reasoning as the text content of your reply. The text content
   must begin with "THOUGHT:" and explain:
   - what you currently know about the page,
   - what you are looking for,
   - which action you will take and why.
   Calling a tool with EMPTY text content is a PROTOCOL VIOLATION.
2. **ACTION (REQUIRED)**: Call exactly ONE tool with the required arguments.
3. You will then receive an **OBSERVATION** (the updated page content).
   Repeat the loop until the task is complete.

### Example (one step)
User task: find the price of a product on the page.
- THOUGHT: The product "Stove Top" is visible on this page with price text [100] text '$8.49'. I can answer directly.
- ACTION: stop(answer="The price is $8.49.")

### Example (search step)
- THOUGHT: The product is not on the homepage. I will use the search box [25] to search for it.
- ACTION: click(element_id="25")

## Instructions
1. Analyze the AX Tree carefully. Element IDs are the [id=xxx] numbers.
2. Do NOT click plain text elements (role "text", "paragraph", "heading",
   "strong", "listitem"). Their content is ALREADY visible in the AX tree;
   read it directly. Only click interactive elements such as link, button,
   tab, checkbox, radio, menuitem, combobox.
3. If the target element is not visible, try scroll(direction) or goto(url).
4. Do NOT repeat the same failed action more than twice; change strategy.
5. When the task is complete, call **stop(answer)** with a clear summary.
6. If the page content is truncated, scroll or use a more specific search.

## Important
- ALWAYS use element IDs from the AX Tree (the [id=xxx] notation).
- Write your thought in natural language; it will be recorded for analysis.
- Be concise in your thinking, focus on action.
"""

MAX_STEPS_WARNING = """
You have reached the maximum number of steps ({max_steps}).
Call stop() with your best answer based on what you have found so far.
"""

# 运行时提醒：放在每条 LLM 输入末尾，强制模型先输出 THOUGHT 再调用工具
THOUGHT_REMINDER = SystemMessage(
    content="REMINDER: Your reply MUST begin with 'THOUGHT:' followed by "
            "your reasoning, and ONLY THEN call a tool. "
            "Calling a tool with empty text content is a protocol violation."
)


def _retrieval_completeness_context(task: str) -> str:
    """Add conservative completeness guidance for likely multi-answer queries."""
    if os.getenv("RETRIEVAL_REPAIR", "0") != "1":
        return ""
    text = str(task or "").casefold()
    multi_answer = any(marker in text for marker in (
        "name(s)", "username(s)", "reviewer(s)", "all ", "top ",
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


def _output_format_context(task: str) -> str:
    """Require JSON only when the task itself requests structured output."""
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


def _thread_id(config: RunnableConfig | None) -> str:
    """从 LangGraph config 中提取 thread_id。"""
    try:
        return (config or {}).get("configurable", {}).get("thread_id", "")
    except Exception:
        return ""


def agent_node(state: AgentState, config: RunnableConfig) -> dict:
    """
    ReAct agent 节点：读取当前状态 → LLM 决策 → 返回 tool_call 或最终回复。

    此节点会被反复调用（自循环），每次调用代表 ReAct 的一个推理步骤。
    当 LLM 不再产出 tool_call 时（或达到 max_steps / stop 被调用），图结束。
    """
    task = state.get("task", "")
    model_profile = state.get("model_profile", "")
    url = state.get("url", "about:blank")
    step_count = state.get("step_count", 0)
    max_steps = state.get("max_steps", 30)
    messages = state.get("messages", [])
    thread_id = _thread_id(config)

    # ---- 同步页面状态 ----
    # 优先从 BrowserEnv 获取真实页面观测，fallback 到模拟 PageState
    obs = get_current_observation()
    current_url = obs["url"] if obs["url"] and obs["url"] != "about:blank" else url
    page_content = obs["page_content"] or state.get("page_content", "(empty page)")
    plan = state.get("plan", "")
    architecture = state.get("architecture", "react")
    plan_context = (
        "\n## High-Level Plan\n"
        "Follow this plan as a guide, revising it when observations contradict it:\n"
        f"{plan}\n"
        if plan else ""
    )
    retrieval_context = _retrieval_completeness_context(task)
    output_format_context = _output_format_context(task)

    # ---- 检查 stop 是否已调用（优先级最高，避免 max_steps 覆盖正确结果）----
    # `done` 属于 LangGraph task state，不能依赖模块级页面状态；否则连续
    # benchmark trial 会把前一个任务的终止信号泄漏到下一个任务。
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

    # ---- 步数检查 ----
    # step_count 只统计 executor LLM 调用；达到上限后不再发起新的调用。
    if step_count >= max_steps:
        return _force_stop(
            messages, step_count, max_steps, thread_id, task, url, architecture
        )
    new_step_count = step_count + 1

    # ---- 构建消息 ----
    # 每轮重新注入任务、最新观测和工具协议。系统消息没有写入 state，避免
    # 状态膨胀；但也不能只在首轮注入，否则多步任务会丢失任务约束。
    system_msg = SystemMessage(content=SYSTEM_PROMPT.format(
        task=task,
        url=current_url,
        page_content=page_content,
        plan_context=plan_context,
        retrieval_context=retrieval_context,
        output_format_context=output_format_context,
    ))
    task_msg = HumanMessage(content=(
        f"Please continue completing this task: {task}\n"
        f"Current page: {current_url}"
    ))
    full_messages = [system_msg, task_msg, *list(messages), THOUGHT_REMINDER]

    if settings.TRACE_CONSOLE:
        print(f"[ReAct][step={new_step_count}] QUESTION task={task}")
        print(f"[ReAct][step={new_step_count}] OBSERVATION url={current_url} ax_chars={len(page_content)}")

    if settings.TRACE_LLM_IO:
        request_data = {
            "message_roles": [getattr(message, "type", "unknown") for message in full_messages],
            "message_count": len(full_messages),
            **get_llm_metadata(model_profile),
            "architecture": architecture,
            "phase": "executor",
        }
        if settings.TRACE_PROMPTS:
            request_data["messages"] = [
                {"role": getattr(message, "type", "unknown"),
                 "content": str(getattr(message, "content", ""))}
                for message in full_messages
            ]
        append_event(thread_id, event="llm_request", step=new_step_count,
                     task=task, url=current_url, data=request_data)

    # ---- LLM 推理 ----
    llm = create_llm(profile_name=model_profile)
    llm_with_tools = llm.bind_tools(ALL_TOOLS)
    try:
        response = llm_with_tools.invoke(full_messages)
    except Exception as exc:
        append_event(
            thread_id,
            event="llm_error",
            step=new_step_count,
            task=task,
            url=current_url,
            data={
                "architecture": architecture,
                "phase": "executor",
                "error": str(exc),
                **get_llm_metadata(model_profile),
            },
        )
        if settings.TRACE_CONSOLE:
            print(f"[ReAct][step={new_step_count}] ERROR {exc}")
        raise

    llm_calls = state.get("llm_calls", 0) + 1
    executor_calls = state.get("executor_calls", 0) + 1

    if settings.TRACE_LLM_IO:
        append_event(
            thread_id,
            event="llm_response",
            step=new_step_count,
            task=task,
            url=current_url,
            data={
                "content": str(getattr(response, "content", "")),
                "tool_calls": getattr(response, "tool_calls", []) or [],
                "architecture": architecture,
                "phase": "executor",
                **get_llm_metadata(model_profile),
            },
        )

    thought = str(getattr(response, "content", "") or "")
    protocol_violation = bool(getattr(response, "tool_calls", None)) and not thought.lstrip().startswith("THOUGHT:")
    if settings.TRACE_CONSOLE:
        print(f"[ReAct][step={new_step_count}] THOUGHT {thought[:1000]}")
        if protocol_violation:
            print(f"[ReAct][step={new_step_count}] PROTOCOL_VIOLATION missing THOUGHT prefix")
    if protocol_violation:
        append_event(
            thread_id,
            event="protocol_violation",
            step=new_step_count,
            task=task,
            url=current_url,
            data={
                "architecture": architecture,
                "phase": "executor",
                "rule": "tool_call_requires_THOUGHT_prefix",
                "content": thought,
            },
        )

    # ---- 检查是否有 tool_calls ----
    has_tool_calls = hasattr(response, "tool_calls") and response.tool_calls
    if has_tool_calls and len(response.tool_calls) > 1:
        # A browser observation is invalidated after each action. Execute one
        # action per ReAct turn so later calls cannot use stale element IDs.
        response = response.model_copy(update={"tool_calls": response.tool_calls[:1]})
        has_tool_calls = response.tool_calls

    # ---- 构建更新 ----
    update = {
        "messages": [response],
        "step_count": new_step_count,
        "url": current_url,
            "page_content": page_content,
            "architecture": architecture,
        "llm_calls": llm_calls,
        "executor_calls": executor_calls,
    }

    if not has_tool_calls:
        # LLM 返回了纯文本而非 tool_call：标准 ReAct 的 Final Answer
        answer = response.content
        update["done"] = True
        update["answer"] = answer
        update["action_history"] = [{
            "step": new_step_count,
            "thought": answer,
            "action": "final_answer",
            "args": {},
            "observation": "",
            "error": False,
        }]
        append_trace(thread_id, make_entry(
            thread_id, step=new_step_count, task=task, url=current_url,
            thought=answer, action="final_answer", args={},
            observation="", error=False, done=True, answer=answer,
            extra={"architecture": architecture, "phase": "executor"},
        ))

    return update


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
    """Execute exactly one action toward the current planner-owned sub-goal."""
    task = state.get("task", "")
    model_profile = state.get("model_profile", "")
    architecture = state.get("architecture", "plan_execute")
    step_count = state.get("step_count", 0)
    max_steps = state.get("max_steps", 30)
    messages = state.get("messages", [])
    thread_id = _thread_id(config)
    obs = get_current_observation()
    url = obs.get("url") or state.get("url", "about:blank")
    page_content = obs.get("page_content") or state.get("page_content", "(empty page)")
    plan_steps = state.get("plan_steps", [])
    current = state.get("current_plan_step", 0)
    current_step = plan_steps[current] if current < len(plan_steps) else {
        "id": current + 1, "goal": "Complete the task", "success_condition": "Task is complete"
    }
    if state.get("done", False):
        return {"done": True}
    if step_count >= max_steps:
        return _force_stop(messages, step_count, max_steps, thread_id, task, url, architecture)

    prompt = SystemMessage(content=(
        "You are the executor in a plan-and-execute web agent.\n"
        "Execute ONLY the current sub-goal below. Use exactly one tool call, "
        "or call stop when the overall task is complete. Do not redesign the "
        "whole plan; the replanner will do that after the observation.\n\n"
        f"Overall task: {task}\nCurrent sub-goal: {current_step['goal']}\n"
        f"Success condition: {current_step.get('success_condition', '')}\n"
        f"URL: {url}\nAccessibility Tree:\n{page_content}\n"
        "Your response must begin with THOUGHT:."
    ))
    full_messages = [prompt, *list(messages), THOUGHT_REMINDER]
    llm = create_llm(profile_name=model_profile).bind_tools(ALL_TOOLS)
    response = llm.invoke(full_messages)
    new_step = step_count + 1
    llm_calls = state.get("llm_calls", 0) + 1
    executor_calls = state.get("executor_calls", 0) + 1
    tool_calls = getattr(response, "tool_calls", []) or []
    thought = str(getattr(response, "content", "") or "")
    if tool_calls and not thought.lstrip().startswith("THOUGHT:"):
        append_event(thread_id, event="protocol_violation", step=new_step,
                     task=task, url=url, data={
                         "architecture": architecture, "phase": "executor",
                         "rule": "tool_call_requires_THOUGHT_prefix",
                         "content": thought,
                     })
    if len(tool_calls) > 1:
        response = response.model_copy(update={"tool_calls": tool_calls[:1]})
    if not getattr(response, "tool_calls", None):
        answer = str(getattr(response, "content", "") or "")
        append_trace(thread_id, make_entry(
            thread_id, step=new_step, task=task, url=url,
            thought=answer, action="final_answer", args={}, observation="",
            error=False, done=True, answer=answer,
            extra={"architecture": architecture, "phase": "executor", "plan_step": current},
        ))
        return {
            "messages": [response], "step_count": new_step, "done": True,
            "answer": answer, "url": url, "page_content": page_content,
            "llm_calls": llm_calls, "executor_calls": executor_calls,
        }
    return {
        "messages": [response], "step_count": new_step,
        "url": url, "page_content": page_content,
        "llm_calls": llm_calls, "executor_calls": executor_calls,
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
    response = llm.invoke([prompt])
    raw = str(getattr(response, "content", "") or "")
    raw_decision = _parse_replanner_decision(raw)
    decision_key = raw_decision.strip().lower() if isinstance(raw_decision, str) else ""
    aliases = {
        "continue": "continue", "next": "continue", "proceed": "continue",
        "advance": "continue", "finish": "finish", "done": "finish",
        "complete": "finish", "stop": "finish", "replan": "replan",
        "retry": "replan", "revise": "replan",
    }
    decision = aliases.get(decision_key, "replan")
    if decision_key not in aliases:
        append_event(thread_id, event="replanner_contract_violation", step=step_count,
                     task=task, url=state.get("url", "about:blank"), data={
                         "architecture": architecture, "phase": "replanner",
                         "raw_decision": str(raw_decision)[:200], "fallback": decision,
                     })
    append_event(thread_id, event="replan_decision", step=step_count,
                 task=task, url=state.get("url", "about:blank"), data={
                     "architecture": architecture, "phase": "replanner",
                     "decision": decision, "reason": raw[:500],
                 })
    calls = state.get("llm_calls", 0) + 1
    replanning_calls = state.get("replanning_calls", 0) + 1
    if decision == "finish":
        return {"done": True, "replan_required": "end", "llm_calls": calls,
                "replanning_calls": replanning_calls}
    if decision == "continue":
        return {"current_plan_step": current + 1, "replan_required": False,
                "llm_calls": calls, "replanning_calls": replanning_calls}
    return {"replan_required": True, "llm_calls": calls,
            "replanning_calls": replanning_calls}


def _parse_replanner_decision(raw: str) -> object:
    """Extract the first valid decision value from a model response."""
    decoder = json.JSONDecoder()
    for index, char in enumerate(raw):
        if char != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(raw[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload.get("decision", "")
    return ""


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

    history_entries = []
    done = False
    answer = ""
    # 一条 AI message 可能携带多个 tool_call，LangGraph 按顺序返回 ToolMessage
    for tc, tm in zip(tool_calls, tool_msgs):
        action_name = tc.get("name", "")
        args = tc.get("args", {})
        obs_text = tm.content if tm else ""
        error = isinstance(obs_text, str) and obs_text.startswith("[Error]")

        history_entries.append({
            "step": step_count,
            "thought": getattr(ai_msg, "content", "") or "",
            "action": action_name,
            "args": args,
            "observation": str(obs_text)[:2000],
            "error": error,
        })

        if settings.TRACE_CONSOLE:
            print(f"[ReAct][step={step_count}] ACTION {action_name} args={args}")
            print(f"[ReAct][step={step_count}] OBSERVATION {str(obs_text)[:1000]}")

        append_trace(thread_id, make_entry(
            thread_id, step=step_count, task=task, url=url,
            thought=getattr(ai_msg, "content", "") or "",
            action=action_name, args=args,
            observation=str(obs_text), error=error,
            extra={"architecture": architecture, "phase": "executor"},
        ))

        if action_name == "stop":
            done = True
            answer = str(args.get("answer", ""))

    update = {
        **result,
        "action_history": history_entries,
    }
    if done:
        update["done"] = True
        update["answer"] = answer
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
    }
