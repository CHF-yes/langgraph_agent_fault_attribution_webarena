"""Shared browser-execution contract for ReAct and plan-and-execute.

Both architectures use the same system prompt, human message, history order,
tool definitions, THOUGHT requirement, one-tool-per-turn rule, no-tool retry,
multi-tool truncation and terminal protocol-failure handling.  The only
architecture-specific prompt content is the plan context section: ReAct sees a
high-level plan, plan-and-execute sees its current sub-goal and success
condition.  Fault configuration, fault windows, target records and scoring
truth are never passed here.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from langchain_core.messages import HumanMessage, SystemMessage

from standard_agent.core.trace import append_event, append_trace, make_entry
from standard_agent.llm.provider import (
    create_llm,
    extract_served_metadata,
    get_llm_metadata,
)
from standard_agent.tools.web_tools import ALL_TOOLS


THOUGHT_REMINDER_TEXT = (
    "REMINDER: Your reply MUST begin with 'THOUGHT:' followed by "
    "your reasoning, and ONLY THEN call a tool. "
    "Calling a tool with empty text content is a protocol violation."
)

NO_TOOL_NUDGE_TEXT = (
    "Your previous reply did not call a tool. Call exactly ONE tool now. "
    "If the task is finished, call stop(answer).")

MAX_STEPS_WARNING = """
You have reached the maximum number of steps ({max_steps}).
Call stop() with your best answer based on what you have found so far.
"""

BROWSER_PROTOCOL = """## Available Actions
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

