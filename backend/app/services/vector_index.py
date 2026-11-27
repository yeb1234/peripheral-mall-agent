"""向量索引服务：把商品与知识库切片写进 pgvector，并提供检索入口。

设计取舍：
- 商品一条一个 chunk（SKU 维度），知识库按章节 chunk，两者共用一张表，
  用 source_kind 区分，避免每个域一套表。
- 写入前先比对 sha256，内容没变就不重复调 embedding（省 token、省时间）。
- embedding 不可用时整体降级：返回 degraded，不写半截数据。
"""
from __future__ import annotations

import hashlib
import logging
import time

from sqlalchemy import select

from app.config import settings
from app.core.embedding_client import get_embedder
from app.core import vector_store
from app.db.models import KnowledgeDoc, Product

log = logging.getLogger(__name__)

BATCH = 16


def _digest(text: str, model: str) -> str:
    raw = f"{model}|{settings.PGVECTOR_DIM}|{text}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


async def _collect(db) -> list[dict]:
    """商品 + 知识库切片，统一成 {source_kind, source_id, chunk_no, title, content}。"""
    docs: list[dict] = []

    products = (await db.execute(select(Product))).scalars().all()
    for p in products:
        content = (
            f"{p.sku} {p.name}｜品牌 {p.brand}｜类目 {p.category}｜售价 {p.price:g} {p.currency}\n"
            f"{p.description}\n规格：{p.specs}"
        )
        docs.append({
            "source_kind": "product",
            "source_id": p.sku,
            "chunk_no": 0,
            "title": p.name,
            "content": content,
            "meta": {"sku": p.sku, "category": p.category, "price": p.price},
        })

    chunks = (await db.execute(select(KnowledgeDoc))).scalars().all()
    for c in chunks:
        docs.append({
            "source_kind": "knowledge",
            "source_id": c.id,
            "chunk_no": c.section_no,
            "title": f"{c.doc_title} / {c.section_title}",
            "content": c.search_text or c.content,
            "meta": {
                "sku": c.product_sku,
                "doc_kind": c.doc_kind,
                "file_url": c.file_url,
                "section": c.section_title,
            },
        })
    return docs


async def sync(db, *, force: bool = False) -> dict:
    """增量同步向量索引。force=True 时忽略 sha 比对全量重写。"""
    t0 = time.perf_counter()
    if not vector_store.enabled():
        return {"ok": False, "enabled": False, "reason": "sqlite_or_disabled"}

    embedder = get_embedder()
    schema = await vector_store.ensure_schema(db)
    if not schema.get("ok"):
        return {"ok": False, "enabled": True, "degraded": True, "reason": schema.get("reason")}
    if not embedder.available:
        return {"ok": False, "enabled": True, "degraded": True,
                "reason": "embedding_unavailable: 缺 SILICONFLOW_API_KEY 或 RAG_ENABLED=false"}

    docs = await _collect(db)
    model = embedder.model

    # 表是原生 SQL 建的，没有 ORM 映射，指纹也直接用 SQL 读。
    from sqlalchemy import text
    old: dict[tuple[str, str, int], str] = {}
    got = (await db.execute(text(
        "SELECT source_kind, source_id, chunk_no, sha256 FROM doc_embeddings"
    ))).mappings().all()
    for r in got:
        old[(r["source_kind"], r["source_id"], int(r["chunk_no"]))] = r["sha256"]

    todo: list[dict] = []
    for d in docs:
        digest = _digest(d["content"], model)
        key = (d["source_kind"], d["source_id"], int(d["chunk_no"]))
        if not force and old.get(key) == digest:
            continue
        d["sha256"] = digest
        todo.append(d)

    written = 0
    failed = 0
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        res = await embedder.embed([b["content"] for b in batch])
        if not res.get("ok") or not res.get("vectors"):
            return {
                "ok": False, "enabled": True, "degraded": True,
                "reason": f"embedding_failed: {res.get('code') or res.get('detail')}",
                "pending": len(todo) - i, "written": written,
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            }
        vectors = res["vectors"]
        if len(vectors) != len(batch):
            return {"ok": False, "degraded": True, "reason": "vector_count_mismatch",
                    "written": written}
        for row, vec in zip(batch, vectors):
            if len(vec) != int(settings.PGVECTOR_DIM):
                return {"ok": False, "degraded": True,
                        "reason": f"dim_mismatch: got {len(vec)}, expect {settings.PGVECTOR_DIM}"}
            row["embedding"] = vec
            row["model"] = model
        written += await vector_store.upsert(db, batch)

    by_kind = {"product": 0, "knowledge": 0}
    for d in docs:
        by_kind[d["source_kind"]] = by_kind.get(d["source_kind"], 0) + 1

    return {
        "ok": True, "enabled": True, "degraded": False,
        "docs": len(docs), "by_kind": by_kind,
        "written": written, "skipped": len(docs) - len(todo), "failed": failed,
        "model": model, "dim": int(settings.PGVECTOR_DIM),
        "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
    }


async def search(db, query: str, top_k: int = 6, kind: str | None = None) -> dict:
    """query → 向量 → pgvector 检索。embedding 不可用时返回 degraded，调用方回退关键词。"""
    if not vector_store.enabled():
        return {"ok": False, "degraded": True, "reason": "sqlite_or_disabled", "results": []}
    embedder = get_embedder()
    if not embedder.available:
        return {"ok": False, "degraded": True, "reason": "embedding_unavailable", "results": []}

    res = await embedder.embed([query], persist=False)
    if not res.get("ok") or not res.get("vectors"):
        return {"ok": False, "degraded": True,
                "reason": res.get("code") or res.get("detail") or "embedding_failed",
                "results": []}
    vec = res["vectors"][0]
    hits = await vector_store.search(db, vec, top_k=top_k, kind=kind)
    return {"ok": True, "degraded": False, "count": len(hits),
            "results": hits, "latency_ms": res.get("latency_ms")}


async def status(db) -> dict:
    stats = await vector_store.stats(db)
    return {
        "ok": bool(stats.get("enabled")),
        **stats,
        "embedder": get_embedder().public,
        "config": {"PGVECTOR_ENABLED": settings.PGVECTOR_ENABLED,
                   "PGVECTOR_DIM": settings.PGVECTOR_DIM},
    }
