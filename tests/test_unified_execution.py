"""Offline acceptance for the unified ReAct / plan-and-execute execution contract.

No model API and no browser: the LLM and ToolNode are simulated, so these tests
exercise the actual LangGraph wiring, prompt assembly, budget accounting and
terminal-state recording.
"""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from standard_agent.core import execution, graph, nodes
from standard_agent.core.graph import (
    build_graph,
    route_after_pe_tools,
    route_after_replanner,
)
from standard_agent.core.state import AgentState
from standard_agent.evaluation import terminal_diagnostic, termination_status
from standard_agent.trial_metadata import build_trial_record
from standard_agent.webarena_verified import make_agent_response


TASK = "Find the order number and return it as JSON."
URL = "http://example.test/orders"


def _state(**overrides) -> dict:
    state = {
        "task": TASK,
        "model_profile": "m",
        "url": URL,
        "page_content": "[1] link 'Orders' [2] text 'order 42'",
        "step_count": 0,
        "max_steps": 20,
        "done": False,
        "answer": "",
        "submission_schema": {"type": "array", "items": {"type": "string"}},
        "submission_task_type": "RETRIEVE",
        "submission_rejections": 0,
        "action_history": [],
        "architecture": "react",
        "plan": "1. open orders",
        "plan_steps": [{"id": 1, "goal": "open the orders page",
                        "success_condition": "order list is visible"}],
        "current_plan_step": 0,
        "plan_revision": 0,
        "replan_required": False,
        "llm_calls": 0,
        "planning_calls": 0,
        "executor_calls": 0,
        "replanning_calls": 0,
        "retry_calls": 0,
        "planning_tokens": 0,
        "executor_tokens": 0,
        "replanning_tokens": 0,
        "planning_time_sec": 0.0,
        "executor_time_sec": 0.0,
        "replanning_time_sec": 0.0,
        "finish_requested": False,
        "submission_correction_pending": False,
        "messages": [],
    }
    state.update(overrides)
    return state


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.bind_calls = 0

    def bind_tools(self, tools):
        self.bind_calls += 1
        return self

    def invoke(self, messages):
        self.calls.append(messages)
        if not self.replies:
            raise AssertionError("unexpected extra LLM call")
        return self.replies.pop(0)


class FakeToolNode:
    def __init__(self, tools):
        pass

    def invoke(self, state):
        calls = state["messages"][-1].tool_calls
        return {"messages": [
            ToolMessage(content=f"[OK] executed {call['name']}",
                        tool_call_id=call["id"], name=call["name"])
            for call in calls
        ]}


def _tool_call(name="click", args=None, content="THOUGHT: act"):
    return AIMessage(content=content, tool_calls=[{
        "name": name, "args": args or {"element_id": "1"},
        "id": "call_1", "type": "tool_call",
    }])


def _stop(answer):
    return AIMessage(content="THOUGHT: submit", tool_calls=[{
        "name": "stop", "args": {"answer": answer},
        "id": "stop_1", "type": "tool_call",
    }])


