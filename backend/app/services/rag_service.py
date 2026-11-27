"""
RAG 商品知识库：双路混合召回 → Reranker 精排 → Top-K 上下文。

链路（可直接讲给面试官听）：
  ① 文档化：把 26 个 SKU 的名称/品类/价格/分组规格拼成一段可检索的自然语言文档；
  ② 召回A：本地中文 2-gram + 领域词加权 + SKU 精确匹配（零网络、精确型号不掉）；
    召回B：硅基流动 /v1/embeddings 的 BAAI/bge-m3 向量 + 余弦（补口语语义）；
    融合  ：**置信度自适应加权 RRF**（见 _keyword_confidence / recall_hybrid）；
  ③ 精排  ：把候选全文 + query 交给 /v1/rerank（BAAI/bge-reranker-v2-m3）；
  ④ 注入  ：Top-K 文档作为 observation 交给售前 Agent，回答「有据可依」；
  ⑤ 降级  ：key 缺失 / 超时 / 429 / 解析失败 → 退回纯关键词召回顺序，链路永不断。

为什么用「加权」RRF 而不是等权：等权 RRF 隐含假设两路召回质量相当。实测中，
纯口语 query（「开视频会议对方听不清」）的关键词路完全失效（top1 分数 < 1，
返回的却是扩展坞），但仍占一半融合票，把向量路的正确候选挤出 Top-K。
因此用关键词路自身的 top1 分数估计置信度，低置信时压低它的权重，让向量路主导。
这条改动来自评测集发现的回归，详见 docs/EVALUATION_REPORT.md 第 3.1 节。

为什么不用向量库（FAISS/Milvus）：26 个 SKU + 416 个章节切片，全量余弦在纯 Python 下
几十毫秒内算完；文档向量按「模型名 + 文本 SHA256」落盘缓存，重启不重复烧 token。
真到百万级文档，只需把 recall_hybrid 里的向量部分换成 ANN 检索。
"""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import select

from app.config import settings
from app.core.rerank_client import get_reranker
from app.db.models import Product

_CATEGORY_CN = {
    "keyboard": "机械键盘", "mouse": "电竞鼠标", "headset": "游戏耳机",
    "webcam": "摄像头", "dock": "扩展坞", "gamepad": "游戏手柄",
}

# 领域词：命中即高权重，弥补中文没有分词器的问题
_DOMAIN_WORDS = [
    "无线", "有线", "蓝牙", "2.4G", "Type-C", "USB-C", "低延迟", "回报率", "8K", "4K",
    "大手", "中手", "小手", "趴握", "抓握", "指尖握", "轻量化", "重量", "对称", "人体工学",
    "机械轴", "磁轴", "光轴", "热插拔", "触发键程", "Rapid Trigger", "RT",
    "PBT", "ABS", "铝合金", "CNC", "塑料", "键帽", "配列", "60%", "75%", "TKL", "全尺寸", "87键",
    "ANC", "主动降噪", "降噪", "HiFi", "7.1", "环绕", "麦克风", "封闭式", "开放式",
    "DPI", "传感器", "RGB", "驱动", "板载", "宏", "macOS", "Windows", "Linux",
    "PS5", "Switch", "iPadOS", "Android", "iOS", "静音", "旋钮",
]


def _fmt(v) -> str:
    if isinstance(v, list):
        return "、".join(map(str, v))
    if isinstance(v, bool):
        return "支持" if v else "不支持"
    return str(v)


def _flat_spec_lines(specs: Any) -> list[str]:
    """把分组规格压成 「组·属性=值」 列表。"""
    lines: list[str] = []

    def walk(node, group: str = ""):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, dict):
                    walk(v, str(k))
                elif isinstance(v, list):
                    if v and not isinstance(v[0], dict):
                        lines.append(f"{group + '·' if group else ''}{k}={_fmt(v)}")
                else:
                    lines.append(f"{group + '·' if group else ''}{k}={_fmt(v)}")
        elif isinstance(node, list):
            for item in node:
                walk(item, group)

    walk(specs)
    return lines


