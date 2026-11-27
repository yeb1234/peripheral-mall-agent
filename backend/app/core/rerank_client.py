"""
SiliconFlow（硅基流动）Reranker 客户端。

定位：RAG 二阶段精排。把「一阶段召回的候选文档」连同用户 query 发给
`POST {base}/v1/rerank`，模型 `BAAI/bge-reranker-v2-m3` 返回每个文档的
relevance_score，按分数排序后取 Top-K 注入 Agent 的 observations。

可靠性设计（和这个项目其余部分的口径一致）：
- 全程 async httpx，不阻塞事件循环；单次超时可配（RAG_TIMEOUT）。
- 429 / 5xx 做有限次指数退避重试，避免免费额度抖动直接打穿链路。
- 相同 query + 候选 + 模型的结果进缓存（RAG_CACHE_TTL），压测/演示不重复烧 token。
- **任何失败都降级**：无 key、鉴权失败、超时、限流、返回解析失败，
  一律返回一阶段召回顺序（degraded=True + RAG0xx 错误码），客服对话绝不中断。
  这是「检索增强」而非「检索依赖」。
- 统计（调用/成功/降级/缓存命中/平均延迟）通过 status() 暴露给设置页。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any

import httpx

from app.config import settings
from app.core.cache import cache_json, get_json
from app.core.errors import AppError, ErrorCode

_STATS: dict[str, Any] = {
    "total_calls": 0,
    "total_success": 0,
    "total_degraded": 0,
    "total_cached": 0,
    "total_retries": 0,
    "last_latency_ms": None,
    "avg_latency_ms": None,
    "_latency_sum": 0.0,
    "_latency_n": 0,
    "last_code": None,
    "last_detail": None,
}
_STATS_LOCK = asyncio.Lock()


def _mask(key: str) -> str:
    return key[:6] + "****" if len(key) > 6 else ("****" if key else "")


def _degraded(documents: list[str], code: ErrorCode, detail: str, top_n: int,
              latency_ms: float | None = None) -> dict:
    """失败时保持一阶段顺序，让上层依然有结果可用。"""
    return {
        "ok": False,
        "degraded": True,
        "code": code.value,
        "detail": detail,
        "model": settings.SILICONFLOW_RERANK_MODEL,
        "cached": False,
        "latency_ms": latency_ms,
        "results": [
            {"index": i, "score": None, "text": d}
            for i, d in enumerate(documents[:top_n])
        ],
    }


class SiliconFlowReranker:
    """无状态客户端；所有配置从 settings 读，缓存在 core.cache（memory / Redis 通用）。"""

    @property
    def available(self) -> bool:
        return bool(settings.RAG_ENABLED and settings.SILICONFLOW_API_KEY)

    @property
    def model(self) -> str:
        return settings.SILICONFLOW_RERANK_MODEL

    def public(self) -> dict:
        """给前端的安全视图：key 一律脱敏，不回传原文。"""
        return {
            "enabled": bool(settings.RAG_ENABLED),
            "configured": bool(settings.SILICONFLOW_API_KEY),
            "has_key": bool(settings.SILICONFLOW_API_KEY),
            "api_key": _mask(settings.SILICONFLOW_API_KEY),
            "base_url": settings.SILICONFLOW_BASE_URL,
            "rerank_model": self.model,
            "embed_model": settings.SILICONFLOW_EMBED_MODEL,
            "recall_k": settings.RAG_RECALL_K,
            "top_k": settings.RAG_TOP_K,
            "cache_ttl": settings.RAG_CACHE_TTL,
            "timeout": settings.RAG_TIMEOUT,
            "available": self.available,
            **{k: v for k, v in _STATS.items() if not k.startswith("_")},
        }

    async def rerank(self, query: str, documents: list[str], top_n: int | None = None) -> dict:
        """返回统一结构；失败时 degraded=True 且 results 保持原始顺序。"""
        q = (query or "").strip()
        docs = [str(d) for d in (documents or []) if str(d).strip()]
        n = max(1, min(int(top_n or len(docs) or 1), len(docs) or 1))

        if not q or not docs:
            err = AppError(ErrorCode.RAG_BAD_INPUT)
            return _degraded(docs, ErrorCode.RAG_BAD_INPUT, err.message, n)

        async with _STATS_LOCK:
            _STATS["total_calls"] += 1

        if not self.available:
            reason = "RAG_ENABLED=false" if not settings.RAG_ENABLED else "未配置 SILICONFLOW_API_KEY"
            return await self._finish_degraded(
                docs, ErrorCode.RAG_AUTH, f"{reason}，已降级为关键词召回顺序", n, 0.0,
            )

        # 缓存键包含 query / 全部候选 / 模型 / top_n，避免不同候选集误命中
        raw = json.dumps([q, docs, self.model, n], ensure_ascii=False, sort_keys=True)
        ck = "rag:rerank:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
        try:
            cached = await get_json(ck)
            if cached and cached.get("results"):
                async with _STATS_LOCK:
                    _STATS["total_cached"] += 1
                    _STATS["total_success"] += 1
                return {**cached, "cached": True}
        except Exception:
            pass  # 缓存故障不影响主链路

        url = settings.SILICONFLOW_BASE_URL.rstrip("/") + "/rerank"
        payload = {
            "model": self.model,
            "query": q,
            "documents": docs,
            "top_n": min(n, len(docs)),
            "return_documents": False,   # 文本本地还原，省一半响应带宽
        }
        headers = {
            "Authorization": f"Bearer {settings.SILICONFLOW_API_KEY}",
            "Content-Type": "application/json",
        }

        t0 = time.perf_counter()
        attempts = max(1, int(settings.RAG_MAX_RETRIES) + 1)
        last_exc: Exception | None = None
        last_status: int | None = None

        for attempt in range(attempts):
            if attempt:
                async with _STATS_LOCK:
                    _STATS["total_retries"] += 1
                await asyncio.sleep(0.6 * (2 ** (attempt - 1)))
            try:
                async with httpx.AsyncClient(timeout=float(settings.RAG_TIMEOUT)) as client:
                    resp = await client.post(url, json=payload, headers=headers)
                last_status = resp.status_code
                if resp.status_code in (401, 403):
                    err = AppError(ErrorCode.RAG_AUTH, detail=f"HTTP {resp.status_code}")
                    return await self._finish_degraded(
                        docs, ErrorCode.RAG_AUTH, f"{err.message} (HTTP {resp.status_code})",
                        n, (time.perf_counter() - t0) * 1000,
                    )
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_exc = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:160]}")
                    continue  # 有限退避重试
                if resp.status_code >= 400:
                    err = AppError(ErrorCode.RAG_UNAVAILABLE, detail=f"HTTP {resp.status_code}: {resp.text[:160]}")
                    return await self._finish_degraded(
                        docs, ErrorCode.RAG_UNAVAILABLE, err.message,
                        n, (time.perf_counter() - t0) * 1000,
                    )

                data = resp.json()
                raw_results = data.get("results") if isinstance(data, dict) else None
                if not isinstance(raw_results, list):
                    err = AppError(ErrorCode.RAG_PARSE, detail="响应缺少 results 数组")
                    return await self._finish_degraded(
                        docs, ErrorCode.RAG_PARSE, err.message,
                        n, (time.perf_counter() - t0) * 1000,
                    )

                parsed: list[dict] = []
                for item in raw_results:
                    try:
                        idx = int(item["index"])
                        score = float(item.get("relevance_score", 0.0))
                        if 0 <= idx < len(docs):
                            parsed.append({"index": idx, "score": round(score, 6), "text": docs[idx]})
                    except (KeyError, TypeError, ValueError):
                        continue
                if not parsed:
                    err = AppError(ErrorCode.RAG_PARSE, detail="results 中无可用 index")
                    return await self._finish_degraded(
                        docs, ErrorCode.RAG_PARSE, err.message,
                        n, (time.perf_counter() - t0) * 1000,
                    )
                parsed.sort(key=lambda x: x["score"], reverse=True)
                parsed = parsed[:n]

                latency = round((time.perf_counter() - t0) * 1000, 1)
                out = {
                    "ok": True, "degraded": False, "code": None, "detail": None,
                    "model": self.model, "cached": False, "latency_ms": latency,
                    "results": parsed,
                }
                try:
                    await cache_json(ck, out, ex=int(settings.RAG_CACHE_TTL))
                except Exception:
                    pass
                async with _STATS_LOCK:
                    _STATS["total_success"] += 1
                    _STATS["last_code"] = None
                    _STATS["last_detail"] = None
                    _STATS["last_latency_ms"] = latency
                    _STATS["_latency_sum"] += latency
                    _STATS["_latency_n"] += 1
                    _STATS["avg_latency_ms"] = round(
                        _STATS["_latency_sum"] / _STATS["_latency_n"], 1
                    )
                return out

            except (httpx.TimeoutException, httpx.RequestError) as e:
                last_exc = e
                continue
            except Exception as e:  # JSON 解析异常等
                last_exc = e
                err = AppError(ErrorCode.RAG_PARSE, detail=f"{type(e).__name__}: {e}")
                return await self._finish_degraded(
                    docs, ErrorCode.RAG_PARSE, err.message,
                    n, (time.perf_counter() - t0) * 1000,
                )

        detail = f"{type(last_exc).__name__ if last_exc else 'HTTP'}: {last_exc or last_status}"
        err = AppError(ErrorCode.RAG_UNAVAILABLE, detail=detail)
        return await self._finish_degraded(
            docs, ErrorCode.RAG_UNAVAILABLE, f"{err.message}（{detail}）",
            n, (time.perf_counter() - t0) * 1000,
        )

    async def _finish_degraded(self, docs: list[str], code: ErrorCode, detail: str,
                               n: int, latency_ms: float) -> dict:
        async with _STATS_LOCK:
            _STATS["total_degraded"] += 1
            _STATS["last_code"] = code.value
            _STATS["last_detail"] = detail
        return _degraded(docs, code, detail, n, round(latency_ms, 1) if latency_ms else None)


_reranker: SiliconFlowReranker | None = None


def get_reranker() -> SiliconFlowReranker:
    global _reranker
    if _reranker is None:
        _reranker = SiliconFlowReranker()
    return _reranker
