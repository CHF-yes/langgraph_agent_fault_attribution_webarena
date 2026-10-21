"""ReAct 协议回归：THOUGHT 提醒的位置与"无工具调用"的处理。

背景：把 THOUGHT 提醒作为尾部 SystemMessage 追加在 HumanMessage 之后时，
deepseek-flash 会返回 finish_reason=stop、tool_calls=[] 的纯文本（"I'll click …"），
harness 曾把它当作最终答案（done=True, error=False）。这些测试锁定修复：
提醒并入 system prompt；无工具调用先重试一次，仍无则标记协议失败而不是成功。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from langchain_core.messages import AIMessage

from standard_agent.core import nodes


def _state():
    return {
        "task": "find the reviews", "model_profile": "m",
        "url": "http://example.test/p", "page_content": "[1] link 'Reviews'",
        "step_count": 0, "max_steps": 5, "done": False, "answer": "",
        "action_history": [], "architecture": "react", "plan": "",
        "plan_steps": [], "current_plan_step": 0, "plan_revision": 0,
        "replan_required": False, "llm_calls": 0, "planning_calls": 0,
        "executor_calls": 0, "replanning_calls": 0, "messages": [],
    }


class _Bound:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        return self.replies.pop(0)


class _FakeLLM:
    def __init__(self, replies):
        self.bound = _Bound(replies)

    def bind_tools(self, tools):
        return self.bound


def _run(monkeypatch, tmp_path, replies):
    monkeypatch.setenv("TRACE_DIR", str(tmp_path))
    fake = _FakeLLM(replies)
    monkeypatch.setattr(nodes, "create_llm", lambda **kwargs: fake)
    result = nodes.agent_node(_state(), {"configurable": {"thread_id": "protocol-test"}})
    return fake, result


def test_react_messages_have_no_trailing_system_message():
    msgs = nodes._react_full_messages("SYSTEM", "task", "http://h/", [])
    assert [type(m).__name__ for m in msgs] == ["SystemMessage", "HumanMessage"]
    assert nodes.THOUGHT_REMINDER_TEXT in msgs[0].content
    assert "REMINDER" not in msgs[-1].content


def test_no_tool_call_is_retried_and_the_retry_is_used(monkeypatch, tmp_path):
    text = AIMessage(content="THOUGHT: I'll click the Reviews tab.")
    call = AIMessage(content="THOUGHT: clicking now.",
                     tool_calls=[{"name": "click", "args": {"element_id": "1"},
                                  "id": "c1", "type": "tool_call"}])
    fake, result = _run(monkeypatch, tmp_path, [text, call])
    assert len(fake.bound.calls) == 2          # 首次 + 重试
    # 重试消息以 HumanMessage 收尾，绝不追加 SystemMessage
    assert type(fake.bound.calls[1][-1]).__name__ == "HumanMessage"
    assert not result.get("done", False)      # 继续执行，未结束
    assert result["messages"][-1].tool_calls
    events = [json.loads(line).get("event") for line in (tmp_path / "protocol-test.jsonl").read_text().splitlines()]
    assert "no_tool_call" in events and "llm_retry" in events


def test_two_text_only_replies_are_a_protocol_failure(monkeypatch, tmp_path):
    text = AIMessage(content="THOUGHT: I need to read the reviews. I'll click Reviews.")
    fake, result = _run(monkeypatch, tmp_path, [text, text])
    assert result["done"] is True
    assert result["answer"] == ""             # 不作为最终答案提交
    assert result["error"] == "no_tool_call"
    assert result["action_history"][-1]["error"] is True
    events = [json.loads(line) for line in (tmp_path / "protocol-test.jsonl").read_text().splitlines()]
    violations = [e for e in events if e.get("event") == "protocol_violation"]
    # append_event 把 data 字段平铺到事件上
    assert violations and violations[-1].get("rule") == "tool_call_required"
    assert violations[-1].get("intent_like") is True


def test_error_survives_the_compiled_graph_and_reaches_the_record(tmp_path):
    """节点 → 真实编译图 → trial record，在独立进程里验证（避免跨测试模块污染）。"""
    import os
    import subprocess
    import textwrap

    script = textwrap.dedent("""
        import json, os
        from langchain_core.messages import AIMessage
        import standard_agent.core.nodes as nodes

        class Bound:
            def __init__(self):
                self.calls = 0

            def invoke(self, messages):
                self.calls += 1
                return AIMessage(content="THOUGHT: I need to read the reviews. I'll click Reviews.")

        class Fake:
            def __init__(self):
                self.bound = Bound()

            def bind_tools(self, tools):
                return self.bound

        nodes.create_llm = lambda **kwargs: Fake()
        from standard_agent.core.graph import build_graph
        from standard_agent.trial_metadata import build_trial_record

        state = {
            "task": "t", "model_profile": "m", "url": "http://example.test/p",
            "page_content": "AX", "step_count": 0, "max_steps": 5,
            "done": False, "answer": "", "action_history": [], "architecture": "react",
            "plan": "", "plan_steps": [], "current_plan_step": 0, "plan_revision": 0,
            "replan_required": False, "llm_calls": 0, "planning_calls": 0,
            "executor_calls": 0, "replanning_calls": 0, "messages": [],
        }
        app = build_graph("react")
        out = app.invoke(state, {"configurable": {"thread_id": "graph-state-test"}})
        record = build_trial_record(
            model_profile="m", architecture="react", fault_type="f", task_id=24,
            seed=1, condition="fault", run_config={}, replicate=0,
            error=out.get("error"))
        print("RESULT" + json.dumps({"error": out.get("error"),
                                     "answer": out.get("answer"),
                                     "record_error": record["error"]}))
    """)
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["TRACE_DIR"] = str(tmp_path)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(root), str(root / ".venv311/lib/python3.11/site-packages"),
         env.get("PYTHONPATH", "")]).strip(os.pathsep)
    proc = subprocess.run([sys.executable, "-c", script], cwd=root, env=env,
                          capture_output=True, text=True, timeout=120)
    results = [line[6:] for line in proc.stdout.splitlines() if line.startswith("RESULT")]
    assert results, proc.stderr[-2000:]
    data = json.loads(results[-1])
    assert data == {"error": "no_tool_call", "answer": "", "record_error": "no_tool_call"}


def test_plan_executor_has_the_same_retry_and_failure_semantics(tmp_path, monkeypatch):
    text = AIMessage(content="THOUGHT: I'll click the Reviews tab.")
    call = AIMessage(content="THOUGHT: clicking now.",
                     tool_calls=[{"name": "click", "args": {"element_id": "1"},
                                  "id": "c1", "type": "tool_call"}])
    monkeypatch.setenv("TRACE_DIR", str(tmp_path))
    monkeypatch.setattr(nodes, "get_current_observation",
                        lambda: {"url": "http://example.test/p", "page_content": "AX"})
    state = _state()
    state.update(architecture="plan_execute",
                 plan_steps=[{"id": 1, "goal": "read reviews", "success_condition": "done"}],
                 current_plan_step=0)

    # 第一次纯文本 → 重试得到 tool call，继续执行
    fake = _FakeLLM([text, call])
    monkeypatch.setattr(nodes, "create_llm", lambda **kw: fake)
    out = nodes.plan_executor_node(state, {"configurable": {"thread_id": "pe-retry"}})
    assert len(fake.bound.calls) == 2
    assert out["messages"][-1].tool_calls
    assert "error" not in out

    # 两次纯文本 → 协议失败：空答案 + error，而不是把文本当答案
    fake2 = _FakeLLM([text, text])
    monkeypatch.setattr(nodes, "create_llm", lambda **kw: fake2)
    out2 = nodes.plan_executor_node(state, {"configurable": {"thread_id": "pe-fail"}})
    assert out2["done"] is True
    assert out2["answer"] == "" and out2["error"] == "no_tool_call"