def product_doc(p: Product) -> dict:
    """单个 SKU → 可检索文档 + 结构化元信息。"""
    try:
        specs = json.loads(p.specs) if p.specs else {}
    except Exception:
        specs = {}
    lines = _flat_spec_lines(specs)
    cat = _CATEGORY_CN.get(p.category, p.category)
    text = (
        f"【{p.sku}】{p.name}｜{p.brand}｜{cat}｜公示价 ¥{p.price:.0f}｜库存 {p.stock}\n"
        f"{p.description or ''}\n"
        f"规格参数：{'；'.join(lines)}"
    )
    return {
        "sku": p.sku,
        "name": p.name,
        "brand": p.brand,
        "category": p.category,
        "category_cn": cat,
        "price": p.price,
        "stock": p.stock,
        "text": text.strip()[:900],   # 控制 rerank 输入 token
    }


async def build_catalog(db) -> list[dict]:
    rows = (await db.execute(select(Product))).scalars().all()
    return [product_doc(p) for p in rows]


def _tokens(q: str) -> list[tuple[str, int]]:
    """query → (token, weight)。中文 2-gram + 英文数字词 + 领域词。"""
    low = q.lower().strip()
    out: dict[str, int] = {}

    def add(tok: str, w: int):
        tok = tok.strip()
        if len(tok) >= 2:
            out[tok] = max(out.get(tok, 0), w)

    # 英文 / 数字词（SKU、Hz、PBT、macOS…）
    for w in re.findall(r"[a-z0-9.%+\-/]+", low):
        if len(w) >= 2:
            add(w, 4)
    # 中文 2-gram（重叠切片，覆盖「中大手」「主动降噪」这类短语）
    for seg in re.findall(r"[\u4e00-\u9fff]+", low):
        for i in range(len(seg) - 1):
            add(seg[i:i + 2], 1)
        if len(seg) == 1:
            add(seg, 1)
    # 领域词命中给高权重
    for w in _DOMAIN_WORDS:
        if w.lower() in low:
            add(w.lower(), 3)
    for cn in _CATEGORY_CN.values():
        if cn in low:
            add(cn, 4)
    return list(out.items())


def recall(query: str, catalog: list[dict], k: int | None = None) -> list[dict]:
    """一阶段召回：轻量 BM25 风格打分，纯 CPU、零网络。"""
    k = max(1, int(k or settings.RAG_RECALL_K))
    toks = _tokens(query)
    query_low = query.lower()
    scored: list[tuple[float, dict]] = []
    for doc in catalog:
        text_low = doc["text"].lower()
        score = 0.0
        for tok, w in toks:
            if tok in text_low:
                score += w
        # SKU / 名称精确命中强加权
        sku = doc["sku"].lower()
        if sku in query_low:
            score += 12
        for piece in re.findall(r"[a-z0-9]{3,}", doc["name"].lower()):
            if piece in query_low:
                score += 2
        if score <= 0:
            continue
        score /= (1 + 0.0004 * len(doc["text"]))   # 轻微惩罚超长文档
        scored.append((round(score, 4), doc))
    scored.sort(key=lambda x: (-x[0], x[1]["sku"]))
    out = []
    for rank, (s, doc) in enumerate(scored[:k], start=1):
        out.append({**doc, "recall_rank": rank, "recall_score": s})
    return out


def _keyword_confidence(kw: list[dict]) -> float:
    """关键词路自身的置信度 ∈ [0,1]，只依赖召回分数分布，不看标注答案。

    经验分界（来自 20 题开发集的关键词 top1 分数）：
      字面型/型号型 query  7.4 ~ 15.0   → conf ≈ 0.93 ~ 1.00，关键词可靠
      纯口语语义型 query   0.0 ~  4.2   → conf ≈ 0.00 ~ 0.53，关键词几乎全是噪声
    8.0 是两簇之间的分界点，线性归一化后用于融合权重。说明书域分数整体更高
    （章节文本更短、领域词密集），同样受益于「高置信才让关键词主导」。
    """
    if not kw:
        return 0.0
    top = float(kw[0].get("recall_score") or 0.0)
    return round(min(1.0, max(0.0, top / 8.0)), 4)


