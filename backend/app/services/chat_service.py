"""
客服会话服务：把一次用户消息跑完整个多 Agent 图，并落库消息。

对外三个入口：
- chat_turn：主对话（含身份闸自动绑定、注入剥离、图编排、消息持久化）
- verify_session：口令核验（手机后四位）绑定订单
- confirm_action：用户点击确认卡后的真正执行（带会话串行锁 + 幂等）
"""
from __future__ import annotations

import json
import uuid

from sqlalchemy import select

from app.agents.graph import get_graph
from app.agents.runtime import Runtime, reset_runtime, set_runtime
from app.agents.state import AgentState
from app.agents.tools.base import ToolContext
from app.agents.tools.executor import execute_confirmed
from app.config import settings
from app.core import auth, query_dedup, rate_limit
from app.core.audit import audit
from app.core.errors import AppError, ErrorCode, fallback_response
from app.core.security import guard_output, strip_injection
from app.core.session_manager import (
    auto_bind_orders,
    get_or_create_session,
    verify_and_bind,
)
from app.db.models import Message, Session
from app.services import memory_service as mem


async def _get_session(db, session_id: str | None) -> Session:
    return await get_or_create_session(db, session_id, "anonymous", "web")


def _public_nlu(result: dict) -> dict:
    """给 SSE meta / 前端展示 / 审计用的理解层摘要（不含内部提示词）。"""
    n = result.get("nlu") or {}
    return {
        "rewritten": n.get("rewritten", ""),
        "intent": n.get("intent"),
        "confidence": n.get("confidence"),
        "source": result.get("nlu_source"),
        "slots": n.get("slots") or {},
        "sub_intents": n.get("sub_intents") or [],
        "needs_clarify": bool(n.get("needs_clarify")),
        "reason": n.get("reason", ""),
        "queue_len": len(n.get("queue") or []),
        "segments": len(result.get("response_segments") or []),
        "deferred": result.get("deferred_intents") or [],
        "degraded_error": n.get("degraded_error"),
    }


