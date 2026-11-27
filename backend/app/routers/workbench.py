"""客服工作台：人工接管视图（转人工后的上下文、审计、订单）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.audit import list_audit
from app.core.session_manager import bound_orders
from app.db.models import Message, Session
from app.db.session import get_db
from sqlalchemy import select

router = APIRouter(prefix="/api/workbench", tags=["workbench"])


@router.get("/sessions")
async def sessions(db=Depends(get_db), limit: int = Query(20)):
    stmt = select(Session).order_by(Session.created_at.desc()).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    out = []
    for s in rows:
        last = (await db.execute(
            select(Message).where(Message.session_id == s.id).order_by(Message.created_at.desc()).limit(1)
        )).scalar_one_or_none()
        orders = await bound_orders(db, s.id)
        out.append({
            "session_id": s.id, "buyer_id": s.buyer_id, "channel": s.channel,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "last_message": last.content if last else "",
            "bound_orders": len(orders),
        })
    return out


@router.get("/sessions/{session_id}/messages")
async def messages(session_id: str, db=Depends(get_db)):
    stmt = select(Message).where(Message.session_id == session_id).order_by(Message.created_at.asc())
    rows = (await db.execute(stmt)).scalars().all()
    return [{"role": m.role, "content": m.content, "meta": m.meta,
             "created_at": m.created_at.isoformat() if m.created_at else None} for m in rows]


@router.get("/audits")
async def audits(db=Depends(get_db), session_id: str | None = Query(None),
                limit: int = Query(50)):
    rows = await list_audit(db, session_id=session_id)
    return [{
        "id": a.id, "session_id": a.session_id, "order_id": a.order_id,
        "event_type": a.event_type, "actor": a.actor, "decision": a.decision,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    } for a in rows[:limit]]
