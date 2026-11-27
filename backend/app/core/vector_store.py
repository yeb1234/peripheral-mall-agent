"""pgvector 向量层：建表、写入、检索都收敛在这里。

为什么用原生 SQL 而不是 ORM：
向量列（``vector(1024)``）和距离操作符（``<=>``）没有跨库等价物，
硬套 ORM 会让 SQLite 与 Postgres 两套 schema 打架。这里把向量能力收进
一个模块，SQLite 下整层禁用，业务层只看到 upsert / search / stats。

坐标约定：``embedding <=> q`` 为余弦距离，score = 1 - distance，越大越相似。
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

log = logging.getLogger(__name__)

TABLE = "doc_embeddings"


def enabled() -> bool:
    """只有 PG 连接才谈得上 pgvector；SQLite 一律跳过，不报错。"""
    return bool(settings.PGVECTOR_ENABLED and str(settings.DATABASE_URL).startswith("postgresql"))


def _vector_literal(vec: list[float]) -> str:
    """把 Python 浮点数组转成 pgvector 认的字符串 '[0.1,0.2]'。"""
    return "[" + ",".join(f"{float(x):.6f}" for x in vec) + "]"


async def ensure_schema(db: AsyncSession) -> dict:
    """建 extension / 表 / HNSW 索引，幂等。失败只降级，不阻塞启动。"""
    if not enabled():
        return {"ok": False, "enabled": False, "reason": "sqlite_or_disabled"}

    dim = int(settings.PGVECTOR_DIM)
    try:
        await db.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await db.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                id BIGSERIAL PRIMARY KEY,
                source_kind VARCHAR(16) NOT NULL,
                source_id VARCHAR(64) NOT NULL,
                chunk_no INTEGER NOT NULL DEFAULT 0,
                title VARCHAR(200) NOT NULL DEFAULT '',
                content TEXT NOT NULL,
                meta JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                model VARCHAR(64) NOT NULL DEFAULT '',
                sha256 VARCHAR(64) NOT NULL DEFAULT '',
                embedding vector({dim}) NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (source_kind, source_id, chunk_no)
            )
        """))
        await db.execute(text(
            f"CREATE INDEX IF NOT EXISTS {TABLE}_hnsw "
            f"ON {TABLE} USING hnsw (embedding vector_cosine_ops)"
        ))
        await db.commit()
        return {"ok": True, "enabled": True, "dim": dim, "table": TABLE}
    except Exception as e:  # 扩展缺失 / 权限不足 / 版本不支持索引
        await db.rollback()
        log.error("pgvector ensure_schema failed: %s: %s", type(e).__name__, e)
        return {"ok": False, "enabled": True, "reason": f"{type(e).__name__}: {e}"[:240]}


async def upsert(db: AsyncSession, rows: list[dict]) -> int:
    """按 (source_kind, source_id, chunk_no) 幂等写入，重复只更新内容与向量。"""
    if not rows or not enabled():
        return 0

    sql = text(f"""
        INSERT INTO {TABLE}
            (source_kind, source_id, chunk_no, title, content, meta, model, sha256, embedding)
        VALUES
            (:source_kind, :source_id, :chunk_no, :title, :content,
             CAST(:meta AS jsonb), :model, :sha256, CAST(:embedding AS vector))
        ON CONFLICT (source_kind, source_id, chunk_no) DO UPDATE SET
            title = EXCLUDED.title,
            content = EXCLUDED.content,
            meta = EXCLUDED.meta,
            model = EXCLUDED.model,
            sha256 = EXCLUDED.sha256,
            embedding = EXCLUDED.embedding,
            updated_at = now()
    """)

    written = 0
    for row in rows:
        await db.execute(sql, {
            "source_kind": str(row["source_kind"])[:16],
            "source_id": str(row["source_id"])[:64],
            "chunk_no": int(row.get("chunk_no", 0)),
            "title": str(row.get("title", ""))[:200],
            "content": row["content"],
            "meta": json.dumps(row.get("meta") or {}, ensure_ascii=False),
            "model": str(row.get("model", ""))[:64],
            "sha256": str(row.get("sha256", ""))[:64],
            "embedding": _vector_literal(row["embedding"]),
        })
        written += 1
    await db.commit()
    return written


async def search(
    db: AsyncSession, query_vec: list[float], top_k: int = 6, kind: str | None = None
) -> list[dict]:
    """余弦相似度检索。kind 为空则全表搜（商品 + 知识库混排）。"""
    if not enabled():
        return []

    base = f"""
        SELECT source_kind, source_id, chunk_no, title, content, meta,
               1 - (embedding <=> CAST(:q AS vector)) AS score
        FROM {TABLE}
    """
    params = {"q": _vector_literal(query_vec), "k": max(1, min(int(top_k), 50))}
    if kind:
        sql = text(base + " WHERE source_kind = :kind ORDER BY embedding <=> CAST(:q AS vector) LIMIT :k")
        params["kind"] = kind
    else:
        sql = text(base + " ORDER BY embedding <=> CAST(:q AS vector) LIMIT :k")

    rows = (await db.execute(sql, params)).mappings().all()
    return [
        {
            "source_kind": r["source_kind"],
            "source_id": r["source_id"],
            "chunk_no": r["chunk_no"],
            "title": r["title"],
            "content": r["content"],
            "meta": r["meta"] or {},
            "score": round(float(r["score"]), 4),
        }
        for r in rows
    ]


async def stats(db: AsyncSession) -> dict:
    """向量表体检：条数、来源分布、最近写入时间。"""
    if not enabled():
        return {"enabled": False, "reason": "sqlite_or_disabled"}
    try:
        total = await db.scalar(text(f"SELECT count(*) FROM {TABLE}"))
        kinds = (await db.execute(text(
            f"SELECT source_kind, count(*) AS n FROM {TABLE} GROUP BY source_kind ORDER BY source_kind"
        ))).mappings().all()
        latest = await db.scalar(text(f"SELECT max(updated_at) FROM {TABLE}"))
        return {
            "enabled": True,
            "table": TABLE,
            "dim": int(settings.PGVECTOR_DIM),
            "total": int(total or 0),
            "by_kind": {r["source_kind"]: int(r["n"]) for r in kinds},
            "latest": latest.isoformat() if latest else None,
        }
    except Exception as e:
        await db.rollback()
        return {"enabled": True, "error": f"{type(e).__name__}: {e}"[:200]}
