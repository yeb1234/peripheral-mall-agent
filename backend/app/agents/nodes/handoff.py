"""转人工节点：带上下文摘要，人工无需重问。"""
from __future__ import annotations

from app.agents.runtime import get_runtime
from app.agents.state import AgentState
from app.core.audit import audit


async def handoff(state: AgentState) -> AgentState:
    rt = get_runtime()
    ctx = state.get("memory_context") or ""
    nlu = state.get("nlu") or {}
    # 人工侧同步的是「用户记忆 + 第一层理解结果」，不只是本轮那句话；
    # 这样坐席能直接看到系统识别的意图、改写后的诉求以及被确认卡挡住的后续诉求。
    await audit(rt.db, session_id=state["session_id"], event_type="handoff", actor="ai",
                input_data={
                    "text": state.get("user_input", ""),
                    "rewritten": nlu.get("rewritten", ""),
                    "intent": state.get("intent"),
                    "nlu_source": state.get("nlu_source"),
                    "deferred_intents": state.get("deferred_intents") or [],
                    "memory": ctx[:600],
                },
                decision="转人工；已同步用户记忆、第一层语义理解与近期订单")
    state["handoff"] = True
    pending = len(state.get("deferred_intents") or [])
    extra = f"另有 {pending} 项已识别的诉求也已一并同步。" if pending else ""
    state["final_response"] = (
        "已为您转接人工客服。已同步您的偏好习惯、近期订单与本次诉求"
        "（含系统识别的意图与改写后的表达），无需重复描述。" + extra
        + "人工客服将尽快接入处理。"
    )
    return state
