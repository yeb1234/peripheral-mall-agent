"""下单与订单查询接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import settings
from app.core import auth
from app.core.errors import AppError, ErrorCode
from app.db.session import get_db
from app.schemas import CreateOrderRequest
from app.services import memory_service as mem
from app.services import order_service

router = APIRouter(prefix="/api/orders", tags=["orders"])


def _resolve_identity(buyer_id: str, token: str | None) -> str:
    """有签名就以签名为准；没有签名时由 REQUIRE_BUYER_TOKEN 决定是否放行。

    订单接口直连数据库，比对话接口更值得收紧——伪造 buyer_id 就能翻别人订单。
    """
    if token:
        verified = auth.verify_buyer(token)
        if not verified:
            raise AppError(ErrorCode.SVC_TOKEN, http_status=401)
        return verified
    if settings.REQUIRE_BUYER_TOKEN:
        raise AppError(ErrorCode.SVC_TOKEN, http_status=401)
    return buyer_id


@router.post("")
async def create_order(req: CreateOrderRequest, db=Depends(get_db)):
    if not req.lines:
        raise HTTPException(status_code=400, detail="购物车为空")
    buyer_id = _resolve_identity(req.buyer_id, req.token)
    out = await order_service.create_order(
        db, buyer_id=buyer_id, lines=[l.model_dump() for l in req.lines],
        address=req.address.model_dump(),
    )
    # 业务写入同步进用户行为流：客服随后就能引用「你刚下的这一单」
    try:
        skus = [i.get("sku", "") for i in out.get("items", [])][:3]
        await mem.ensure_profile(db, buyer_id)
        await mem.record_event(
            db, buyer_id, "order_created", sku=skus[0] if skus else "",
            summary=f"下单 ¥{out.get('total', 0):.0f} · {len(req.lines)} 件商品",
            payload={"order_no_tail": out.get("order_no_tail"), "skus": skus},
        )
        await db.commit()
    except Exception:  # noqa: BLE001 — 记忆是增强，不能让下单失败
        await db.rollback()
    return out


@router.get("/mine")
async def my_orders(
    buyer_id: str = Query("buyer_demo"),
    token: str | None = Query(None),
    db=Depends(get_db),
):
    return await order_service.list_orders(db, _resolve_identity(buyer_id, token))
