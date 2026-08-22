"""
StateGraph 构建 - ReAct 单节点自循环。

图拓扑：
    __start__ → agent (LLM + tool_calling)
                  ↻ tools → agent (自循环)
                  → END (无 tool_call / done / max_steps)
"""

from langgraph.graph import StateGraph, END
from langgraph.prebuilt import tools_condition
from langgraph.checkpoint.memory import MemorySaver

from standard_agent.core.state import AgentState
from standard_agent.core.nodes import agent_node, run_tools


def build_graph() -> StateGraph:
    """
    构建并编译 ReAct Agent StateGraph。

    简化版 ReAct 结构：
    - 1 个 agent 节点：LLM 推理 + 决策下一步动作
    - 1 个 tools 节点（LangGraph 内置 ToolNode）：执行 tool_call
    - tools_condition（内置路由）：有 tool_call → tools，无 → END

    使用 MemorySaver checkpointer 实现多轮状态持久化。
    """
    workflow = StateGraph(AgentState)

    # ---- 添加节点 ----
    workflow.add_node("agent", agent_node)
    # run_tools 包装内置 ToolNode，额外记录 action_history + JSONL trace
    workflow.add_node("tools", run_tools)

    # ---- 入口 ----
    workflow.set_entry_point("agent")

    # ---- 条件路由 ----
    # tools_condition: 如果最后一条 AIMessage 有 tool_calls → "tools"，否则 → END
    workflow.add_conditional_edges(
        "agent",
        tools_condition,
    )

    # ---- 工具执行后回到 agent ----
    workflow.add_edge("tools", "agent")

    # ---- 编译 ----
    memory = MemorySaver()
    app = workflow.compile(checkpointer=memory)

    return app
