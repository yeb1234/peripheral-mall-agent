"""
对话语义去重（防重复提交 / 连点 / 网络重试把同一句话跑两遍）。

两层判定：
1. 指纹命中：归一化后完全一样 → 直接复用上次回复；
2. 近义命中：字符 3-gram Jaccard ≥ 阈值（默认 0.9）且长度接近 —— 覆盖
   「KB-60HE 有货吗」/「KB-60HE 还有货么」这类同义重问。

只缓存「纯读」的回复：带确认卡 / 报错 / 转人工的回复不缓存，
否则用户重新提问会拿到一张已经过期的卡。
"""
from __future__ import annotations

import hashlib
import json
import re

from app.config import settings
from app.core.cache import get_cache, get_json
from app.core.security import normalize_text

_IDX_LIMIT = 8
_PUNCT = re.compile(r"[^\w\u4e00-\u9fff]+")


def fingerprint(text: str) -> str:
    norm = _PUNCT.sub("", normalize_text(text).lower())
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


def _shingles(text: str, n: int = 2) -> set[str]:
    norm = _PUNCT.sub("", normalize_text(text).lower())
    if len(norm) <= n:
        return {norm}
    return {norm[i:i + n] for i in range(len(norm) - n + 1)}


def similarity(a: str, b: str) -> float:
    """短问句用 2-gram：Jaccard 看整体重合，containment 看短句是否被长句覆盖。

    只用 Jaccard 时「KB-60HE 有货吗」/「KB60HE 还有货吗」只有 0.5 左右，
    阈值一旦放宽又会把「键盘有货吗」和「鼠标有货吗」判成同一句。
    两项加权后：近义重问 ≈0.78，换一个品类 ≈0.44，分得开。
    """
    sa, sb = _shingles(a), _shingles(b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    jac = inter / len(sa | sb)
    cont = inter / min(len(sa), len(sb))
    return round(0.55 * jac + 0.45 * cont, 4)


def _key(session_id: str, fp: str) -> str:
    return f"p4:dedup:{session_id}:{fp}"


def _idx_key(session_id: str) -> str:
    return f"p4:dedup:idx:{session_id}"


async def lookup(session_id: str, text: str) -> dict | None:
    """命中返回 {reply, meta, fp, sim}；未命中 None。"""
    if not session_id or not (text or "").strip():
        return None
    cache = get_cache()
    fp = fingerprint(text)
    raw = await cache.get(_key(session_id, fp))
    if raw:
        data = json.loads(raw)
        return {"reply": data["reply"], "meta": data.get("meta") or {}, "fp": fp, "sim": 1.0}

    idx = await get_json(_idx_key(session_id)) or []
    best = None
    for item in idx:
        sim = similarity(text, item.get("text", ""))
        if sim >= settings.DEDUP_SIM and abs(len(text) - len(item.get("text", ""))) <= 30:
            if best is None or sim > best[0]:
                best = (sim, item)
    if not best:
        return None
    raw = await cache.get(best[1]["key"])
    if not raw:
        return None
    data = json.loads(raw)
    return {"reply": data["reply"], "meta": data.get("meta") or {}, "fp": best[1].get("fp"), "sim": round(best[0], 3)}


async def remember(session_id: str, text: str, reply: str, meta: dict) -> None:
    """缓存可复用的只读回复。写操作卡 / 错误 / 转人工不进缓存。"""
    if not session_id or not reply:
        return
    if meta.get("confirmation") or meta.get("handoff") or meta.get("error_code") or meta.get("need_bind"):
        return
    cache = get_cache()
    fp = fingerprint(text)
    key = _key(session_id, fp)
    await cache.set(key, json.dumps({"reply": reply, "meta": meta, "ts": 0}, ensure_ascii=False),
                    ex=settings.DEDUP_WINDOW)
    idx = await get_json(_idx_key(session_id)) or []
    idx = [x for x in idx if x.get("fp") != fp]
    idx.append({"fp": fp, "key": key, "text": (text or "")[:120]})
    idx = idx[-_IDX_LIMIT:]
    await cache.set(_idx_key(session_id), json.dumps(idx, ensure_ascii=False), ex=settings.DEDUP_WINDOW)


async def sweep(session_id: str) -> None:
    """清掉某会话的去重索引（会话重置 / 身份切换时用）。"""
    cache = get_cache()
    await cache.delete(_idx_key(session_id))