## Browser Execution Protocol (MANDATORY)
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
- Be concise in your thinking, focus on action."""


def with_thought_reminder(system_prompt: str) -> str:
    """Attach the THOUGHT reminder to the system prompt, never to a tail message."""
    return f"{system_prompt}\n\n{THOUGHT_REMINDER_TEXT}"


def build_system_prompt(task: str, url: str, page_content: str, *,
                        context: str = "",
                        submission_task_type: str | None = None,
                        answer_contract: str = "") -> str:
    """Build the one browser prompt both architectures use.

    ``context`` is the single architecture-specific slot: a high-level plan for
    ReAct, or the current sub-goal plus success condition for the executor.
    """
    prompt = (
        "You are a web automation agent completing tasks on real websites.\n\n"
        f"## Your Task\n{task}\n\n"
        f"## Current Page\n- URL: {url}\n"
        f"- Accessibility Tree (AX Tree):\n{page_content}\n"
    )
    if context:
        prompt += context
    if answer_contract:
        prompt += answer_contract
    prompt += "\n" + BROWSER_PROTOCOL
    return with_thought_reminder(prompt)


def build_execution_messages(task: str, url: str, page_content: str, history, *,
                             context: str = "",
                             submission_task_type: str | None = None,
                             answer_contract: str = "") -> list:
    """System, human and history in the same order for both architectures."""
    return [
        SystemMessage(content=build_system_prompt(
            task, url, page_content, context=context,
            submission_task_type=submission_task_type,
            answer_contract=answer_contract)),
        HumanMessage(content=(f"Please continue completing this task: {task}\n"
                              f"Current page: {url}")),
        *list(history),
    ]


def resolve_observation(obs, state) -> tuple[str, str]:
    """One URL and page-content fallback rule for both architectures."""
    obs = obs or {}
    url = obs.get("url")
    if not url or url == "about:blank":
        url = state.get("url", "about:blank")
    page_content = obs.get("page_content") or state.get("page_content") or "(empty page)"
    return url, page_content


def thread_id(config) -> str:
    try:
        return (config or {}).get("configurable", {}).get("thread_id", "")
    except Exception:
        return ""


_ACTION_INTENT = (
    r"(?i)\b(?:i'?ll|i will|i'm going to|i am going to|let me|i need to|i should)\b"
    r"[^.]{0,120}\b(?:click|type|scroll|navigate|goto|go to|open|select|hover|search|read|check)\b")


def looks_like_action_intent(text: str) -> bool:
    import re
    return bool(re.search(_ACTION_INTENT, str(text or "")))


def has_thought_prefix(content) -> bool:
    return str(content or "").lstrip().startswith("THOUGHT:")


def usage_delta(response) -> dict:
    """Prompt/completion/total tokens reported for one model call."""
    data = extract_served_metadata(response, requested_model="")
    return {
        "prompt_tokens": int(data.get("prompt_tokens") or 0),
        "completion_tokens": int(data.get("completion_tokens") or 0),
        "total_tokens": int(data.get("total_tokens") or 0),
    }


def record_provenance(thread_id: str, step: int, task: str, url: str,
                      response, model_profile: str, architecture: str,
                      phase: str, latency_s: float) -> None:
    """Record the endpoint-reported model identity; diagnostics never fail a trial."""
    try:
        requested = get_llm_metadata(model_profile).get("model", "")
        data = {
            **get_llm_metadata(model_profile),
            **extract_served_metadata(response, requested_model=requested),
            "architecture": architecture,
            "phase": phase,
        }
        data["latency_s"] = round(latency_s, 3)
        append_event(thread_id, event="llm_provenance", step=step,
                     task=task, url=url, data=data)
    except Exception:
        pass


@dataclass
class ExecutorTurn:
    response: object
    messages: list
    thought: str
    thought_present: bool
    protocol_violation: bool
    no_tool_call: bool
    retried: bool
    llm_calls: int
    executor_calls: int
    retry_calls: int
    duration_sec: float
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    extra: dict = field(default_factory=dict)


def run_executor_turn(*, state, config, system_prompt: str,
                      label: str = "ReAct", phase: str = "executor",
                      extra_trace: dict | None = None,
                      llm_factory=create_llm,
                      trace_request: bool = True) -> ExecutorTurn:
    """Run one execution turn under the shared tool/protocol rules."""
    from standard_agent.config import settings

    task = state.get("task", "")
    model_profile = state.get("model_profile", "")
    architecture = state.get("architecture", "react")
    url = state.get("url", "about:blank")
    page_content = state.get("page_content", "(empty page)")
    thread_id = config_thread_id(config)
    extra_trace = dict(extra_trace or {})
    step = int(state.get("step_count", 0)) + 1
    base_calls = int(state.get("llm_calls", 0) or 0)
    base_exec = int(state.get("executor_calls", 0) or 0)
    base_retry = int(state.get("retry_calls", 0) or 0)

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=(f"Please continue completing this task: {task}\n"
                              f"Current page: {url}")),
        *list(state.get("messages", [])),
    ]
    llm = llm_factory(profile_name=model_profile)
    bound = llm.bind_tools(ALL_TOOLS)
    if settings.TRACE_CONSOLE:
        print(f"[{label}][step={step}] QUESTION task={task}")
        print(f"[{label}][step={step}] OBSERVATION url={url} ax_chars={len(page_content)}")
    if trace_request:
        request_data = {
            "message_roles": [getattr(message, "type", "unknown") for message in messages],
            "message_count": len(messages),
            **get_llm_metadata(model_profile),
            "architecture": architecture,
            "phase": phase,
            **extra_trace,
        }
        if settings.TRACE_PROMPTS:
            request_data["messages"] = [
                {"role": getattr(message, "type", "unknown"),
                 "content": str(getattr(message, "content", ""))}
                for message in messages
            ]
        append_event(thread_id, event="llm_request", step=step,
                     task=task, url=url, data=request_data)

    started = time.time()
    try:
        response = bound.invoke(messages)
    except Exception as exc:
        append_event(thread_id, event="llm_error", step=step,
                     task=task, url=url,
                     data={"architecture": architecture, "phase": phase,
                           "error": str(exc), **get_llm_metadata(model_profile),
                           **extra_trace})
        raise
    first_latency = time.time() - started
    record_provenance(thread_id, step, task, url, response, model_profile,
                      architecture, phase, first_latency)
    tokens = usage_delta(response)
    retried = False
    if not (getattr(response, "tool_calls", None) or []):
        append_event(thread_id, event="no_tool_call", step=step,
                     task=task, url=url,
                     data={"architecture": architecture, "phase": phase,
                           "content": str(getattr(response, "content", "")),
                           "finish_reason": (getattr(response, "response_metadata", {}) or {}).get("finish_reason"),
                           "intent_like": looks_like_action_intent(getattr(response, "content", "")),
                           **extra_trace})
        retry_started = time.time()
        retry = bound.invoke([*messages, response,
                              HumanMessage(content=NO_TOOL_NUDGE_TEXT)])
        retried = True
        record_provenance(thread_id, step, task, url, retry, model_profile,
                          architecture, phase + "_retry",
                          time.time() - retry_started)
        retry_tokens = usage_delta(retry)
        for key in tokens:
            tokens[key] += retry_tokens[key]
        append_event(thread_id, event="llm_retry", step=step,
                     task=task, url=url,
                     data={"architecture": architecture, "phase": phase,
                           "content": str(getattr(retry, "content", "")),
                           "tool_calls": len(getattr(retry, "tool_calls", None) or []),
                           "separate_model_call": True,
                           **extra_trace})
        response = retry
    latency = time.time() - started

    thought = str(getattr(response, "content", "") or "")
    tool_calls = getattr(response, "tool_calls", None) or []
    thought_present = has_thought_prefix(thought)
    protocol_violation = bool(tool_calls) and not thought_present
    if protocol_violation:
        append_event(thread_id, event="protocol_violation", step=step,
                     task=task, url=url,
                     data={"architecture": architecture, "phase": phase,
                           "rule": "tool_call_requires_THOUGHT_prefix",
                           "thought_present": False, "content": thought,
                           **extra_trace})
    if len(tool_calls) > 1:
        append_event(thread_id, event="multi_tool_call", step=step,
                     task=task, url=url,
                     data={"architecture": architecture, "phase": phase,
                           "requested": len(tool_calls), "executed": 1,
                           "rule": "one_tool_per_turn", **extra_trace})
        response = response.model_copy(update={"tool_calls": tool_calls[:1]})

    if settings.TRACE_LLM_IO:
        append_event(thread_id, event="llm_response", step=step,
                     task=task, url=url,
                     data={"content": thought,
                           "tool_calls": getattr(response, "tool_calls", []) or [],
                           "thought_present": thought_present,
                           "architecture": architecture, "phase": phase,
                           **get_llm_metadata(model_profile), **extra_trace})

    if settings.TRACE_CONSOLE:
        print(f"[{label}][step={step}] THOUGHT {thought[:1000]}")
        if protocol_violation:
            print(f"[{label}][step={step}] PROTOCOL_VIOLATION missing THOUGHT prefix")

    return ExecutorTurn(
        response=response,
        messages=messages,
        thought=thought,
        thought_present=thought_present,
        protocol_violation=protocol_violation,
        no_tool_call=not (getattr(response, "tool_calls", None) or []),
        retried=retried,
        llm_calls=base_calls + 1 + (1 if retried else 0),
        executor_calls=base_exec + 1,
        retry_calls=base_retry + (1 if retried else 0),
        duration_sec=latency,
        prompt_tokens=tokens["prompt_tokens"],
        completion_tokens=tokens["completion_tokens"],
        total_tokens=tokens["total_tokens"],
        extra={"thought_present": thought_present},
    )


def config_thread_id(config) -> str:
    return thread_id(config)


def no_tool_call_update(*, state, config, turn: ExecutorTurn, label: str,
                        extra_trace: dict | None = None) -> dict:
    """Shared terminal update for a turn that still has no tool after retry."""
    thread_id = config_thread_id(config)
    task = state.get("task", "")
    url = state.get("url", "about:blank")
    architecture = state.get("architecture", "react")
    step = int(state.get("step_count", 0)) + 1
    raw_text = turn.thought
    intent_like = looks_like_action_intent(raw_text)
    extra_trace = dict(extra_trace or {})
    append_event(thread_id, event="protocol_violation", step=step,
                 task=task, url=url,
                 data={"architecture": architecture, "phase": "executor",
                       "rule": "tool_call_required", "intent_like": intent_like,
                       "thought_present": turn.thought_present,
                       "content": raw_text, "retried": True,
                       **extra_trace})
    append_trace(thread_id, make_entry(
        thread_id, step=step, task=task, url=url,
        thought=raw_text, action="final_answer", args={},
        observation="", error=True, done=True, answer="",
        extra={"architecture": architecture, "phase": "executor",
               "no_tool_call": True, "intent_like": intent_like,
               "protocol_failure": "no_tool_call",
               "thought_present": turn.thought_present, **extra_trace}))
    return {
        "messages": [turn.response],
        "step_count": step,
        "done": True,
        "answer": "",
        "error": "no_tool_call",
        "termination": "no_tool_call",
        "url": url,
        "page_content": state.get("page_content", "(empty page)"),
        "llm_calls": turn.llm_calls,
        "executor_calls": turn.executor_calls,
        "retry_calls": turn.retry_calls,
        "executor_tokens": int(state.get("executor_tokens", 0) or 0) + turn.total_tokens,
        "executor_time_sec": round(float(state.get("executor_time_sec", 0) or 0)
                                   + turn.duration_sec, 3),
        "action_history": [{
            "step": step,
            "thought": raw_text,
            "thought_present": turn.thought_present,
            "action": "final_answer",
            "args": {},
            "observation": "",
            "error": True,
            "protocol_failure": "no_tool_call",
        }],
    }