async def chat_turn(db, *, session_id: str | None, buyer_id: str, channel: str, message: str,
                    client_ip: str = "", identity_mode: str = "unsigned") -> dict:
    # ---- 入口限流：身份 + IP 双维度，防止换 buyer_id 绕过单身份配额 ----
    checks: list[tuple[str, str, int, int]] = []
    if client_ip:
        checks.append(("chat_ip", client_ip, settings.IP_RATE_LIMIT, settings.CHAT_RATE_WINDOW))
    if buyer_id and buyer_id != "anonymous":
        checks.append(("chat", buyer_id, settings.CHAT_RATE_LIMIT, settings.CHAT_RATE_WINDOW))
    rl = await rate_limit.check_many(checks)
    if not rl["allowed"]:
        await audit(db, session_id=session_id, event_type="rate_limited",
                    input_data={"buyer_id": buyer_id, "ip": client_ip, "count": rl["count"]},
                    decision=f"对话入口限流：{rl['count']}/{rl['limit']} per {rl['window']}s")
        raise AppError(
            ErrorCode.SVC_RATE_LIMIT,
            f"请求过于频繁（{rl['limit']} 次 / {rl['window']} 秒），请 {rl['retry_after']} 秒后再试。",
            http_status=429,
        )

    clean, injected = strip_injection(message)
    sess = await get_or_create_session(db, session_id, buyer_id or "anonymous", channel)

    # ---- 会话归属校验：同一 session 不能被换一个 buyer_id 接管 ----
    if (sess.buyer_id and sess.buyer_id != "anonymous" and buyer_id
            and buyer_id != "anonymous" and sess.buyer_id != buyer_id):
        await audit(db, session_id=sess.id, event_type="identity_mismatch",
                    input_data={"session_buyer": sess.buyer_id, "request_buyer": buyer_id,
                                "ip": client_ip},
                    decision="会话身份与请求身份不一致，拒绝")
        raise AppError(ErrorCode.SVC_IDENTITY, http_status=403)
    if sess.buyer_id == "anonymous" and buyer_id and buyer_id != "anonymous":
        sess.buyer_id = buyer_id  # 匿名会话首次登录：绑定身份，不换会话

    # ---- 语义去重：同会话窗口内的重复/近义问句直接复用上次只读回复 ----
    dedup_hit = await query_dedup.lookup(sess.id, message)
    if dedup_hit:
        await audit(db, session_id=sess.id, event_type="dedup_hit",
                    input_data={"message": message[:120], "sim": dedup_hit["sim"]},
                    decision="命中语义去重，复用上一轮回复，未消耗模型调用")
        meta = dict(dedup_hit.get("meta") or {})
        return {
            "session_id": sess.id,
            "response": dedup_hit["reply"],
            "intent": meta.get("intent"),
            "confirmation": meta.get("confirmation"),
            "need_bind": meta.get("need_bind", False),
            "handoff": meta.get("handoff", False),
            "error_code": meta.get("error_code"),
            "task_id": meta.get("task_id"),
            "task_resumed": False,
            "nlu": meta.get("nlu") or {},
            "agent_trail": meta.get("agent_trail") or [],
            "deduped": True,
            "dedup_sim": dedup_hit["sim"],
            "identity_mode": identity_mode,
        }

    # 已知买家自动绑定近 90 天订单（最优路径，用户不用说单号）
    if buyer_id and buyer_id != "anonymous":
        await auto_bind_orders(db, sess)

    # ---- 记忆层（读）：在任何 LLM 调用之前完成，且写事务立即提交 ----
    # 首次访问懒创建用户画像；之后 snapshot/build_history 都是纯读，
    # 避免长事务跨越图执行造成 SQLite 锁等待。
    await mem.ensure_profile(db, sess.buyer_id)
    await db.commit()
    snap = await mem.snapshot(db, sess.buyer_id, session_id=sess.id)
    history = await mem.build_history(db, sess.buyer_id, limit=6, session_id=sess.id)

    state: AgentState = {
        "session_id": sess.id, "buyer_id": sess.buyer_id, "channel": channel,
        "user_input": clean, "injection_detected": injected,
        "messages": history, "observations": [], "confirmation": None,
        "final_response": "", "handoff": False, "need_bind": False, "error": None,
        "error_code": None,
        "memory_context": snap["context"],
    }
    rt = Runtime(db=db, ctx=ToolContext(db=db, session_id=sess.id, buyer_id=sess.buyer_id,
                                        channel=channel, user_text=clean))
    token = set_runtime(rt)

    # 顶层守卫：整张图任何异常都降级为带错误码的兜底回复，绝不空白/卡死
    error_code: str | None = None
    try:
        result = await get_graph().ainvoke(state)
    except AppError as e:
        error_code = e.code.value
        await audit(db, session_id=sess.id, event_type="system_error",
                    input_data={"message": clean}, decision=f"顶层兜底：{e.code.value} {e.message}")
        result = {
            "intent": "handoff", "confirmation": None, "need_bind": False,
            "handoff": True, "final_response": fallback_response(e), "error_code": e.code.value,
        }
    except Exception as e:  # noqa: BLE001  — 任何未预期异常都不应冲出到 SSE
        error_code = ErrorCode.SYS_UNKNOWN.value
        await audit(db, session_id=sess.id, event_type="system_error",
                    input_data={"message": clean}, decision=f"顶层兜底(未预期)：{type(e).__name__}: {e}")
        result = {
            "intent": "handoff", "confirmation": None, "need_bind": False,
            "handoff": True, "final_response": fallback_response(AppError(ErrorCode.SYS_UNKNOWN, detail=str(e))),
            "error_code": ErrorCode.SYS_UNKNOWN.value,
        }
    finally:
        reset_runtime(token)

    sid, bid = sess.id, sess.buyer_id
    # 输出侧后置扫描：PII 打码 + 系统提示/内部口径拦截 + 价格承诺清洗
    result["final_response"] = guard_output(result.get("final_response") or "")
    db.add(Message(session_id=sid, role="user", content=clean,
                   meta=json.dumps({"injected": injected}, ensure_ascii=False)))
    db.add(Message(session_id=sid, role="assistant", content=result["final_response"],
                   meta=json.dumps({
                       "intent": result.get("intent"),
                       "confirmation": result.get("confirmation"),
                       "handoff": result.get("handoff"),
                       "error_code": result.get("error_code"),
                       "nlu": _public_nlu(result),
                   }, ensure_ascii=False)))
    await db.commit()

    # ---- 记忆层（写）：独立于主回复事务，失败只记日志，绝不拖挂客服 ----
    task_id: str | None = None
    task_resumed = False
    try:
        # 多意图串行派发后 result.intent 会是「最后一段」的意图，
        # 任务归属必须取第一层识别出的主意图，否则记忆里的阶段会串。
        nlu = result.get("nlu") or {}
        intent = nlu.get("intent") or result.get("intent") or "chitchat"
        task, task_resumed = await mem.upsert_task(db, bid, sid, intent, clean)
        task_id = task.id
        conf = result.get("confirmation")
        deferred = result.get("deferred_intents") or []
        if conf or deferred:
            # 二次确认卡 / 被阻塞的剩余诉求 = 任务断点：跨会话回来仍能从这里续点
            await mem.pause_task(db, task.id, {
                "confirm_id": conf.get("confirm_id") if conf else None,
                "preview": conf.get("preview", "") if conf else "",
                "stage": intent,
                "message": clean[:160],
                "nlu_rewritten": nlu.get("rewritten", ""),
                "deferred_intents": deferred,
            })
        await mem.apply_prefs(db, bid, clean)
        focus = mem.focus_sku(clean) or mem.focus_sku(nlu.get("rewritten", ""))
        await mem.record_event(
            db, bid, "chat", sku=focus, summary=clean[:120],
            payload={"intent": intent, "task_id": task.id, "resumed": task_resumed,
                     "error_code": result.get("error_code"),
                     "nlu_source": result.get("nlu_source"),
                     "sub_intents": nlu.get("sub_intents") or [],
                     "segments": len(result.get("response_segments") or []),
                     "deferred": len(deferred)},
            session_id=sid, task_id=task.id,
        )
        await db.commit()
    except Exception as e:  # noqa: BLE001
        await db.rollback()
        import logging
        logging.getLogger("memory").warning("记忆写入失败（不影响回复）：%s", e)

    payload = {
        "session_id": sid,
        "response": result["final_response"],
        "intent": result.get("intent"),
        "confirmation": result.get("confirmation"),
        "need_bind": result.get("need_bind", False),
        "handoff": result.get("handoff", False),
        "error_code": result.get("error_code") or error_code,
        "task_id": task_id,
        "task_resumed": task_resumed,
        "nlu": _public_nlu(result),
        # 跨 Agent 交接轨迹（售前→售中→售后），前端「语义理解/工作台」与演示可读
        "agent_trail": result.get("agent_trail") or [],
        "deduped": False,
        "identity_mode": identity_mode,
    }
    # 只缓存「纯读」回复；带确认卡/转人工/报错的回复不缓存（避免复用过期卡）
    await query_dedup.remember(sid, message, payload["response"], {
        "intent": payload["intent"], "confirmation": payload["confirmation"],
        "need_bind": payload["need_bind"], "handoff": payload["handoff"],
        "error_code": payload["error_code"], "task_id": payload["task_id"],
        "nlu": payload["nlu"], "agent_trail": payload["agent_trail"],
    })
    return payload