def _events(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


# ---------------------------------------------------------------------------
# Shared prompt, message order and prompt hygiene
# ---------------------------------------------------------------------------

def test_shared_prompt_contains_one_browser_contract():
    react = nodes._react_system_prompt(TASK, URL, "AX", plan_context="")
    executor = nodes._plan_executor_prompt(
        TASK, {"goal": "open orders", "success_condition": "list visible"},
        URL, "AX")
    for prompt in (react, executor):
        assert execution.BROWSER_PROTOCOL in prompt
        assert execution.THOUGHT_REMINDER_TEXT in prompt
        assert "Do NOT repeat the same failed action more than twice" in prompt
        for tool in ("click", "type_text", "scroll", "goto", "go_back",
                     "go_forward", "select_option", "hover", "stop"):
            assert f"**{tool}(" in prompt
        assert nodes._answer_contract_context(TASK, "RETRIEVE") in prompt
    context = nodes._execution_context(
        {"goal": "open orders", "success_condition": "list visible"})
    assert executor.replace(context, "") == react
    assert "Current sub-goal: open orders" in executor
    assert "Success condition: list visible" in executor
    assert "## Execution Context" in executor
    assert "High-Level Plan" not in executor


def test_prompt_does_not_leak_fault_or_scoring_internals():
    for prompt in (
        nodes._react_system_prompt(TASK, URL, "AX"),
        nodes._plan_executor_prompt(TASK, {"goal": "g"}, URL, "AX"),
    ):
        lowered = prompt.casefold()
        for marker in ("injection", "fault", "window_steps", "target_endpoint",
                       "expected value", "ground truth", "fault_seed"):
            assert marker not in lowered


def test_both_nodes_send_system_human_history_in_same_order(monkeypatch):
    history = [AIMessage(content="THOUGHT: prior"),
               ToolMessage(content="[OK] prior", tool_call_id="c0")]
    replies = [_tool_call(), _tool_call()]
    fake = FakeLLM(replies)
    monkeypatch.setattr(nodes, "create_llm", lambda **kw: fake)
    monkeypatch.setattr(nodes, "get_current_observation",
                        lambda: {"url": URL, "page_content": "AX"})
    react_state = _state(messages=list(history), architecture="react", plan="")
    pe_state = _state(messages=list(history), architecture="plan_execute")

    nodes.agent_node(react_state, {"configurable": {"thread_id": "t-react"}})
    nodes.plan_executor_node(pe_state, {"configurable": {"thread_id": "t-pe"}})
    for call in fake.calls:
        assert [getattr(m, "type", "") for m in call[:4]] == [
            "system", "human", "ai", "tool"]
        assert call[1].content.startswith("Please continue completing this task:")
    react_system = fake.calls[0][0].content
    pe_system = fake.calls[1][0].content
    assert execution.BROWSER_PROTOCOL in react_system
    assert execution.BROWSER_PROTOCOL in pe_system
    context = nodes._execution_context(
        {"goal": "open the orders page", "success_condition": "order list is visible"}
    )
    assert context in pe_system
    assert pe_system.replace(context, "", 1) == react_system
    assert fake.calls[0][1:] == fake.calls[1][1:]


# ---------------------------------------------------------------------------
# Shared tool-call protocol
# ---------------------------------------------------------------------------

def _run_node(monkeypatch, architecture, replies, state=None):
    fake = FakeLLM(replies)
    monkeypatch.setattr(nodes, "create_llm", lambda **kw: fake)
    monkeypatch.setattr(nodes, "get_current_observation",
                        lambda: {"url": URL, "page_content": "AX"})
    state = state or _state(architecture=architecture)
    node = nodes.agent_node if architecture == "react" else nodes.plan_executor_node
    return node(state, {"configurable": {"thread_id": f"t-{architecture}"}}), fake


@pytest.mark.parametrize("architecture", ["react", "plan_execute"])
def test_multi_tool_reply_executes_exactly_one(monkeypatch, tmp_path, architecture):
    monkeypatch.delenv("TRACE_RUN_ID", raising=False)
    monkeypatch.setenv("TRACE_DIR", str(tmp_path))
    reply = AIMessage(content="THOUGHT: two", tool_calls=[
        {"name": "click", "args": {"element_id": "1"}, "id": "a", "type": "tool_call"},
        {"name": "click", "args": {"element_id": "2"}, "id": "b", "type": "tool_call"},
    ])
    out, fake = _run_node(monkeypatch, architecture, [reply])
    assert len(out["messages"][-1].tool_calls) == 1
    events = _events(tmp_path / f"t-{architecture}.jsonl")
    multi = [e for e in events if e.get("event") == "multi_tool_call"]
    assert multi and multi[-1]["requested"] == 2 and multi[-1]["executed"] == 1


@pytest.mark.parametrize("architecture", ["react", "plan_execute"])
def test_no_tool_retry_success_and_failure_match(monkeypatch, tmp_path, architecture):
    monkeypatch.setenv("TRACE_DIR", str(tmp_path / architecture))
    out, fake = _run_node(
        monkeypatch, architecture,
        [AIMessage(content="THOUGHT: I will click"), _tool_call()])
    assert len(fake.calls) == 2
    assert fake.calls[1][-1].content == execution.NO_TOOL_NUDGE_TEXT
    assert out["messages"][-1].tool_calls and "done" not in out
    assert out["retry_calls"] == 1

    monkeypatch.setenv("TRACE_DIR", str(tmp_path / f"{architecture}-fail"))
    out2, fake2 = _run_node(
        monkeypatch, architecture,
        [AIMessage(content="THOUGHT: I will click"),
         AIMessage(content="THOUGHT: I will still click")])
    assert len(fake2.calls) == 2
    assert out2["done"] is True
    assert out2["answer"] == ""
    assert out2["error"] == "no_tool_call"
    assert out2["termination"] == "no_tool_call"
    assert out2["action_history"][-1]["protocol_failure"] == "no_tool_call"


@pytest.mark.parametrize("architecture", ["react", "plan_execute"])
def test_tool_error_observation_is_passed_through_unchanged(
        monkeypatch, architecture):
    class ErrorToolNode(FakeToolNode):
        def invoke(self, state):
            return {"messages": [ToolMessage(
                content="[Error] 500 Internal Server Error",
                tool_call_id="call_1", name="click")]}

    monkeypatch.setattr("langgraph.prebuilt.ToolNode", ErrorToolNode)
    state = _state(architecture=architecture, messages=[
        _tool_call(), ToolMessage(content="[Error] 500 Internal Server Error",
                                  tool_call_id="call_1", name="click")])
    out = nodes.run_tools(state, {"configurable": {"thread_id": "t-error"}})
    assert out["messages"][-1].content == "[Error] 500 Internal Server Error"
    assert out["action_history"][-1]["error"] is True
    assert out["action_history"][-1]["observation"].startswith("[Error] 500")


@pytest.mark.parametrize("architecture", ["react", "plan_execute"])
def test_thought_prefix_is_recorded_for_executed_reply(monkeypatch, architecture):
    monkeypatch.setattr("langgraph.prebuilt.ToolNode", FakeToolNode)
    for content, expected in (("THOUGHT: act", True), ("I will click", False)):
        state = _state(architecture=architecture, messages=[_tool_call(content=content)])
        out = nodes.run_tools(state, {"configurable": {"thread_id": "t-thought"}})
        assert out["action_history"][-1]["thought_present"] is expected


# ---------------------------------------------------------------------------
# Submission correction and finish handoff
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("architecture", ["react", "plan_execute"])
def test_first_invalid_then_valid_submission(monkeypatch, architecture):
    monkeypatch.setenv("SUBMISSION_CONTRACT", "1")
    monkeypatch.setattr("langgraph.prebuilt.ToolNode", FakeToolNode)
    invalid = nodes.run_tools(
        _state(architecture=architecture, messages=[_stop("The order is 42.")]),
        {"configurable": {"thread_id": "t-invalid"}})
    assert not invalid.get("done")
    assert invalid["submission_correction_pending"] is True
    assert invalid["messages"][-1].content.startswith("[Error] Submission rejected")
    assert "current plan step has not advanced" in invalid["messages"][-1].content.casefold()
    assert "current_plan_step" not in invalid

    corrected_state = _state(
        architecture=architecture,
        messages=[_stop('["42"]')],
        submission_rejections=1,
        submission_correction_pending=True)
    corrected = nodes.run_tools(corrected_state,
                                {"configurable": {"thread_id": "t-corrected"}})
    assert corrected["done"] is True
    assert corrected["answer"] == '["42"]'
    assert corrected["termination"] == "stop"


@pytest.mark.parametrize("architecture", ["react", "plan_execute"])
def test_two_invalid_submissions_are_a_protocol_failure(monkeypatch, architecture):
    monkeypatch.setenv("SUBMISSION_CONTRACT", "1")
    monkeypatch.setattr("langgraph.prebuilt.ToolNode", FakeToolNode)
    out = nodes.run_tools(
        _state(architecture=architecture, messages=[_stop("not json")],
               submission_rejections=1),
        {"configurable": {"thread_id": "t-two-invalid"}})
    assert out["done"] is True
    assert out["answer"] == ""
    assert out["error"] == "submission_contract_failed"
    assert out["termination"] == "submission_contract_failed"


def test_plan_execute_correction_skips_replanner_in_the_graph(monkeypatch, tmp_path):
    monkeypatch.setenv("SUBMISSION_CONTRACT", "1")
    monkeypatch.delenv("TRACE_RUN_ID", raising=False)
    monkeypatch.setenv("TRACE_DIR", str(tmp_path))
    monkeypatch.setattr("langgraph.prebuilt.ToolNode", FakeToolNode)
    monkeypatch.setattr(nodes, "get_current_observation",
                        lambda: {"url": URL, "page_content": "AX"})
    fake = FakeLLM([
        AIMessage(content='{"steps":[{"id":1,"goal":"open orders",'
                          '"success_condition":"list visible"}]}'),
        _stop("The order is 42."),
        _stop('["42"]'),
    ])
    monkeypatch.setattr(nodes, "create_llm", lambda **kw: fake)
    state = _state(architecture="plan_execute")
    out = build_graph("plan_execute").invoke(
        state, {"configurable": {"thread_id": "graph-correction"}})
    assert out["done"] is True and out["answer"] == '["42"]'
    assert out["submission_rejections"] == 1
    assert out["current_plan_step"] == 0
    assert len(fake.calls) == 3          # planner, invalid executor, corrected executor
    events = _events(tmp_path / "graph-correction.jsonl")
    assert not [e for e in events if e.get("event") == "replan_decision"]
    assert [e for e in events if e.get("event") == "submission_rejected"]


def test_plan_execute_two_invalid_submissions_end_without_replanner(monkeypatch, tmp_path):
    monkeypatch.setenv("SUBMISSION_CONTRACT", "1")
    monkeypatch.delenv("TRACE_RUN_ID", raising=False)
    monkeypatch.setenv("TRACE_DIR", str(tmp_path))
    monkeypatch.setattr("langgraph.prebuilt.ToolNode", FakeToolNode)
    monkeypatch.setattr(nodes, "get_current_observation",
                        lambda: {"url": URL, "page_content": "AX"})
    fake = FakeLLM([
        AIMessage(content='{"steps":[{"id":1,"goal":"open orders",'
                          '"success_condition":"list visible"}]}'),
        _stop("not json"),
        _stop("still not json"),
    ])
    monkeypatch.setattr(nodes, "create_llm", lambda **kw: fake)
    out = build_graph("plan_execute").invoke(
        _state(architecture="plan_execute"),
        {"configurable": {"thread_id": "graph-two-invalid"}})
    assert out["done"] is True and out["answer"] == ""
    assert out["error"] == "submission_contract_failed"
    assert out["current_plan_step"] == 0
    assert len(fake.calls) == 3


def test_replanner_finish_only_requests_executor_wrap_up(monkeypatch, tmp_path):
    monkeypatch.delenv("TRACE_RUN_ID", raising=False)
    monkeypatch.setenv("TRACE_DIR", str(tmp_path))
    monkeypatch.setattr("langgraph.prebuilt.ToolNode", FakeToolNode)
    monkeypatch.setattr(nodes, "get_current_observation",
                        lambda: {"url": URL, "page_content": "AX"})
    fake = FakeLLM([
        AIMessage(content='{"steps":[{"id":1,"goal":"open orders",'
                          '"success_condition":"list visible"}]}'),
        _tool_call(),
        AIMessage(content='{"decision":"finish","reason":"found it"}'),
        _stop('["42"]'),
    ])
    monkeypatch.setattr(nodes, "create_llm", lambda **kw: fake)
    out = build_graph("plan_execute").invoke(
        _state(architecture="plan_execute"),
        {"configurable": {"thread_id": "graph-finish"}})
    assert out["done"] is True and out["answer"] == '["42"]'
    wrap_up_prompt = fake.calls[3][0].content
    assert "Finalise the task" in wrap_up_prompt
    assert "still call stop(answer)" in wrap_up_prompt
    events = _events(tmp_path / "graph-finish.jsonl")
    decisions = [e for e in events if e.get("event") == "replan_decision"]
    assert decisions and decisions[-1]["decision"] == "finish"


def test_replanner_finish_does_not_set_done(monkeypatch):
    class FinishLLM:
        def invoke(self, messages):
            return AIMessage(content='{"decision":"finish","reason":"done"}')

    monkeypatch.setattr(nodes, "create_llm", lambda **kw: FinishLLM())
    state = _state(architecture="plan_execute",
                   messages=[ToolMessage(content="[OK] done", tool_call_id="x")])
    out = nodes.replanner_node(state, {"configurable": {"thread_id": "t"}})
    assert out["finish_requested"] is True
    assert "done" not in out
    assert "answer" not in out
    assert "current_plan_step" not in out


def test_routing_helpers():
    assert route_after_pe_tools({"done": True}) == "end"
    assert route_after_pe_tools({"submission_correction_pending": True}) == "executor"
    assert route_after_pe_tools({}) == "replanner"
    assert route_after_replanner({"done": True}) == "end"
    assert route_after_replanner({"finish_requested": True}) == "executor"
    assert route_after_replanner({"replan_required": True}) == "planner"
    assert route_after_replanner({}) == "executor"


# ---------------------------------------------------------------------------
# Budget, plan accounting and terminal states
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("architecture", ["react", "plan_execute"])
def test_twentieth_execution_turn_stops_before_another_llm_call(
        monkeypatch, architecture):
    fake = FakeLLM([])
    monkeypatch.setattr(nodes, "create_llm", lambda **kw: fake)
    monkeypatch.setattr(nodes, "get_current_observation",
                        lambda: {"url": URL, "page_content": "AX"})
    state = _state(architecture=architecture, step_count=20)
    node = nodes.agent_node if architecture == "react" else nodes.plan_executor_node
    out = node(state, {"configurable": {"thread_id": f"cap-{architecture}"}})
    assert out["done"] is True
    assert out["termination"] == "cap_exhausted"
    assert out["cap_exhausted"] is True
    assert "Reached max steps (20)" in out["answer"]
    assert fake.calls == []


@pytest.mark.parametrize("architecture", ["react", "plan_execute"])
def test_nineteenth_turn_is_the_last_execution_turn(monkeypatch, architecture):
    fake = FakeLLM([_tool_call()])
    monkeypatch.setattr(nodes, "create_llm", lambda **kw: fake)
    monkeypatch.setattr(nodes, "get_current_observation",
                        lambda: {"url": URL, "page_content": "AX"})
    state = _state(architecture=architecture, step_count=19)
    node = nodes.agent_node if architecture == "react" else nodes.plan_executor_node
    out = node(state, {"configurable": {"thread_id": f"turn20-{architecture}"}})
    assert out["step_count"] == 20
    assert not out.get("done", False)
    assert len(fake.calls) == 1


@pytest.mark.parametrize("architecture", ["react", "plan_execute"])
@pytest.mark.parametrize("submit_on_last_turn", [False, True])
def test_graph_honors_the_last_execution_turn(
        monkeypatch, tmp_path, architecture, submit_on_last_turn):
    monkeypatch.setenv("SUBMISSION_CONTRACT", "1")
    monkeypatch.setenv("TRACE_DIR", str(tmp_path))
    monkeypatch.setattr("langgraph.prebuilt.ToolNode", FakeToolNode)
    monkeypatch.setattr(nodes, "get_current_observation",
                        lambda: {"url": URL, "page_content": "AX"})
    last_reply = _stop('["42"]') if submit_on_last_turn else _tool_call()
    replies = [last_reply]
    if architecture == "plan_execute":
        replies.insert(0, AIMessage(content='{"steps":[{"id":1,"goal":"open orders",'
                                           '"success_condition":"list visible"}]}'))
    fake = FakeLLM(replies)
    monkeypatch.setattr(nodes, "create_llm", lambda **kwargs: fake)

    out = build_graph(architecture).invoke(
        _state(architecture=architecture, step_count=19, plan=""),
        {"configurable": {"thread_id": "graph-last-turn"}})

    assert out["done"] is True
    assert out["step_count"] == 20
    assert out["executor_calls"] == 1
    assert out["replanning_calls"] == 0
    assert out["planning_calls"] == (1 if architecture == "plan_execute" else 0)
    assert out["termination"] == ("stop" if submit_on_last_turn else "cap_exhausted")
    if submit_on_last_turn:
        assert out["answer"] == '["42"]'


def test_planning_and_replanning_do_not_consume_execution_turns(monkeypatch, tmp_path):
    monkeypatch.setenv("TRACE_DIR", str(tmp_path))
    usage = {"input_tokens": 5, "output_tokens": 3, "total_tokens": 8}

    class PlanLLM:
        def __init__(self):
            self.bind_calls = 0

        def bind_tools(self, tools):
            self.bind_calls += 1
            return self

        def invoke(self, messages):
            return AIMessage(
                content='{"steps":[{"id":1,"goal":"g","success_condition":"s"}]}',
                usage_metadata=usage)

    planner = PlanLLM()
    monkeypatch.setattr(nodes, "create_llm", lambda **kw: planner)
    out = nodes.planner_node(_state(step_count=7),
                             {"configurable": {"thread_id": "planner"}})
    assert out["planning_calls"] == 1
    assert out["planning_tokens"] == 8
    assert out["planning_time_sec"] >= 0
    assert "step_count" not in out
    assert planner.bind_calls == 0

    class ReplanLLM:
        def __init__(self):
            self.bind_calls = 0

        def bind_tools(self, tools):
            self.bind_calls += 1
            return self

        def invoke(self, messages):
            return AIMessage(content='{"decision":"continue","reason":"ok"}',
                             usage_metadata=usage)

    replanner = ReplanLLM()
    monkeypatch.setattr(nodes, "create_llm", lambda **kw: replanner)
    out2 = nodes.replanner_node(
        _state(architecture="plan_execute", step_count=7,
               messages=[ToolMessage(content="[OK]", tool_call_id="x")]),
        {"configurable": {"thread_id": "replanner"}})
    assert out2["replanning_calls"] == 1
    assert out2["replanning_tokens"] == 8
    assert out2["replanning_time_sec"] >= 0
    assert "step_count" not in out2
    assert replanner.bind_calls == 0


def test_terminal_states_are_distinguishable():
    assert termination_status(True, '["42"]', None, "RETRIEVE") == "stop"
    assert termination_status(True, "", None, "RETRIEVE") == "empty_submission"
    assert termination_status(False, "", None, "RETRIEVE") == "incomplete"
    assert termination_status(True, "", "no_tool_call", "RETRIEVE") == "no_tool_call"
    assert termination_status(True, "Reached max steps (20)", None) == "cap_exhausted"
    assert termination_status(True, "", "submission_contract_failed") == \
        "submission_contract_failed"


def test_trial_record_carries_terminal_and_accounting_fields():
    record = build_trial_record(
        model_profile="m", architecture="plan_execute", fault_type="web_http_error",
        task_id=228, seed=1, condition="fault", run_config={},
        completed=False, success=False, steps=20, cap_exhausted=False,
        llm_calls=9, planning_calls=1, executor_calls=7, replanning_calls=1,
        retry_calls=1, planning_tokens=10, executor_tokens=100,
        replanning_tokens=20, planning_time_sec=1.5, executor_time_sec=9.0,
        replanning_time_sec=2.0, termination="no_tool_call",
        protocol_failure=True, empty_submission=False, answer_empty=True,
        error="no_tool_call")
    assert record["termination"] == "no_tool_call"
    assert record["protocol_failure"] is True
    assert record["empty_submission"] is False
    assert record["answer_empty"] is True
    assert record["planning_tokens"] == 10
    assert record["executor_time_sec"] == 9.0
    assert record["replanning_calls"] == 1


def test_scoring_artifact_distinguishes_terminal_states():
    assert terminal_diagnostic("stop") is None
    assert "no tool call" in terminal_diagnostic("no_tool_call")
    assert "corrected stop" in terminal_diagnostic("submission_contract_failed")
    assert "empty submission" in terminal_diagnostic("empty_submission")
    assert "incomplete" in terminal_diagnostic("incomplete")
    assert "max_steps" in terminal_diagnostic("cap_exhausted", 20)

    task = {"task_id": 1, "eval": [{
        "results_schema": {"type": "array", "items": {"type": "string"}},
        "expected": {"task_type": "retrieve", "status": "SUCCESS"},
    }]}
    cases = {
        "no_tool_call": terminal_diagnostic("no_tool_call"),
        "submission_contract_failed": terminal_diagnostic("submission_contract_failed"),
        "empty_submission": terminal_diagnostic("empty_submission"),
        "cap_exhausted": terminal_diagnostic("cap_exhausted", 20),
    }
    details = {}
    for name, diagnostic in cases.items():
        response = make_agent_response(task, completed=False, answer="",
                                       diagnostic=diagnostic)
        assert response["status"] == "UNKNOWN_ERROR"
        details[name] = response["error_details"]
    assert len(set(details.values())) == len(cases)

    success = make_agent_response(task, completed=True, answer='["42"]',
                                  diagnostic=None)
    assert success["status"] == "SUCCESS"
    assert success["retrieved_data"] == ["42"]


def test_react_graph_stops_only_after_stop_tool(monkeypatch, tmp_path):
    monkeypatch.setenv("SUBMISSION_CONTRACT", "1")
    monkeypatch.delenv("TRACE_RUN_ID", raising=False)
    monkeypatch.setenv("TRACE_DIR", str(tmp_path))
    monkeypatch.setattr("langgraph.prebuilt.ToolNode", FakeToolNode)
    fake = FakeLLM([_stop('["42"]')])
    monkeypatch.setattr(nodes, "create_llm", lambda **kw: fake)
    out = build_graph("react").invoke(
        _state(architecture="react"),
        {"configurable": {"thread_id": "graph-react-stop"}})
    assert out["done"] is True
    assert out["answer"] == '["42"]'
    assert out["termination"] == "stop"
    assert len(fake.calls) == 1
