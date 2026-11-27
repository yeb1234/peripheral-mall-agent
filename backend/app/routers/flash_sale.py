"""抢购 / 预售排队接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db.session import get_db
from app.services import flash_sale as fs
from app.services import memory_service as mem

router = APIRouter(prefix="/api/flash-sale", tags=["flash-sale"])


class ReserveReq(BaseModel):
    sku: str
    buyer_id: str


class ConfigureReq(BaseModel):
    sku: str
    total_stock: int
    launch_at: float | None = None
    hold_seconds: int = 120


@router.post("/configure")
async def configure(req: ConfigureReq):
    fs.configure(req.sku, req.total_stock, req.launch_at, req.hold_seconds)
    return {"ok": True, "sku": req.sku, "total": req.total_stock}


@router.post("/reserve")
async def reserve(req: ReserveReq, db=Depends(get_db)):
    out = await fs.reserve(req.sku, req.buyer_id)
    try:
        hit = bool(out.get("won") or out.get("ok"))
        await mem.ensure_profile(db, req.buyer_id)
        await mem.record_event(
            db, req.buyer_id, "flash_reserve" if hit else "flash_attempt",
            sku=req.sku, summary=str(out.get("reason") or ("抢购成功" if hit else "排队中")),
            payload={k: v for k, v in out.items() if isinstance(v, (int, float, str, bool, type(None)))},
        )
        await db.commit()
    except Exception:  # noqa: BLE001
        await db.rollback()
    return out


@router.get("/status")
async def status(sku: str, buyer_id: str | None = None):
    return await fs.status(sku, buyer_id)
