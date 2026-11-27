"""闲聊节点。"""
from __future__ import annotations

from app.agents.llm import get_llm
from app.agents.prompts import CHITCHAT
from app.agents.state import AgentState
from app.core.errors import AppError, fallback_response


async def chitchat(state: AgentState) -> AgentState:
    llm = get_llm()
    # 闲聊也要带着用户记忆，但不能因此放松「不得编造事实」的硬约束
    ctx = state.get("memory_context") or ""
    system = CHITCHAT
    if ctx:
        system += (
            "\n\n【用户记忆与网站联动上下文（系统记录）】\n" + ctx[:1600]
            + "\n可据此个性化寒暄；只有这里明确记录的事实才可引用，其余一律不编造。"
        )
    # 闲聊刻意用「用户原话」而不是改写文本：寒暄的语气、语气词本身就是内容，
    # 规范化成「问候客服」反而会让回复变味。这也说明改写不是无差别套用，
    # 而是按意图决定用在哪个环节（工具选择/检索用改写，闲聊用原话）。
    nlu = state.get("nlu") or {}
    if nlu.get("needs_clarify"):
        system += "\n\n第一层理解认为本轮诉求信息不足，可自然地询问具体型号或订单，但不要假设任何事实。"
    try:
        reply = await llm.generate_reply(system, [], state.get("user_input", ""))
    except AppError as e:
        state["error_code"] = e.code.value
        state["final_response"] = fallback_response(e)
        state["handoff"] = True
        return state
    state["final_response"] = reply
    return state
