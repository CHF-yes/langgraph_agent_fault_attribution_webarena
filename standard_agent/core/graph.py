"""
StateGraph 构建 - ReAct 单节点自循环。

图拓扑：
    __start__ → agent (LLM + tool_calling)
                   ↻ tools → agent (未完成)
                   → END (无 tool_call / done / max_steps)
"""

from langgraph.graph import StateGraph, END
from langgraph.prebuilt import tools_condition
from langgraph.checkpoint.memory import MemorySaver

from standard_agent.core.state import AgentState
from standard_agent.core.nodes import agent_node, planner_node, run_tools


def build_graph(architecture: str = "react") -> StateGraph:
    """
    构建并编译指定架构的 Agent StateGraph。

    简化版 ReAct 结构：
    - react：agent → tools → agent 的标准工具调用循环
    - plan_execute：planner → agent → tools → agent；planner 只在任务开始调用一次
    - 两种架构共用相同的工具、浏览器环境、状态和 trace 机制

    使用 MemorySaver checkpointer 实现多轮状态持久化。
    """
    if architecture not in {"react", "plan_execute"}:
        raise ValueError("Unknown architecture. Expected: react, plan_execute")

    workflow = StateGraph(AgentState)

    # ---- 添加节点 ----
    workflow.add_node("agent", agent_node)
    if architecture == "plan_execute":
        workflow.add_node("planner", planner_node)
    # run_tools 包装内置 ToolNode，额外记录 action_history + JSONL trace
    workflow.add_node("tools", run_tools)

    # ---- 入口 ----
    workflow.set_entry_point("planner" if architecture == "plan_execute" else "agent")
    if architecture == "plan_execute":
        workflow.add_edge("planner", "agent")

    # ---- 条件路由 ----
    # tools_condition: 如果最后一条 AIMessage 有 tool_calls → "tools"，否则 → END
    workflow.add_conditional_edges(
        "agent",
        tools_condition,
    )

    # stop 工具会在 run_tools 中设置 done。完成后直接结束，避免额外
    # 调用一次 agent 节点并把 step_count/trace 重复增加。
    workflow.add_conditional_edges(
        "tools",
        lambda state: "end" if state.get("done", False) else "agent",
        {"agent": "agent", "end": END},
    )

    # ---- 编译 ----
    memory = MemorySaver()
    app = workflow.compile(checkpointer=memory)

    return app
