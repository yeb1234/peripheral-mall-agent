"""
RAG / Reranker 自检与演示接口（设置页用）。

- GET  /api/rag/status  ：硅基流动配置（key 脱敏）、重排统计、知识库文档数
- GET  /api/rag/catalog ：查看 26 个 SKU 生成的检索文档（学习用）
- POST /api/rag/search  ：完整链路「召回 → 重排 → Top-K」，返回重排前后顺序与分数
- POST /api/rag/rerank  ：裸重排，传入任意 query + documents，直接看相关性分数

与 /api/llm/* 一样：任何失败都返回 HTTP 200 + ok=false + code + detail，不抛 500，
因为检索增强链路允许降级，不能把设置页或客服对话带崩。
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.embedding_client import get_embedder
from app.core.rerank_client import get_reranker
from app.db.session import AsyncSessionLocal
from app.services import rag_service

router = APIRouter(prefix="/api/rag", tags=["rag"])


class SearchPayload(BaseModel):
    query: str
    top_k: int | None = None
    recall_k: int | None = None
    use_rerank: bool = True
    recall_mode: str = "hybrid"   # keyword / vector / hybrid，供对比实验与降级验证


class RerankPayload(BaseModel):
    query: str
    documents: list[str] = Field(default_factory=list)
    top_n: int | None = None


@router.get("/status")
async def status():
    try:
        async with AsyncSessionLocal() as db:
            catalog = await rag_service.build_catalog(db)
        return {
            "ok": True,
            **get_reranker().public(),
            "embedding": get_embedder().public(),
            "catalog_size": len(catalog),
            "pipeline": "DB SKU 文档 → 关键词 + bge-m3 向量置信度加权 RRF 混合召回 → bge-reranker-v2-m3 精排 → Top-K",
        }
    except Exception as e:
        return {"ok": False, "code": "SYS000", "detail": f"{type(e).__name__}: {e}",
                **get_reranker().public()}


@router.get("/catalog")
async def catalog(limit: int = 26):
    try:
        async with AsyncSessionLocal() as db:
            docs = await rag_service.build_catalog(db)
        return {"ok": True, "count": len(docs),
                "documents": docs[:max(1, min(limit, 100))]}
    except Exception as e:
        return {"ok": False, "code": "SYS000", "detail": f"{type(e).__name__}: {e}",
                "documents": []}


@router.post("/search")
async def search(payload: SearchPayload):
    try:
        async with AsyncSessionLocal() as db:
            out = await rag_service.search(
                db, payload.query,
                top_k=payload.top_k,
                recall_k=payload.recall_k,
                use_rerank=payload.use_rerank,
                recall_mode=payload.recall_mode,
            )
        return out
    except Exception as e:
        return {"ok": False, "code": "SYS000", "detail": f"{type(e).__name__}: {e}",
                "candidates": [], "results": []}


@router.post("/rerank")
async def rerank(payload: RerankPayload):
    try:
        return await rag_service.rerank_texts(payload.query, payload.documents, payload.top_n)
    except Exception as e:
        return {"ok": False, "degraded": True, "code": "SYS000",
                "detail": f"{type(e).__name__}: {e}", "results": []}
