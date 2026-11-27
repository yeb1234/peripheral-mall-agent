"""
第一层理解节点（understand）：图里的第一个业务节点。

它把「用户原话」翻译成「下游编排器能直接消费的结构化任务」：
  改写后的查询 / 主意图 / 置信度 / 槽位 / 多意图队列 / 澄清标记。

为什么要放在图里而不是做成 FastAPI 中间件：
- 结果随 state 传给每个子 Agent，可观测（SSE meta）、可审计、可回放；
- 失败可以在节点内闭环降级，不把异常抛到路由层；
- 多意图队列天然可以由 LangGraph 的条件边循环派发。
"""
from __future__ import annotations

import logging

from app.agents.runtime import get_runtime
from app.agents.state import AgentState
from app.core.audit import audit
from app.services import nlu_service

logger = logging.getLogger(__name__)


async def understand(state: AgentState) -> AgentState:
    rt = get_runtime()
    raw = state.get("user_input", "") or ""

    res = await nlu_service.analyze_input(
        rt.ctx.db,
        raw,
        buyer_id=state.get("buyer_id"),
        session_id=state.get("session_id"),
        context=state.get("memory_context"),
        history=state.get("messages"),
        injection_detected=state.get("injection_detected"),
    )

    nlu = dict(res["nlu"])
    if res.get("error_code"):
        # 理解层降级只记在结构里，不写 state.error_code —— 本轮客服仍能正常回答
        nlu["degraded_error"] = res["error_code"]

    # 安全收敛已下沉到 nlu_service（调试接口与客服链路共用同一结论），
    # 节点这里只补一条审计留痕，不再重复改状态。
    if res.get("guard") == "injection":
        try:
            await audit(
                rt.ctx.db, session_id=state.get("session_id", ""),
                event_type="nlu_injection",
                input_data={"input": raw},
                decision="越权/注入指令命中，理解层强制 handoff，未派发任何工具",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("注入审计写入失败（已忽略）：%s", e)

    nlu["queue"] = res["queue"]
    state["nlu"] = nlu
    state["nlu_source"] = res["source"]
    state["intent_queue"] = res["queue"]
    if res["queue"]:
        state["current_goal"] = res["queue"][0].get("rewritten") or raw
    else:
        state["current_goal"] = raw
    state["response_segments"] = []
    state["deferred_intents"] = []

    try:
        await audit(
            rt.ctx.db,
            session_id=state.get("session_id", ""),
            event_type="nlu_understand",
            input_data={
                "input": raw,
                "rewritten": nlu.get("rewritten"),
                "intent": nlu.get("intent"),
                "confidence": nlu.get("confidence"),
                "slots": nlu.get("slots"),
                "source": res["source"],
                "queue_len": len(res["queue"]),
            },
            decision=f"第一层理解：{res['source']}，拆出 {len(res['queue'])} 个待派发意图",
        )
    except Exception as e:  # noqa: BLE001 — 审计失败绝不影响客服链路
        logger.warning("NLU 审计写入失败（已忽略）：%s", e)

    return state