async def recall_hybrid(
    query: str, catalog: list[dict], k: int | None = None, mode: str = "hybrid"
) -> dict:
    """一阶段混合召回：关键词 + 硅基流动 bge-m3 向量，加权 RRF 融合。

    mode = keyword / vector / hybrid / hybrid_equal。
      hybrid        —— 置信度自适应加权（生产默认）
      hybrid_equal  —— 旧版等权 RRF，仅用于评测里做「修复前后」对照
    任何向量侧失败都自动退回纯关键词，effective_mode 标成 keyword。

    RRF 用排名而非分数融合，回避「关键词分 0~20 / 余弦 -1~1」量纲不一致：
        score(d) = Σ w_i / (60 + rank_i(d))
    但**等权隐含假设两路质量相当**，实测在口语 query 上不成立，因此 hybrid
    用关键词路自己的 top1 分数估计置信度，动态定权：
        conf  = clamp(kw_top1 / 8, 0, 1)
        w_kw  = 2.0 * conf²             # 0（噪声路完全不投票）~ 2.0（强字面信号主导）
        w_vec = 1.0                     # 向量路恒定，保证语义始终有票
    用平方而非线性，是因为等权 RRF 里「两路都出现的文档」天然拿双份加成：
    即使把噪声路权重降到 0.25，它仍能把向量路真正相关的候选挤出 Top-K。
    平方让低置信时噪声路的贡献趋近于 0，只在它自己有把握时才主导排序。
    这样精确型号/参数词由关键词主导，口语场景交给向量，互不拖累。
    """
    import time

    from app.core.embedding_client import cosine, get_embedder

    t0 = time.perf_counter()
    k = max(1, int(k or settings.RAG_RECALL_K))
    pool_k = max(k * 3, 12)
    kw = recall(query, catalog, pool_k)

    def _key(d: dict) -> str:
        # 商品文档按 SKU 唯一；说明书切片同一 SKU 多章节，用切片 id
        return str(d.get("id") or d.get("sku") or id(d))

    conf = _keyword_confidence(kw)
    equal = mode == "hybrid_equal"
    w_kw = 1.0 if equal else round(2.0 * conf * conf, 4)
    w_vec = 1.0
    base_meta = {
        "requested_mode": mode, "effective_mode": "hybrid" if equal else mode,
        "degraded": False, "code": None, "detail": None,
        "embed_model": get_embedder().model,
        "kw_top": [{"sku": c["sku"], "section_title": c.get("section_title"),
                    "rank": c["recall_rank"], "score": c["recall_score"]} for c in kw[:k]],
        "vec_top": [], "embed_latency_ms": None, "cached": 0,
        "latency_ms": None,
        "kw_confidence": conf, "w_keyword": w_kw, "w_vector": w_vec,
        "fusion": "equal-rrf" if equal else "confidence-weighted-rrf",
    }

    if mode == "keyword" or not catalog:
        out = []
        for rank, d in enumerate(kw[:k], start=1):
            out.append({**d, "recall_rank": rank, "recall_score": d["recall_score"],
                        "kw_rank": rank, "vec_rank": None, "recall_source": "keyword"})
        base_meta["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return {"candidates": out, **base_meta}

    emb = get_embedder()
    dres = await emb.embed([d["text"] for d in catalog], persist=True)
    qres = await emb.embed([query], persist=False)
    if not (dres.get("ok") and qres.get("ok")):
        # 降级：向量不可用 → 纯关键词
        failed = dres if not dres.get("ok") else qres
        out = []
        for rank, d in enumerate(kw[:k], start=1):
            out.append({**d, "recall_rank": rank, "recall_score": d["recall_score"],
                        "kw_rank": rank, "vec_rank": None, "recall_source": "keyword"})
        meta = {**base_meta, "effective_mode": "keyword", "degraded": True,
                "code": failed.get("code"), "detail": failed.get("detail"),
                "embed_latency_ms": failed.get("latency_ms"),
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}
        return {"candidates": out, **meta}

    qvec = qres["vectors"][0]
    scored: list[tuple[float, dict]] = []
    for d, vec in zip(catalog, dres["vectors"]):
        if not vec:
            continue
        scored.append((cosine(qvec, vec), d))
    scored.sort(key=lambda x: (-x[0], str(x[1].get("sku", ""))))
    vec_docs = [d for _, d in scored[:pool_k]]
    vec_score_by_key = {_key(d): round(s, 6) for s, d in scored[:pool_k]}
    base_meta["vec_top"] = [
        {"sku": d["sku"], "section_title": d.get("section_title"),
         "rank": i + 1, "score": vec_score_by_key[_key(d)]}
        for i, d in enumerate(vec_docs[:k])
    ]
    base_meta["embed_latency_ms"] = round(
        float(dres.get("latency_ms") or 0) + float(qres.get("latency_ms") or 0), 1
    )
    base_meta["cached"] = int(dres.get("cached") or 0)
    base_meta["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    if mode == "vector":
        out = []
        for rank, d in enumerate(vec_docs[:k], start=1):
            out.append({**d, "recall_rank": rank,
                        "recall_score": vec_score_by_key[_key(d)],
                        "kw_rank": None, "vec_rank": rank, "recall_source": "vector"})
        return {"candidates": out, **base_meta}

    # hybrid：加权 RRF 融合，候选集取两路并集，按融合分排序。
    # 低置信时关键词路权重被压到 0.25，避免「全噪声的一路」拿走一半选票，
    # 把向量路真正相关的候选挤出 Top-K（评测 v1 发现的回归）。
    rrf: dict[str, float] = {}
    rank_info: dict[str, dict] = {}
    merged: dict[str, dict] = {}
    for rank, d in enumerate(kw, start=1):
        key = _key(d)
        rrf[key] = rrf.get(key, 0.0) + w_kw / (60 + rank)
        rank_info.setdefault(key, {})["kw"] = rank
        merged[key] = d
    for rank, d in enumerate(vec_docs, start=1):
        key = _key(d)
        rrf[key] = rrf.get(key, 0.0) + w_vec / (60 + rank)
        rank_info.setdefault(key, {})["vec"] = rank
        merged.setdefault(key, d)
    for key, info in rank_info.items():
        info["src"] = "both" if info.get("kw") and info.get("vec") else (
            "keyword" if info.get("kw") else "vector")
    ordered = sorted(merged.items(), key=lambda kv: (-rrf[kv[0]], str(kv[1].get("sku", ""))))
    out = []
    for rank, (key, d) in enumerate(ordered[:k], start=1):
        info = rank_info.get(key, {})
        out.append({
            **d,
            "recall_rank": rank,
            "recall_score": round(rrf[key] * 1000, 4),  # 放大便于展示，排序不变
            "kw_rank": info.get("kw"), "vec_rank": info.get("vec"),
            "recall_source": info.get("src", "keyword"),
        })
    return {"candidates": out, **base_meta}


def _observation(results: list[dict], degraded: bool, model: str) -> str:
    """给 Agent 的 observation：同时是客服话术（agent_node 会原样展示给用户），
    因此文案面向买家，内部细节（code / model / 分数）留给 API 的 rerank 字段。"""
    if not results:
        return "抱歉，暂时没有检索到匹配的商品，能否再描述一下您的需求（手型、连接方式、预算）？"
    head = ("为您从商品知识库匹配到以下商品（按关键词匹配排序）："
            if degraded else "为您从商品知识库检索并重排后推荐以下几款：")
    lines = [head, ""]
    for i, r in enumerate(results, start=1):
        lines.append(f"{i}. 【{r['sku']}】{r['name']} · ¥{r['price']:.0f}")
        lines.append("   " + r["text"].replace("\n", " ")[:240])
    lines.append("")
    lines.append("以上参数来自商品库，可继续问我具体规格、兼容性或库存。")
    return "\n".join(lines)


async def search(
    db,
    query: str,
    top_k: int | None = None,
    recall_k: int | None = None,
    use_rerank: bool = True,
    recall_mode: str = "hybrid",
) -> dict:
    """完整链路：混合召回 →（可选）rerank 精排 → Top-K。
    返回结构同时喂给 API（展示召回前后）、评估脚本与 Agent 工具（observation）。"""
    q = (query or "").strip()
    k = max(1, min(int(top_k or settings.RAG_TOP_K), 10))
    if not q:
        return {
            "ok": False, "query": q, "code": "RAG004",
            "detail": "检索问题不能为空。", "candidates": [], "results": [],
            "observation": "请描述您想找的商品（如：适合大手的无线轻量鼠标）。",
        }

    catalog = await build_catalog(db)
    rec = await recall_hybrid(q, catalog, recall_k, mode=recall_mode)
    candidates = rec["candidates"]
    if not candidates:
        # 无命中时给前 K 个兜底，避免模型收到空 observation
        candidates = [{**d, "recall_rank": i + 1, "recall_score": 0.0,
                       "kw_rank": None, "vec_rank": None, "recall_source": "fallback"}
                      for i, d in enumerate(catalog[:max(k, 3)])]

    texts = [c["text"] for c in candidates]
    if use_rerank and settings.RAG_ENABLED:
        rr = await get_reranker().rerank(q, texts, top_n=k)
    else:
        rr = {
            "ok": False, "degraded": True, "code": "RAG000",
            "detail": "本次未启用 Reranker（手动关闭或未配置），使用一阶段召回顺序。",
            "model": settings.SILICONFLOW_RERANK_MODEL, "cached": False,
            "latency_ms": None,
            "results": [{"index": i, "score": None, "text": t}
                        for i, t in enumerate(texts[:k])],
        }

    results: list[dict] = []
    for final_rank, item in enumerate(rr["results"], start=1):
        # reranker 的 index 是送入 texts 的 0-based 位置，直接按位置还原
        idx = int(item.get("index", -1))
        src = candidates[idx] if 0 <= idx < len(candidates) else None
        if not src:
            continue
        results.append({
            **{key: src[key] for key in
               ("sku", "name", "brand", "category", "category_cn", "price", "stock", "text")},
            "rank": final_rank,
            "score": item.get("score"),
            "recall_rank": src["recall_rank"],
            "recall_score": src["recall_score"],
            "moved": src["recall_rank"] - final_rank,
        })

    return {
        "ok": True,
        "query": q,
        "catalog_size": len(catalog),
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
            {key: c[key] for key in
             ("sku", "name", "category_cn", "price", "recall_rank", "recall_score")}
            | {"kw_rank": c.get("kw_rank"), "vec_rank": c.get("vec_rank"),
               "recall_source": c.get("recall_source")}
            for c in candidates
        ],
        "results": [
            {key: r[key] for key in
             ("sku", "name", "category_cn", "price", "rank", "score",
              "recall_rank", "recall_score", "moved", "text")}
            for r in results
        ],
        "observation": _observation(results, bool(rr.get("degraded")),
                                    rr.get("model") or "Reranker"),
    }


async def rerank_texts(query: str, documents: list[str], top_n: int | None = None) -> dict:
    """裸重排：给设置页做「随手点着重玩」的测试入口，与商品库无关。"""
    return await get_reranker().rerank(query, documents, top_n=top_n)
