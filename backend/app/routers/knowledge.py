"""
说明书知识库接口（设置页 / 学习用）。

- GET  /api/knowledge/status    ：知识库文档构成、SKU 覆盖、最近同步时间
- GET  /api/knowledge/documents ：查看实际入库的章节切片
- POST /api/knowledge/sync      ：手动触发自动同步，返回幂等统计
- POST /api/knowledge/search   ：检索 → 精排 → 带章节引用的结果

与 /api/rag/* 一样：任何失败返回 HTTP 200 + ok=false + code + detail，
检索增强链路允许降级，不能把设置页或客服对话带崩。
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.db.session import AsyncSessionLocal
from app.services import knowledge_service as kb

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


class SearchPayload(BaseModel):
    query: str
    product_sku: str = ""
    top_k: int | None = None
    recall_k: int | None = None
    use_rerank: bool = True
    recall_mode: str = "hybrid"


@router.get("/status")
async def status():
    try:
        async with AsyncSessionLocal() as db:
            return await kb.stats(db)
    except Exception as e:
        return {"ok": False, "code": "SYS000", "detail": f"{type(e).__name__}: {e}"}


@router.get("/documents")
async def documents(sku: str = "", limit: int = 30):
    try:
        async with AsyncSessionLocal() as db:
            return await kb.list_documents(db, sku=sku, limit=limit)
    except Exception as e:
        return {"ok": False, "code": "SYS000", "detail": f"{type(e).__name__}: {e}",
                "documents": []}


@router.post("/sync")
async def sync():
    """手动触发一次幂等同步；内容未变的来源不会被重建。"""
    try:
        async with AsyncSessionLocal() as db:
            return await kb.sync_knowledge(db)
    except Exception as e:
        return {"ok": False, "code": "SYS000", "detail": f"{type(e).__name__}: {e}"}


@router.post("/search")
async def search(payload: SearchPayload):
    try:
        async with AsyncSessionLocal() as db:
            return await kb.search(
                db, payload.query,
                sku=payload.product_sku or "",
                top_k=payload.top_k,
                recall_k=payload.recall_k,
                use_rerank=payload.use_rerank,
                recall_mode=payload.recall_mode,
            )
    except Exception as e:
        return {"ok": False, "code": "SYS000", "detail": f"{type(e).__name__}: {e}",
                "candidates": [], "results": []}
