"""第一层语义理解（NLU）调试接口。

与 /api/chat 的区别：这里只跑 understand 那一层，不派发工具、不写订单、
不生成客服回复，方便面试现场逐句演示「系统把用户这句话理解成了什么」。

复用 services/nlu_service.analyze_input —— 调试所见即线上所跑，
避免出现「实验台效果很好、客服链路却是另一套逻辑」的假演示。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.config import settings
from app.core import auth
from app.core.errors import AppError, ErrorCode
from app.core.llm_registry import get_registry
from app.db.session import get_db
from app.services import nlu_service

router = APIRouter(prefix="/api/nlu", tags=["nlu"])


class AnalyzeRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=500)
    buyer_id: str = "buyer_demo"
    session_id: str | None = None
    force: bool = False       # 强制走 LLM（auto 模式下用来演示真实改写）
    token: str | None = Field(default=None, max_length=512)


class ModeRequest(BaseModel):
    mode: str = Field(..., pattern="^(auto|always|off)$")


def _config() -> dict:
    return {
        "mode": settings.NLU_MODE,
        "max_sub_intents": settings.NLU_MAX_SUB_INTENTS,
        "provider": get_registry().active,
        "modes": {
            "auto": "直白业务句走规则短路，只有指代/多意图/模糊表述才调 LLM",
            "always": "每轮都由 LLM 做改写与意图识别（演示用，成本更高）",
            "off": "纯规则，断网/额度耗尽也能跑完整条 Agent 链路",
        },
    }


@router.get("/config")
async def get_config():
    return {"ok": True, **_config()}


@router.post("/config")
async def set_config(req: ModeRequest):
    """进程内临时切换模式（重启回 .env 默认值），只影响本后端进程。"""
    object.__setattr__(settings, "NLU_MODE", req.mode)
    return {"ok": True, **_config()}


@router.post("/analyze")
async def analyze(req: AnalyzeRequest, db=Depends(get_db)):
    buyer_id = req.buyer_id
    if req.token:
        verified = auth.verify_buyer(req.token)
        if not verified:
            raise AppError(ErrorCode.SVC_TOKEN, http_status=401)
        buyer_id = verified
    out = await nlu_service.analyze_input(
        db, req.text,
        buyer_id=buyer_id,
        session_id=req.session_id,
        force=req.force,
    )
    await db.commit()
    # 附上「这句话会被派给哪些 Agent」，前端可直接画派发序列
    out["agents"] = [
        {
            "order": i + 1,
            "intent": q.get("intent"),
            "agent": nlu_service.AGENT_LABEL.get(q.get("intent", ""), q.get("intent")),
            "goal": q.get("goal"),
            "primary": bool(q.get("primary")),
        }
        for i, q in enumerate(out.get("queue") or [])
    ]
    out["config"] = _config()
    return out
