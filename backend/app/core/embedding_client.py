"""
SiliconFlow 嵌入客户端：BAAI/bge-m3（1024 维，多语言，8K 上下文）。

在 RAG 链路里的位置（与 rerank_client 组成「两个 BGE 模型都走硅基流动」）：

    一阶段召回  ├─ 关键词召回（本地 2-gram + 领域词 + SKU 加权，零成本、零网络）
                └─ 向量召回（本文件 /v1/embeddings → 余弦相似度）
                      ↓ RRF 融合
    二阶段精排  →  /v1/rerank（bge-reranker-v2-m3）

可靠性口径与 rerank_client 完全一致：
- 文档向量按 「模型 + 文本」哈希缓存，落盘 gzip JSON，重启不重复烧 token；
- 无 key / 超时 / 429 / 解析失败一律 degraded=True 且不抛异常，
  上层自动退回纯关键词召回 —— 检索增强 ≠ 检索依赖；
- 统计（调用/成功/降级/缓存命中/延迟/token）通过 public() 暴露给设置页和评估脚本。
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.core.errors import AppError, ErrorCode

_STATS: dict[str, Any] = {
    "total_calls": 0,
    "total_success": 0,
    "total_degraded": 0,
    "total_cached": 0,
    "total_retries": 0,
    "total_tokens": 0,
    "cached_docs": 0,
    "dim": None,
    "last_latency_ms": None,
    "avg_latency_ms": None,
    "last_code": None,
    "last_detail": None,
    "_latency_sum": 0.0,
    "_latency_n": 0,
}

_CACHE: dict[str, list[float]] = {}
_CACHE_PATH = Path(__file__).resolve().parents[2] / ".embed_cache.json.gz"
_CACHE_LOADED = False
_CACHE_DIRTY = False
_STATS_LOCK = asyncio.Lock()
_CACHE_LOCK = asyncio.Lock()

BATCH_SIZE = 32


def _key(text: str, model: str) -> str:
    return hashlib.sha256(f"{model}\n{text}".encode("utf-8")).hexdigest()


def _load_cache() -> None:
    global _CACHE_LOADED
    if _CACHE_LOADED:
        return
    _CACHE_LOADED = True
    try:
        if _CACHE_PATH.is_file():
            with gzip.open(_CACHE_PATH, "rt", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                _CACHE.update({k: v for k, v in data.items() if isinstance(v, list)})
    except Exception:
        pass  # 缓存损坏不影响检索，重新 embed 即可


def _flush_sync() -> None:
    with gzip.open(_CACHE_PATH, "wt", encoding="utf-8") as f:
        json.dump(_CACHE, f, separators=(",", ":"))


async def _flush() -> None:
    """落盘用 to_thread，避免 400+ 条 1024 维向量序列化阻塞事件循环。"""
    global _CACHE_DIRTY
    async with _CACHE_LOCK:
        if not _CACHE_DIRTY or not _CACHE:
            return
        try:
            await asyncio.to_thread(_flush_sync)
            _CACHE_DIRTY = False
        except Exception:
            pass  # 落盘失败只影响下次启动，不影响本次检索


class SiliconFlowEmbedder:
    @property
    def available(self) -> bool:
        return bool(settings.RAG_ENABLED and settings.SILICONFLOW_API_KEY)

    @property
    def model(self) -> str:
        return settings.SILICONFLOW_EMBED_MODEL

    def public(self) -> dict:
        _load_cache()
        return {
            "enabled": bool(settings.RAG_ENABLED),
            "configured": bool(settings.SILICONFLOW_API_KEY),
            "has_key": bool(settings.SILICONFLOW_API_KEY),
            "api_key": (settings.SILICONFLOW_API_KEY[:6] + "****"
                        if len(settings.SILICONFLOW_API_KEY) > 6 else "****"
                        if settings.SILICONFLOW_API_KEY else ""),
            "base_url": settings.SILICONFLOW_BASE_URL,
            "embed_model": self.model,
            "dim": _STATS["dim"],
            "cached_docs": len(_CACHE),
            "available": self.available,
            **{k: v for k, v in _STATS.items() if not k.startswith("_")},
        }

    async def _degraded(self, code: ErrorCode, detail: str, latency_ms: float) -> dict:
        async with _STATS_LOCK:
            _STATS["total_degraded"] += 1
            _STATS["last_code"] = code.value
            _STATS["last_detail"] = detail
        return {
            "ok": False, "degraded": True, "code": code.value, "detail": detail,
            "model": self.model, "vectors": None, "dim": None,
            "cached": 0, "latency_ms": round(latency_ms, 1),
        }

    async def embed(self, texts: list[str], *, persist: bool = True) -> dict:
        """批量嵌入。返回与 texts 等长的 vectors（失败则为 None + degraded=True）。"""
        t0 = time.perf_counter()
        _load_cache()
        async with _STATS_LOCK:
            _STATS["total_calls"] += 1

        clean = [str(t).strip() for t in (texts or [])]
        if not clean or any(not t for t in clean):
            err = AppError(ErrorCode.RAG_BAD_INPUT)
            return await self._degraded(
                ErrorCode.RAG_BAD_INPUT, err.message, (time.perf_counter() - t0) * 1000
            )

        if not self.available:
            reason = "RAG_ENABLED=false" if not settings.RAG_ENABLED else "未配置 SILICONFLOW_API_KEY"
            return await self._degraded(
                ErrorCode.RAG_AUTH, f"{reason}，已降级为关键词召回",
                (time.perf_counter() - t0) * 1000,
            )

        keys = [_key(t, self.model) for t in clean]
        vectors: list[list[float] | None] = [_CACHE.get(k) for k in keys]
        miss_idx = [i for i, v in enumerate(vectors) if v is None]
        cached_n = len(clean) - len(miss_idx)
        if cached_n:
            async with _STATS_LOCK:
                _STATS["total_cached"] += cached_n

        url = settings.SILICONFLOW_BASE_URL.rstrip("/") + "/embeddings"
        headers = {
            "Authorization": f"Bearer {settings.SILICONFLOW_API_KEY}",
            "Content-Type": "application/json",
        }
        global _CACHE_DIRTY
        new_tokens = 0

        for start in range(0, len(miss_idx), BATCH_SIZE):
            batch_idx = miss_idx[start:start + BATCH_SIZE]
            batch_texts = [clean[i] for i in batch_idx]
            payload = {"model": self.model, "input": batch_texts, "encoding_format": "float"}
            attempts = max(1, int(settings.RAG_MAX_RETRIES) + 1)
            last_exc: Exception | None = None
            done = False

            for attempt in range(attempts):
                if attempt:
                    async with _STATS_LOCK:
                        _STATS["total_retries"] += 1
                    await asyncio.sleep(0.6 * (2 ** (attempt - 1)))
                try:
                    async with httpx.AsyncClient(timeout=float(settings.RAG_TIMEOUT)) as client:
                        resp = await client.post(url, json=payload, headers=headers)
                    if resp.status_code in (401, 403):
                        return await self._degraded(
                            ErrorCode.RAG_AUTH, f"HTTP {resp.status_code} 鉴权失败",
                            (time.perf_counter() - t0) * 1000,
                        )
                    if resp.status_code == 429 or resp.status_code >= 500:
                        last_exc = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:120]}")
                        continue
                    if resp.status_code >= 400:
                        return await self._degraded(
                            ErrorCode.RAG_UNAVAILABLE, f"HTTP {resp.status_code}: {resp.text[:120]}",
                            (time.perf_counter() - t0) * 1000,
                        )
                    data = resp.json()
                    rows = data.get("data") if isinstance(data, dict) else None
                    if not isinstance(rows, list) or len(rows) != len(batch_texts):
                        return await self._degraded(
                            ErrorCode.RAG_PARSE, "embeddings 响应缺少 data 或条数不匹配",
                            (time.perf_counter() - t0) * 1000,
                        )
                    # 按 index 还原，避免服务端乱序
                    by_idx = {int(r["index"]): r["embedding"] for r in rows}
                    for j, i in enumerate(batch_idx):
                        vec = by_idx.get(j)
                        if not isinstance(vec, list) or not vec:
                            return await self._degraded(
                                ErrorCode.RAG_PARSE, "embedding 向量为空或缺少 index",
                                (time.perf_counter() - t0) * 1000,
                            )
                        vectors[i] = [float(x) for x in vec]
                        if persist:
                            _CACHE[keys[i]] = vectors[i]  # type: ignore[assignment]
                    new_tokens += int((data.get("usage") or {}).get("total_tokens") or 0)
                    done = True
                    break
                except (httpx.TimeoutException, httpx.RequestError) as e:
                    last_exc = e
                    continue
                except Exception as e:
                    return await self._degraded(
                        ErrorCode.RAG_PARSE, f"{type(e).__name__}: {e}",
                        (time.perf_counter() - t0) * 1000,
                    )

            if not done:
                return await self._degraded(
                    ErrorCode.RAG_UNAVAILABLE,
                    f"嵌入请求失败：{type(last_exc).__name__ if last_exc else 'HTTP'}: {last_exc}",
                    (time.perf_counter() - t0) * 1000,
                )

        if persist and miss_idx:
            _CACHE_DIRTY = True
            await _flush()

        out_vectors = [v for v in vectors if v is not None]
        dim = len(out_vectors[0]) if out_vectors else None
        latency = round((time.perf_counter() - t0) * 1000, 1)
        async with _STATS_LOCK:
            _STATS["total_success"] += 1
            _STATS["total_tokens"] += new_tokens
            _STATS["dim"] = dim or _STATS["dim"]
            _STATS["cached_docs"] = len(_CACHE)
            _STATS["last_code"] = None
            _STATS["last_detail"] = None
            _STATS["last_latency_ms"] = latency
            _STATS["_latency_sum"] += latency
            _STATS["_latency_n"] += 1
            _STATS["avg_latency_ms"] = round(_STATS["_latency_sum"] / _STATS["_latency_n"], 1)
        return {
            "ok": True, "degraded": False, "code": None, "detail": None,
            "model": self.model, "vectors": vectors, "dim": dim,
            "cached": cached_n, "new": len(miss_idx), "tokens": new_tokens,
            "latency_ms": latency,
        }


_EMBEDDER: SiliconFlowEmbedder | None = None


def get_embedder() -> SiliconFlowEmbedder:
    global _EMBEDDER
    if _EMBEDDER is None:
        _EMBEDDER = SiliconFlowEmbedder()
    return _EMBEDDER


def cosine(a: list[float], b: list[float]) -> float:
    """纯 Python 余弦（未装 numpy 也能跑）；向量范数在调用方预计算更快。"""
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / ((na ** 0.5) * (nb ** 0.5))
