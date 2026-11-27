"""
审计（写操作第六道关：审计与回滚）。

四类事件必留痕：会话建立与绑定、工具调用（含入参与判定）、写操作执行与撤销、转人工与人工接管。
审计表只追加不修改，按会话与订单双维度可查。
"""
from __future__ import annotations

import json
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Audit


async def audit(
    db: AsyncSession,
    *,
    session_id: str,
    event_type: str,
    order_id: str = "",
    actor: str = "ai",
    input_data: dict | None = None,
    output_data: dict | None = None,
    decision: str = "",
) -> None:
    db.add(Audit(
        id=uuid.uuid4().hex,
        session_id=session_id,
        order_id=order_id,
        event_type=event_type,
        actor=actor,
        input=json.dumps(input_data or {}, ensure_ascii=False),
        output=json.dumps(output_data or {}, ensure_ascii=False),
        decision=decision,
    ))
    # 审计不立即 commit，由调用方统一提交，保证与业务写同事务边界


async def list_audit(db: AsyncSession, session_id: str | None = None,
                     order_id: str | None = None) -> list[Audit]:
    from sqlalchemy import select
    stmt = select(Audit).order_by(Audit.created_at.desc())
    if session_id:
        stmt = stmt.where(Audit.session_id == session_id)
    if order_id:
        stmt = stmt.where(Audit.order_id == order_id)
    return list((await db.execute(stmt)).scalars().all())
