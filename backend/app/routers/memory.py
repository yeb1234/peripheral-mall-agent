"""记忆系统接口：用户画像、行为流、购买记录、多任务与续点。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.db.models import AgentTask
from app.db.session import get_db
from app.services import memory_service as mem

router = APIRouter(prefix="/api/memory", tags=["memory"])


class EventIn(BaseModel):
    buyer_id: str = "buyer_demo"
    kind: str
    sku: str = ""
    summary: str = ""
    payload: dict = {}
    session_id: str = ""
    task_id: str = ""


class ProfileIn(BaseModel):
    buyer_id: str
    display_name: str | None = None
    prefs: dict | None = None


@router.get("/snapshot")
async def snapshot(
    buyer_id: str = Query("buyer_demo"),
    session_id: str | None = Query(None),
    db=Depends(get_db),
):
    """一次拿全：画像/习惯、购买记录、行为流、最近对话、任务与 LLM 上下文摘要。"""
    out = await mem.snapshot(db, buyer_id, session_id=session_id)
    # snapshot 会为新买家懒创建画像，这里提交让「首次访问」也真正落库
    await db.commit()
    return out


@router.post("/event")
async def add_event(req: EventIn, db=Depends(get_db)):
    """前端埋点入口（浏览/加购/下载/抢购），后端业务写入也会调同一 service。"""
    await mem.ensure_profile(db, req.buyer_id)
    ev = await mem.record_event(
        db, req.buyer_id, req.kind, sku=req.sku, summary=req.summary,
        payload=req.payload, session_id=req.session_id, task_id=req.task_id,
    )
    await db.commit()
    return {"ok": True, "id": ev.id, "kind": ev.kind, "buyer_id": req.buyer_id}


@router.post("/profile")
async def update_profile(req: ProfileIn, db=Depends(get_db)):
    p = await mem.set_profile(db, req.buyer_id, display_name=req.display_name, prefs=req.prefs)
    await db.commit()
    return {"ok": True, "buyer_id": p.buyer_id, "display_name": p.display_name,
            "prefs": json.loads(p.prefs_json or "{}")}


@router.get("/events")
async def events(buyer_id: str = Query("buyer_demo"), limit: int = Query(30, ge=1, le=200), db=Depends(get_db)):
    return {"ok": True, "buyer_id": buyer_id, "events": await mem.recent_events(db, buyer_id, limit)}


@router.get("/tasks")
async def tasks(
    buyer_id: str = Query("buyer_demo"),
    include_done: bool = Query(False),
    db=Depends(get_db),
):
    if include_done:
        rows = list((await db.execute(
            select(AgentTask).where(AgentTask.buyer_id == buyer_id)
            .order_by(AgentTask.updated_at.desc()).limit(30)
        )).scalars().all())
    else:
        rows = await mem.open_tasks(db, buyer_id, limit=30)
    return {"ok": True, "buyer_id": buyer_id,
            "tasks": [mem.task_public(t) for t in rows]}


@router.post("/tasks/{task_id}/resume")
async def resume_task(task_id: str, db=Depends(get_db)):
    """把挂起任务恢复为进行中，并返回一条可直接发给客服的续点话术。"""
    out = await mem.resume_task(db, task_id)
    await db.commit()
    return out
