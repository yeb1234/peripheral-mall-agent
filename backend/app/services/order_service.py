"""订单服务：下单（异步高性能）、查询。

下单路径设计：
- 入参是购物车子项（sku + qty）+ 结构化收货地址；
- 服务端重算金额（不采信前端给的总价），防篡改；
- 手机号只存哈希（PII 最小化）；
- 一次事务内写订单 + 明细，返回脱敏后的订单快照。
"""
from __future__ import annotations

import datetime as dt
import json
import uuid

from sqlalchemy import select

from app.core.security import hash_phone
from app.db.models import Order, OrderItem, OrderStatus, Product


def _order_no() -> str:
    return "P4" + dt.datetime.utcnow().strftime("%Y%m%d") + uuid.uuid4().hex[:6].upper()


async def create_order(db, *, buyer_id: str, lines: list[dict], address: dict) -> dict:
    total = 0.0
    order = Order(
        order_no=_order_no(),
        buyer_id=buyer_id,
        phone_hash=hash_phone(address.get("phone", "")),
        status=OrderStatus.PAID,  # 演示：下单即视为已支付，便于直接体验改/退
        province=address.get("province", ""),
        city=address.get("city", ""),
        district=address.get("district", ""),
        detail=address.get("detail", ""),
        contact=address.get("contact", ""),
        paid_at=dt.datetime.utcnow(),
    )
    db.add(order)
    await db.flush()

    for line in lines:
        prod = await db.get(Product, line.get("product_id")) if line.get("product_id") else None
        if not prod:
            prod = (await db.execute(
                select(Product).where(Product.sku == line.get("sku"))
            )).scalar_one_or_none()
        if not prod:
            continue
        qty = max(1, int(line.get("qty", 1)))
        item = OrderItem(
            order_id=order.id, product_id=prod.id, sku=prod.sku, name=prod.name,
            qty=qty, unit_price=prod.price, spec_snapshot=prod.specs,
        )
        db.add(item)
        total += prod.price * qty
    order.total = round(total, 2)
    await db.commit()
    await db.refresh(order, attribute_names=["items"])
    return order.to_public_dict()


async def list_orders(db, buyer_id: str) -> list[dict]:
    from sqlalchemy.orm import selectinload
    stmt = (
        select(Order)
        .where(Order.buyer_id == buyer_id)
        .order_by(Order.created_at.desc())
        .options(selectinload(Order.items))
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [o.to_public_dict() for o in rows]


async def get_order(db, order_id: str, buyer_id: str) -> dict | None:
    o = await db.get(Order, order_id)
    if not o or o.buyer_id != buyer_id:
        return None
    await db.refresh(o, attribute_names=["items"])
    return o.to_public_dict()
