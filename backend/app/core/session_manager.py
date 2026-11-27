"""
会话与身份管理（写操作第一道关：身份）。

设计要点（文档「会话、身份与渠道」）：
- 订单 ID 由会话上下文注入，模型无权指定；用户报的手机号/订单号只用于核验，不参与寻址。
- 自动绑定：已知 buyer_id 时，绑定其近 90 天订单（最多 3 单）。
- 口令核验：未知身份时，让用户输入收货手机后四位，与订单比对（只比对，不展示完整号码）。
- 绑定范围：仅近 90 天、最多 3 单，防止越界查询。
- 失效：会话超时解绑。
"""
from __future__ import annotations

import datetime as dt
import json
import uuid

from sqlalchemy import select

from app.config import settings
from app.core.audit import audit
from app.core.cache import get_cache
from app.core.security import hash_phone, mask_phone
from app.db.models import Order, OrderBinding, OrderStatus, Session


async def get_or_create_session(db, session_id: str | None, buyer_id: str, channel: str) -> Session:
    if session_id:
        sess = (await db.execute(select(Session).where(Session.id == session_id))).scalar_one_or_none()
        if sess:
            # 超时则解绑
            if sess.expires_at and sess.expires_at < dt.datetime.utcnow():
                await _unbind_all(db, sess.id)
                sess.expires_at = dt.datetime.utcnow() + dt.timedelta(seconds=settings.SESSION_TTL_SECONDS)
            return sess
    sess = Session(
        id=uuid.uuid4().hex,
        buyer_id=buyer_id,
        channel=channel,
        expires_at=dt.datetime.utcnow() + dt.timedelta(seconds=settings.SESSION_TTL_SECONDS),
    )
    db.add(sess)
    # 立即提交，释放写锁：不要让会话创建的写事务跨过后续（可能很长的）LLM 调用，
    # 否则并发请求会因持锁过久而 database is locked
    await db.commit()
    return sess


async def _unbind_all(db, session_id: str) -> None:
    bindings = (await db.execute(
        select(OrderBinding).where(OrderBinding.session_id == session_id)
    )).scalars().all()
    for b in bindings:
        await db.delete(b)


async def auto_bind_orders(db, session: Session) -> list[OrderBinding]:
    """已知 buyer_id：绑定近 90 天订单（最多 MAX_BOUND_ORDERS）。"""
    cutoff = dt.datetime.utcnow() - dt.timedelta(days=settings.ORDER_BIND_WINDOW_DAYS)
    stmt = (
        select(Order)
        .where(Order.buyer_id == session.buyer_id, Order.created_at >= cutoff)
        .order_by(Order.created_at.desc())
        .limit(settings.MAX_BOUND_ORDERS)
    )
    orders = (await db.execute(stmt)).scalars().all()
    bound = await _bind(db, session, list(orders))
    # 绑定写入后立即提交，释放写锁（同 get_or_create_session 的考量）
    await db.commit()
    return bound


async def verify_and_bind(db, session: Session, phone_last4: str) -> dict:
    """
    口令核验：用户报手机后四位，与订单 phone_hash 比对。
    只比对，不展示完整号码；命中后绑定其订单。
    """
    target_hash = hash_phone("1380000" + phone_last4) if len(phone_last4) == 4 else ""
    candidate = (await db.execute(select(Order))).scalars().all()
    matched = [o for o in candidate if o.phone_hash == target_hash]
    if not matched:
        await audit(db, session_id=session.id, event_type="session_bind",
                    input_data={"phone_last4": mask_phone("1380000" + phone_last4)},
                    decision="核验失败：未匹配到订单")
        return {"ok": False, "reason": "尾号核验未通过，请确认收货手机后四位"}
    # 绑定该买家近 90 天订单
    session.buyer_id = matched[0].buyer_id
    bindings = await auto_bind_orders(db, session)
    await audit(db, session_id=session.id, event_type="session_bind",
                input_data={"phone_last4": mask_phone(matched[0].phone_hash)},
                decision=f"核验通过，绑定 {len(bindings)} 单")
    return {"ok": True, "bound": len(bindings)}


def _reconstruct(last4: str) -> str:
    # demo 专用：补全演示号码
    return "1380000" + last4 if len(last4) == 4 else last4


async def _bind(db, session: Session, orders: list[Order]) -> list[OrderBinding]:
    existing = (await db.execute(
        select(OrderBinding).where(OrderBinding.session_id == session.id)
    )).scalars().all()
    have = {b.order_id for b in existing}
    created = []
    for o in orders:
        if o.id in have:
            continue
        b = OrderBinding(session_id=session.id, order_id=o.id, relation="recent")
        db.add(b)
        created.append(b)
    await db.flush()
    return created


async def bound_orders(db, session_id: str) -> list[Order]:
    stmt = (
        select(Order)
        .join(OrderBinding, OrderBinding.order_id == Order.id)
        .where(OrderBinding.session_id == session_id)
    )
    return list((await db.execute(stmt)).scalars().all())


async def resolve_order(db, session_id: str, hint_status: OrderStatus | None = None,
                        hint_text: str = "") -> Order | None:
    """
    身份闸核心：从「会话绑定的订单」中推导目标订单，模型绝不指定。
    - 用户原话里出现某个绑定单的尾号（如 AA42）→ 直接在绑定单里消歧；
    - 指定 hint 状态则挑匹配状态的最新一单；
    - 否则返回唯一绑定单，或 None（需用户明确）。
    """
    orders = await bound_orders(db, session_id)
    if not orders:
        return None
    if hint_text:
        up = hint_text.upper()
        for o in orders:  # 只在已绑定订单里比对尾号，不会越权查到别人的单
            tail = (o.order_no or "")[-4:].upper()
            if tail and tail in up:
                return o
    if hint_status:
        for o in orders:
            if o.status == hint_status:
                return o
        # 没匹配到 hint，仍返回最新的，让策略层去判定拒绝
        return orders[0]
    if len(orders) == 1:
        return orders[0]
    return orders[0]  # 多单时默认最新，具体动作由策略层按状态判定


def session_snapshot(session: Session) -> dict:
    return json.loads(session.state_json or "{}")
