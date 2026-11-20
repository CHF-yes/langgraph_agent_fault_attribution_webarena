"""
StateGraph 构建 - ReAct 与 plan-and-execute 共用一个执行器契约。

图拓扑：
    react:         __start__ → agent ↻ tools → agent → END
    plan_execute:  __start__ → planner → executor ↻ tools → replanner
                              ↖__________________________|

两个架构只在计划层不同；执行、工具、预算和终止规则由 nodes.py 的共享实现保证。
"""

from langgraph.graph import StateGraph, END
from langgraph.prebuilt import tools_condition
from langgraph.checkpoint.memory import MemorySaver

from standard_agent.core.state import AgentState
from standard_agent.core.nodes import (
    agent_node,
    planner_node,
    plan_executor_node,
    replanner_node,
    run_tools,
)


def route_after_react_tools(state: AgentState) -> str:
    return "end" if state.get("done", False) else "agent"


def route_after_executor(state: AgentState) -> str:
    if state.get("done", False):
        return "end"
    return tools_condition(state)


def route_after_pe_tools(state: AgentState) -> str:
    """Let the executor handle corrections and an exhausted execution budget."""
    if state.get("done", False):
        return "end"
    if (state.get("submission_correction_pending", False)
            or state.get("step_count", 0) >= state.get("max_steps", 30)):
        return "executor"
    return "replanner"


def route_after_replanner(state: AgentState) -> str:
    if state.get("done", False):
        return "end"
    if state.get("finish_requested", False):
        return "executor"
    if state.get("replan_required"):
        return "planner"
    return "executor"


def build_graph(architecture: str = "react") -> StateGraph:
    """
    构建并编译指定架构的 Agent StateGraph。

    - react：agent → tools → agent 的标准工具调用循环
    - plan_execute：planner → executor → tools → replanner
    - 两种架构共用相同的工具、浏览器环境、状态、执行回合、提交与 trace 机制

    使用 MemorySaver checkpointer 实现多轮状态持久化。
    """
    if architecture not in {"react", "plan_execute"}:
        raise ValueError("Unknown architecture. Expected: react, plan_execute")

    workflow = StateGraph(AgentState)

    if architecture == "react":
        workflow.add_node("agent", agent_node)
    else:
        workflow.add_node("planner", planner_node)
        workflow.add_node("executor", plan_executor_node)
        workflow.add_node("replanner", replanner_node)
    workflow.add_node("tools", run_tools)

    workflow.set_entry_point("planner" if architecture == "plan_execute" else "agent")
    if architecture == "plan_execute":
        workflow.add_edge("planner", "executor")

    if architecture == "plan_execute":
        workflow.add_conditional_edges(
            "executor", route_after_executor,
            {"tools": "tools", "end": END},
        )
        workflow.add_conditional_edges(
            "tools", route_after_pe_tools,
            {"replanner": "replanner", "executor": "executor", "end": END},
        )
        workflow.add_conditional_edges(
            "replanner", route_after_replanner,
            {"planner": "planner", "executor": "executor", "end": END},
        )
    else:
        workflow.add_conditional_edges(
            "agent", tools_condition, {"tools": "tools", END: END},
        )
        workflow.add_conditional_edges(
            "tools", route_after_react_tools,
            {"agent": "agent", "end": END},
        )

    memory = MemorySaver()
    return workflow.compile(checkpointer=memory)
