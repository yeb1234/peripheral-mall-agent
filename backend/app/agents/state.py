"""
LangGraph 状态定义。

状态是「图在节点间传递的黑盒」。我们把业务相关字段都放进状态，
节点是无副作用的纯函数（除了调用工具），便于你单独读某个节点理解逻辑。

分层（面试常问「LangGraph 到底管什么」）：
- LangGraph 管：状态在节点间的流转、条件路由、多轮派发的循环边界。
- 不管：语义理解（NLU 节点内的 LLM）、长期记忆（DB 里的 UserProfile/Event/Task）、
  工具可靠性（executor 六道关）、模型限流（独立 limiter）。
"""
from __future__ import annotations

from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    # ---- 输入与身份 ----
    session_id: str
    buyer_id: str
    channel: str
    user_input: str                  # 用户原话（已过注入剥离）
    injection_detected: bool         # 入模前是否命中越权/注入指令（安全可观测）

    # ---- 第一层语义理解（understand 节点产出）----
    nlu: dict                        # 改写/意图/置信度/槽位/子意图/澄清标记
    nlu_source: str                  # llm | rule | fallback
    intent_queue: list[dict]         # 待派发的意图队列，主意图排在首位
    current_goal: str                # 当前这一派发段的规范化目标（喂给子 Agent）
    response_segments: list[str]     # 多意图分段回复
    deferred_intents: list[dict]     # 因确认卡/身份闸延后、需写进任务断点的意图

    # ---- 路由 ----
    intent: str                      # presale | insale | aftersale | chitchat | handoff
    intent_source: str               # nlu_llm | nlu_rule | nlu_fallback | rule | llm | fallback
    active_agent: str                # 当前被路由到的子 Agent
    agent_trail: list[dict]          # 跨 Agent 交接轨迹：[{seq,from,to,goal}]，用于可观测与演示

    # ---- 执行与输出 ----
    messages: Annotated[list, add_messages]   # 给 LLM 的对话历史（跨会话记忆注入）
    observations: list[str]          # 工具查证结果
    confirmation: dict | None        # 待用户确认的二次确认卡
    final_response: str              # 本轮最终回复
    handoff: bool                    # 是否转人工
    need_bind: bool                  # 是否需要身份核验
    error: str | None
    error_code: str | None           # 系统错误码（错误代码控制）
    memory_context: str              # 长期记忆 + 网站行为 + 进行中任务（跨会话注入）
    task_id: str | None              # 本轮所属任务（多任务区分 / 续点）


def effective_text(state: AgentState) -> str:
    """下游节点统一取「理解后的文本」。

    优先用当前派发段的规范化改写，其次主改写，最后才是用户原话。
    这样第一层的指代消解 / 口语规范化能真正作用到工具选择与检索，
    而不是只停留在展示层。
    """
    return (
        state.get("current_goal")
        or (state.get("nlu") or {}).get("rewritten")
        or state.get("user_input")
        or ""
    )