async def verify_session(db, session_id: str, phone_last4: str) -> dict:
    sess = (await db.execute(select(Session).where(Session.id == session_id))).scalar_one_or_none()
    if not sess:
        return {"ok": False, "reason": "会话不存在"}
    res = await verify_and_bind(db, sess, phone_last4)
    await db.commit()
    return res


async def confirm_action(db, session_id: str, confirm_id: str, approve: bool) -> dict:
    sess = (await db.execute(select(Session).where(Session.id == session_id))).scalar_one_or_none()
    if not sess:
        return {"ok": False, "observation": "会话不存在"}
    sid, bid = sess.id, sess.buyer_id
    ctx = ToolContext(db=db, session_id=sid, buyer_id=bid, channel=sess.channel)
    rt = Runtime(db=db, ctx=ctx)
    token = set_runtime(rt)
    try:
        out = await execute_confirmed(confirm_id, approve, ctx)
        await db.commit()
    except Exception as e:  # noqa: BLE001  — 确认执行异常：回滚并安全返回，不 500
        await db.rollback()
        await audit(
            db, session_id=session_id, event_type="write_error", order_id=None,
            input_data={"confirm_id": confirm_id, "approve": approve},
            decision=f"异常回滚：{type(e).__name__}: {e}",
        )
        out = {"ok": False, "observation": "操作执行时出现系统异常，已回滚，请稍后重试或转人工。"}
    finally:
        reset_runtime(token)

    db.add(Message(session_id=sid, role="assistant", content=out.get("observation", ""),
                   meta=json.dumps({"confirm_id": confirm_id, "approve": approve}, ensure_ascii=False)))
    await db.commit()

    # 确认执行完毕 = 任务收口；记录结果，续点列表不再出现它
    try:
        t = await mem.find_task_by_confirm(db, bid, confirm_id)
        if t:
            await mem.finish_task(db, t.id, "用户确认执行" if approve else "用户取消")
            await mem.record_event(
                db, bid, "confirm_executed", sku=t.focus_sku, summary=t.title,
                payload={"confirm_id": confirm_id, "approve": approve, "ok": out.get("ok")},
                session_id=sid, task_id=t.id,
            )
            await db.commit()
    except Exception:  # noqa: BLE001
        await db.rollback()
    return out
