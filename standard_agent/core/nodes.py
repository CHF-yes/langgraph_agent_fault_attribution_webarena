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
    new_step_count = step_count + 1
    if new_step_count > max_steps:
        return _force_stop(messages, max_steps, thread_id, task, url)

    # ---- 构建消息 ----
    # 每轮重新注入任务、最新观测和工具协议。系统消息没有写入 state，避免
    # 状态膨胀；但也不能只在首轮注入，否则多步任务会丢失任务约束。
    system_msg = SystemMessage(content=SYSTEM_PROMPT.format(
        task=task,
        url=current_url,
        page_content=page_content,
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
            data={"error": str(exc), **get_llm_metadata(model_profile)},
        )
        if settings.TRACE_CONSOLE:
            print(f"[ReAct][step={new_step_count}] ERROR {exc}")
        raise

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
            data={"rule": "tool_call_requires_THOUGHT_prefix", "content": thought},
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
        ))

    return update


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


def _force_stop(messages: list, max_steps: int, thread_id: str,
                task: str, url: str) -> dict:
    """达到最大步数时，追加一条强制 stop 消息并记录 trace。"""
    warning = SystemMessage(content=MAX_STEPS_WARNING.format(max_steps=max_steps))
    answer = f"Reached max steps ({max_steps})"
    append_trace(thread_id, make_entry(
        thread_id, step=0, task=task, url=url,
        thought="", action="force_stop", args={},
        observation="", error=True, done=True, answer=answer,
    ))
    return {
        "messages": list(messages) + [warning],
        "step_count": max_steps,
        "done": True,
        "answer": answer,
    }
