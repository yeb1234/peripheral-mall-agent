"""买家身份接口：用手机后四位（或 dev 演示白名单）换签名 token。

演示场景：前端首次加载调 `POST /api/auth/buyer {buyer_id:"buyer_demo"}`，
dev 模式直接签发；生产环境（ENV=prod）必须带 phone_last4，与该买家最近订单的收货手机哈希比对。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.config import settings
from app.core import auth
from app.core.errors import AppError, ErrorCode
from app.core.security import hash_phone
from app.db.models import Order
from app.db.session import get_db
from app.schemas import AuthRequest

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/buyer")
async def issue_buyer_token(req: AuthRequest, db=Depends(get_db)):
    buyer_id = req.buyer_id or "buyer_demo"

    # dev：允许演示身份直接签发，方便本地/面试演示不卡登录
    if settings.ENV != "prod" and not req.phone_last4 and auth.demo_buyer_allowed(buyer_id):
        token = auth.sign_buyer(buyer_id)
        return {"ok": True, "buyer_id": buyer_id, "token": token,
                "expires_in": settings.BUYER_TOKEN_TTL, "mode": "demo"}

    if not req.phone_last4:
        raise AppError(ErrorCode.SVC_TOKEN, "需要手机号后四位才能签发身份凭证。", http_status=401)

    # 用该买家最近一笔订单的收货手机哈希核验（订单是唯一可信来源，画像里不存明文）
    order = (await db.execute(
        select(Order).where(Order.buyer_id == buyer_id).order_by(Order.created_at.desc()).limit(1)
    )).scalar_one_or_none()
    if not order or not order.phone_hash:
        raise AppError(ErrorCode.SVC_TOKEN, "该身份没有可核验的订单，无法签发。", http_status=401)

    # 演示订单手机号固定 13800001234，这里只用于拼候选；真实接入应查用户中心
    if order.phone_hash != hash_phone("1380000" + req.phone_last4):
        raise AppError(ErrorCode.SVC_TOKEN, "手机号后四位不匹配。", http_status=401)

    token = auth.sign_buyer(buyer_id)
    return {"ok": True, "buyer_id": buyer_id, "token": token,
            "expires_in": settings.BUYER_TOKEN_TTL, "mode": "verified"}


@router.post("/verify-token")
async def verify_token(payload: dict):
    """调试用：校验 token 并回显 buyer_id（不返回任何用户数据）。"""
    buyer_id = auth.verify_buyer(payload.get("token"))
    return {"ok": bool(buyer_id), "buyer_id": buyer_id}
