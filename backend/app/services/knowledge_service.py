"""
说明书知识库：Manual / Resource → 章节切片 → 召回 → Reranker → 带引用的答案。

为什么是独立索引域：
    商品 RAG（rag_service）只索引 26 个 SKU 的参数文档，服务于「推荐 / 规格」；
    说明书知识库索引章节级使用说明，服务于「怎么配对 / 怎么设置 / 故障排查」。
    两者共用 recall() 与 SiliconFlow Reranker，但召回池分开，避免说明书章节
    混进商品推荐结果、也避免把使用问题误判成选购需求。

「自动」体现在三处：
    ① 服务启动（lifespan）自动调用 sync_knowledge()，空库/存量库都自愈；
    ② 内容指纹 sha256 幂等：说明书没改就不重建，改了才整体替换该来源的切片；
    ③ 下载文件正文与知识库切片同源（manual_content.render_document），
       因此说明书一旦更新，下载内容、SHA256、知识库三者一起变。

    另有 POST /api/knowledge/sync 供演示时手动触发并观察同步统计。
"""
from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import delete, func, select

from app.config import settings
from app.core.rerank_client import get_reranker
from app.db.models import KnowledgeDoc, Manual, Product, Resource
from app.services import manual_content, rag_service, resource_service

SOURCE_LABELS = {"manual": "用户手册", "resource": "资源文档"}


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def _sources(db) -> list[dict]:
    """读出全部 Manual / Resource，联商品序列化成与下载接口一致的 item。"""
    out: list[dict] = []
    for model, kind in ((Manual, "manual"), (Resource, "resource")):
        rows = (
            await db.execute(
                select(model, Product).join(Product, model.product_id == Product.id)
            )
        ).all()
        for m, p in rows:
            item = resource_service._serialize(m, p, kind)
            out.append({
                "kind": kind,
                "item": item,
                "full_text": manual_content.render_document(kind, item),
                "sections": manual_content.sections(kind, item),
            })
    return out


async def sync_knowledge(db) -> dict:
    """幂等自动同步：把来源正文按章节切片写入 knowledge_docs。

    - 内容指纹未变 → 跳过（unchanged）
    - 内容变更     → 删除该来源旧切片再写入新切片（updated）
    - 新来源       → 直接写入（created）
    - 来源已删除   → 清理孤立切片（removed）

    同一事务内完成，任何时刻知识库都不会出现「半新半旧」的混排。
    """
    sources = await _sources(db)
    existing = (await db.execute(select(KnowledgeDoc))).scalars().all()
    by_source: dict[tuple[str, str], list[KnowledgeDoc]] = {}
    for d in existing:
        by_source.setdefault((d.source_kind, d.source_id), []).append(d)

    created = updated = unchanged = removed = chunks = 0
    seen: set[tuple[str, str]] = set()

    for src in sources:
        kind, item = src["kind"], src["item"]
        key = (kind, item["id"])
        seen.add(key)
        digest = _digest(src["full_text"])
        secs = src["sections"]
        old = by_source.get(key, [])

        if old and len(old) == len(secs) and all(o.content_sha256 == digest for o in old):
            unchanged += 1
            chunks += len(old)
            continue

        if old:
            await db.execute(
                delete(KnowledgeDoc).where(
                    KnowledgeDoc.source_kind == kind,
                    KnowledgeDoc.source_id == item["id"],
                )
            )
            updated += 1
        else:
            created += 1

        for s in secs:
            search_text = (
                f"{item.get('product_sku', '')} {item.get('product_name', '')} "
                f"{item.get('title', '')} {s['section_title']} {s['content']}"
            )
            db.add(KnowledgeDoc(
                source_kind=kind,
                source_id=item["id"],
                product_sku=item.get("product_sku", ""),
                product_name=item.get("product_name", ""),
                category=item.get("category", ""),
                doc_title=item.get("title", ""),
                doc_kind=item.get("resource_kind", "manual"),
                section_no=s["section_no"],
                section_title=s["section_title"],
                content=s["content"],
                search_text=search_text[:4000],
                file_url=item.get("file_url", ""),
                content_sha256=digest,
            ))
            chunks += 1

    for key, docs in by_source.items():
        if key in seen:
            continue
        await db.execute(
            delete(KnowledgeDoc).where(
                KnowledgeDoc.source_kind == key[0],
                KnowledgeDoc.source_id == key[1],
            )
        )
        removed += len(docs)

    await db.commit()
    return {
        "ok": True,
        "sources": len(sources),
        "created": created, "updated": updated,
        "unchanged": unchanged, "removed": removed,
        "chunks": chunks,
        "detail": f"同步完成：{len(sources)} 份说明书/资源 → {chunks} 个章节切片"
                  f"（新建 {created} · 更新 {updated} · 未变 {unchanged} · 清理 {removed}）",
    }


