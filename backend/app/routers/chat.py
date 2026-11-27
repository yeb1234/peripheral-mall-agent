"""客服对话接口：SSE 流式输出 + 身份核验 + 二次确认执行。

关于重试：模型调用本身是非流式的（chat.completions.create 不带 stream），
整轮 Agent 图跑完、拿到完整回复后才逐字推 SSE token。因此所有 LLM 重试
都发生在「响应体返回之前」，不存在流式输出已开始还自动重试造成重复内容的情况。
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse

from app.config import settings
from app.core import auth
from app.core.errors import AppError, ErrorCode
from app.db.session import get_db
from app.schemas import ChatRequest, ConfirmRequest, VerifyRequest
from app.services import chat_service

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _emit(event: str, data: dict | str) -> dict:
    """构造一条 SSE 事件（EventSourceResponse 接收的 dict）。"""
    return {"event": event, "data": data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)}


@router.post("")
async def chat_stream(req: ChatRequest, request: Request, db=Depends(get_db)):
    """SSE：先按字符流式推送回复（演示流式输出），最后推 meta（意图/确认卡/转人工/错误码）。

    错误代码控制：任何异常都被捕获并转换为带错误码的 `error` 事件，
    且无论成功失败都发送 `done`，前端据此解除 loading，绝不卡在空气泡。
    """

    async def event_gen() -> AsyncGenerator[dict, None]:
        # ---- 身份：签名 token 优先；没有 token 时按环境策略决定放行还是拒绝 ----
        buyer_id = req.buyer_id
        identity_mode = "unsigned"
        if req.token:
            verified = auth.verify_buyer(req.token)
            if not verified:
                yield _emit("error", AppError(ErrorCode.SVC_TOKEN, http_status=401).to_dict())
                yield _emit("done", "{}")
                return
            buyer_id, identity_mode = verified, "token"
        elif settings.REQUIRE_BUYER_TOKEN:
            yield _emit("error", AppError(ErrorCode.SVC_TOKEN, http_status=401).to_dict())
            yield _emit("done", "{}")
            return

        # 限流控制系统：若模型队列已有请求在排队，先告知前端，避免用户以为卡死
        from app.core.llm_rate_limit import get_llm_limiter

        queued = get_llm_limiter().waiting_count()
        if queued > 0:
            yield _emit("queue", {"position": queued})

        try:
            result = await chat_service.chat_turn(
                db, session_id=req.session_id, buyer_id=buyer_id,
                channel=req.channel, message=req.message,
                client_ip=(request.client.host if request.client else ""),
                identity_mode=identity_mode,
            )
        except AppError as e:  # 服务端已尽力兜底，这里仍是最后一道防线
            yield _emit("error", e.to_dict())
            yield _emit("done", "{}")
            return
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger("chat").exception("chat_stream unhandled")
            yield _emit("error", {"code": "SYS000", "message": f"服务异常：{type(e).__name__}", "detail": f"{type(e).__name__}: {e}"})
            yield _emit("done", "{}")
            return

        text = result["response"] or ""
        if not text:  # 兜底：防止出现空气泡
            text = f"[{result.get('error_code') or 'SYS000'}] 抱歉，本次未能生成回复，请重试。"

        for ch in text:
            yield _emit("token", ch)
            await asyncio.sleep(0.006)

        meta = {
            "session_id": result["session_id"], "intent": result["intent"],
            "confirmation": result["confirmation"], "need_bind": result["need_bind"],
            "handoff": result["handoff"], "error_code": result.get("error_code"),
            "task_id": result.get("task_id"), "task_resumed": result.get("task_resumed", False),
            # 第一层理解结果：前端可展示「系统把这句话理解成了什么、派给了谁」
            "nlu": result.get("nlu") or {},
            # 跨 Agent 交接轨迹：售前→售中→售后 的移交记录，演示时可直观看到
            "agent_trail": result.get("agent_trail") or [],
            # 语义去重 / 身份来源：演示时可看到「这轮没花模型调用」
            "deduped": bool(result.get("deduped")),
            "dedup_sim": result.get("dedup_sim"),
            "identity_mode": result.get("identity_mode") or "unsigned",
        }
        yield _emit("meta", meta)
        yield _emit("done", "{}")

    return EventSourceResponse(event_gen())


@router.post("/verify")
async def verify(req: VerifyRequest, db=Depends(get_db)):
    return await chat_service.verify_session(db, req.session_id, req.phone_last4)


@router.post("/confirm")
async def confirm(req: ConfirmRequest, db=Depends(get_db)):
    return await chat_service.confirm_action(db, req.session_id, req.confirm_id, req.approve)
