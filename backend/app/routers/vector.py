"""向量索引运维接口：体检、重建、试检索。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.db.session import get_db
from app.services import vector_index

router = APIRouter(prefix="/api/vector", tags=["vector"])


@router.get("/status")
async def status(db=Depends(get_db)):
    """向量层体检：表是否存在、条数、来源分布、embedding 是否可用。"""
    return await vector_index.status(db)


@router.post("/reindex")
async def reindex(force: bool = Query(False, description="true 则忽略 sha 全量重写"),
                  db=Depends(get_db)):
    """增量重建索引；内容未变的切片跳过，不重复调用 embedding。"""
    return await vector_index.sync(db, force=force)


@router.get("/search")
async def search(
    q: str = Query(..., min_length=1),
    kind: str = Query("", description="product / knowledge，空为混排"),
    top_k: int = Query(6, ge=1, le=20),
    db=Depends(get_db),
):
    """直接试检索，用于验证 pgvector 链路（不经过 Agent）。"""
    return await vector_index.search(db, q, top_k=top_k, kind=kind or None)