async def stats(db) -> dict:
    """设置页状态：文档构成与覆盖情况。"""
    total = await db.scalar(select(func.count()).select_from(KnowledgeDoc)) or 0
    by_kind: dict[str, int] = {}
    for kind in ("manual", "resource"):
        by_kind[kind] = await db.scalar(
            select(func.count()).select_from(KnowledgeDoc).where(KnowledgeDoc.source_kind == kind)
        ) or 0
    sku_count = await db.scalar(
        select(func.count(func.distinct(KnowledgeDoc.product_sku)))
    ) or 0
    last = await db.scalar(select(func.max(KnowledgeDoc.updated_at)))
    product_docs = len(await rag_service.build_catalog(db))
    return {
        "ok": True,
        "total_docs": total,
        "manual_docs": by_kind["manual"],
        "resource_docs": by_kind["resource"],
        "sku_count": sku_count,
        "product_docs": product_docs,
        "last_synced_at": last.isoformat() if last else None,
        "pipeline": "Manual/Resource 正文 → 章节切片(内容指纹幂等) → 关键词召回 → /v1/rerank 精排 → 带引用答案",
        "indexes": [
            {"name": "商品参数文档", "count": product_docs, "usage": "推荐 / 规格 / 兼容性"},
            {"name": "说明书与资源章节", "count": total, "usage": "使用设置 / 故障排查 / 保修"},
        ],
    }


async def _documents(db, sku: str = "") -> list[dict]:
    """知识库切片 → 可被 rag_service.recall 直接消费的文档结构。"""
    stmt = select(KnowledgeDoc)
    if sku:
        stmt = stmt.where(KnowledgeDoc.product_sku == sku.upper())
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "id": d.id,
            "sku": d.product_sku,
            "name": d.product_name,
            "text": d.search_text,
            "doc_title": d.doc_title,
            "doc_kind": d.doc_kind,
            "source_kind": d.source_kind,
            "section_no": d.section_no,
            "section_title": d.section_title,
            "content": d.content,
            "file_url": d.file_url,
            "content_sha256": d.content_sha256,
        }
        for d in rows
    ]


def _observation(query: str, results: list[dict], sku: str, degraded: bool) -> str:
    if not results:
        return ("说明书知识库里暂时没有查到相关内容。"
                "可以换个说法（如「怎么配对蓝牙」「DPI 怎么调」）或告诉我具体型号（如 KB-PRO87）。")
    label = "检索并精排" if not degraded else "关键词检索"
    scope = f"（已限定型号 {sku}）" if sku else ""
    lines = [f"根据官方说明书知识库{label}到以下说明{scope}：", ""]
    for r in results:
        lines.append(f"▸ 【{r['sku']} · {r['doc_title']}】第 {r['section_no']} 节「{r['section_title']}」")
        lines.append("   " + r["content"].replace("\n", " ")[:360])
        lines.append("")
    urls = list(dict.fromkeys(r["file_url"] for r in results if r["file_url"]))
    lines.append("以上为说明书原文片段，非模型凭记忆生成。需要完整文档可到「资源下载」按型号下载，"
                 "页面提供 SHA256 校验。")
    if urls:
        lines.append("原文链接：" + "；".join(urls[:3]))
    return "\n".join(lines)


