"""
LangGraph 编排：第一层理解 → Supervisor 多智能体分配 → 子 Agent → 收口/合并。

  START → understand（LLM 改写 + 意图识别 + 槽位 + 多意图拆解）
        → supervisor --intent--> presale | insale | aftersale | chitchat | handoff
        → collect --(还有待办意图且无阻塞)--> supervisor   ← 多意图串行派发的回边
                 --(否则)--> compose → END

设计要点：
- 图只编译一次（get_graph 缓存）；DB 会话通过 runtime contextvar 注入，按请求隔离。
- 循环不是「Agent 自己想再干一轮」，而是「第一层识别出来的待办队列」，
  队列长度由 NLU_MAX_SUB_INTENTS 限死，边界确定、可压测、不会失控。
- 二次确认卡是任务断点：collect 遇到确认卡立即停止派发，剩余意图交给
  chat_service 写进 AgentTask.checkpoint，跨会话可续点（LangGraph 自身
  的 checkpointer 只管线程内状态，跨进程/跨会话的持久化由记忆层负责）。
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

# 触发工具注册（register_tool 的副作用）
import app.agents.tools.read_tools  # noqa: F401
import app.agents.tools.write_tools  # noqa: F401
import app.agents.tools.web_search_tool  # noqa: F401
import app.agents.tools.product_bench_tool  # noqa: F401
import app.agents.tools.service_tools  # noqa: F401
import app.agents.tools.copy_tool  # noqa: F401

from app.agents.nodes.agent_node import make_agent_node
from app.agents.nodes.chitchat import chitchat
from app.agents.nodes.collect import collect, compose, route_after_collect
from app.agents.nodes.handoff import handoff
from app.agents.nodes.supervisor import supervisor
from app.agents.nodes.understand import understand
from app.agents.state import AgentState

_graph = None


def get_graph():
    global _graph
    if _graph is None:
        g = StateGraph(AgentState)
        g.add_node("understand", understand)
        g.add_node("supervisor", supervisor)
        g.add_node("presale", make_agent_node("presale"))
        g.add_node("insale", make_agent_node("insale"))
        g.add_node("aftersale", make_agent_node("aftersale"))
        g.add_node("chitchat", chitchat)
        g.add_node("handoff", handoff)
        g.add_node("collect", collect)
        g.add_node("compose", compose)

        g.add_edge(START, "understand")
        g.add_edge("understand", "supervisor")
        g.add_conditional_edges(
            "supervisor",
            lambda s: s["active_agent"],
            {
                "presale": "presale",
                "insale": "insale",
                "aftersale": "aftersale",
                "chitchat": "chitchat",
                "handoff": "handoff",
            },
        )
        for n in ("presale", "insale", "aftersale", "chitchat", "handoff"):
            g.add_edge(n, "collect")
        g.add_conditional_edges(
            "collect", route_after_collect,
            {"loop": "supervisor", "done": "compose"},
        )
        g.add_edge("compose", END)
        _graph = g.compile()
    return _graph