async def search(
    db,
    query: str,
    sku: str = "",
    top_k: int | None = None,
    recall_k: int | None = None,
    use_rerank: bool = True,
    recall_mode: str = "hybrid",
) -> dict:
    """说明书知识库检索：混合召回 →（可选）精排 → 带章节引用的结果 + 客服话术。"""
    q = (query or "").strip()
    k = max(1, min(int(top_k or 4), 10))
    if not q:
        return {"ok": False, "code": "RAG004", "detail": "检索问题不能为空。",
                "query": q, "candidates": [], "results": [],
                "observation": "请描述您想查的使用问题（如「怎么配对蓝牙」「键盘没反应怎么排查」）。"}

    docs = await _documents(db, sku)
    if not docs:
        return {"ok": False, "code": "KB001", "detail": "知识库尚无切片，请先执行同步。",
                "query": q, "sku": sku or None, "doc_count": 0,
                "candidates": [], "results": [],
                "observation": "说明书知识库尚未建立，请稍后重试或联系管理员执行同步。"}

    rk = max(int(recall_k or 0), k * 3, 8)
    rec = await rag_service.recall_hybrid(q, docs, rk, mode=recall_mode)
    candidates = rec["candidates"]
    if not candidates:
        # 有 SKU 但没命中：退一步在该 SKU 内按章节顺序给前 K 个，避免空手而归
        candidates = [{**d, "recall_rank": i + 1, "recall_score": 0.0,
                       "kw_rank": None, "vec_rank": None, "recall_source": "fallback"}
                      for i, d in enumerate(docs[:k])]

    texts = [c["text"] for c in candidates]
    if use_rerank and settings.RAG_ENABLED:
        rr = await get_reranker().rerank(q, texts, top_n=k)
    else:
        rr = {
            "ok": False, "degraded": True, "code": "RAG000",
            "detail": "本次未启用 Reranker（手动关闭/离线 Mock/未配置），使用一阶段召回顺序。",
            "model": settings.SILICONFLOW_RERANK_MODEL, "cached": False,
            "latency_ms": None,
            "results": [{"index": i, "score": None, "text": t} for i, t in enumerate(texts[:k])],
        }

    results: list[dict] = []
    for final_rank, item in enumerate(rr["results"], start=1):
        idx = int(item.get("index", -1))
        if not (0 <= idx < len(candidates)):
            continue
        src = candidates[idx]
        results.append({
            "sku": src["sku"], "product_name": src["name"],
            "doc_title": src["doc_title"], "doc_kind": src["doc_kind"],
            "source_kind": src["source_kind"],
            "section_no": src["section_no"], "section_title": src["section_title"],
            "content": src["content"], "file_url": src["file_url"],
            "rank": final_rank, "score": item.get("score"),
            "recall_rank": src["recall_rank"], "recall_score": src["recall_score"],
            "moved": src["recall_rank"] - final_rank,
        })

    return {
        "ok": True,
        "query": q,
        "sku": sku or None,
        "doc_count": len(docs),
        "recall_k": len(candidates),
        "top_k": len(results),
        "degraded": bool(rr.get("degraded")),
        "code": rr.get("code"),
        "detail": rr.get("detail"),
        "rerank": {
            "ok": rr.get("ok"), "degraded": rr.get("degraded"), "code": rr.get("code"),
            "detail": rr.get("detail"), "model": rr.get("model"),
            "cached": rr.get("cached"), "latency_ms": rr.get("latency_ms"),
        },
        "retrieval": {
            "requested_mode": rec["requested_mode"],
            "effective_mode": rec["effective_mode"],
            "degraded": rec["degraded"],
            "code": rec["code"], "detail": rec["detail"],
            "embed_model": rec["embed_model"],
            "embed_latency_ms": rec["embed_latency_ms"],
            "cached": rec["cached"],
            "latency_ms": rec["latency_ms"],
            "kw_top": rec["kw_top"], "vec_top": rec["vec_top"],
            "kw_confidence": rec.get("kw_confidence"),
            "w_keyword": rec.get("w_keyword"), "w_vector": rec.get("w_vector"),
            "fusion": rec.get("fusion"),
        },
        "candidates": [
            {"sku": c["sku"], "section_title": c["section_title"],
             "recall_rank": c["recall_rank"], "recall_score": c["recall_score"],
             "kw_rank": c.get("kw_rank"), "vec_rank": c.get("vec_rank"),
             "recall_source": c.get("recall_source")}
            for c in candidates
        ],
        "results": results,
        "observation": _observation(q, results, sku, bool(rr.get("degraded"))),
    }


async def list_documents(db, sku: str = "", limit: int = 30) -> dict:
    """学习用：查看知识库里实际存了哪些切片。"""
    docs = await _documents(db, sku)
    lim = max(1, min(int(limit or 30), 200))
    return {
        "ok": True,
        "total": len(docs),
        "documents": [
            {key: d[key] for key in
             ("sku", "doc_title", "doc_kind", "section_no", "section_title",
              "content", "file_url", "content_sha256")}
            for d in docs[:lim]
        ],
    }
