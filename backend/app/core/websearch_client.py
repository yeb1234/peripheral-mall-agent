"""
联网搜索编排层（多源 router）。

分工：百炼 EnhancedSearch MCP 打中文/国内语境，Tavily 打英文/海外源；
- auto：按问题语种选主源，主源结果不足或失败时自动补次源；
- both：两源并发，合并去重后再重排；
- bailian / tavily：只用指定源（排障与设置页试搜用）。

对外契约不变：get_websearch().search(...) 返回 dict（ok/degraded/code/detail/
query/results/cached/latency_ms/observation），永不抛异常；新增 providers 明细。

可靠性：任一源失败只在结果里标记 degraded 并回落另一源；两源都失败才整体降级，
客服链路照常答复本店资料。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from typing import Any

from app.config import settings
from app.core.cache import cache_json, get_json
from app.core.errors import ErrorCode
from app.core.websearch_providers import (
    ProviderResult,
    available_providers,
    get_provider,
    merge_results,
)

logger = logging.getLogger(__name__)

_STATS: dict[str, Any] = {
    "total_calls": 0,
    "total_success": 0,
    "total_degraded": 0,
    "total_cached": 0,
    "total_fallback": 0,
    "cached_results": 0,
    "last_latency_ms": None,
    "avg_latency_ms": None,
    "last_code": None,
    "last_detail": None,
    "by_provider": {"bailian": {"calls": 0, "ok": 0, "failed": 0},
                    "tavily": {"calls": 0, "ok": 0, "failed": 0}},
    "_latency_sum": 0.0,
    "_latency_n": 0,
}
_STATS_LOCK = asyncio.Lock()

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_MODES = {"auto", "bailian", "tavily", "both"}


def _cjk_ratio(text: str) -> float:
    compact = [c for c in (text or "") if not c.isspace()]
    if not compact:
        return 0.0
    return len(_CJK_RE.findall("".join(compact))) / len(compact)


def _pick_primary(query: str, mode: str) -> tuple[str, str | None]:
    """返回 (主源列表, 次源)；主源列表用于并发，次源用于结果不足时补。"""
    if mode == "both":
        return ["bailian", "tavily"], None
    if mode in ("bailian", "tavily"):
        return [mode], None
    # auto：中文问题先打百炼（国内源覆盖好），英文/外文先打 Tavily（海外源覆盖好）
    if _cjk_ratio(query) >= 0.10:
        return ["bailian"], "tavily"
    return ["tavily"], "bailian"


def _cache_key(query: str, mode: str, category: str, time_range: str,
               top_k: int, rerank: bool) -> str:
    raw = f"{query}|{mode}|{category}|{time_range}|{top_k}|{int(rerank)}"
    return "ws:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()


class WebSearchClient:
    @property
    def available(self) -> bool:
        return bool(settings.WEBSEARCH_ENABLED and available_providers())

    @property
    def tool_name(self) -> str:
        return settings.WEBSEARCH_TOOL

    @property
    def source_mode(self) -> str:
        mode = (settings.WEBSEARCH_SOURCE or "auto").strip().lower()
        return mode if mode in _MODES else "auto"

    def public(self) -> dict:
        return {
            "enabled": bool(settings.WEBSEARCH_ENABLED),
            "configured": bool(settings.WEBSEARCH_API_KEY),
            "has_key": bool(settings.WEBSEARCH_API_KEY),
            "api_key": (settings.WEBSEARCH_API_KEY[:6] + "****"
                        if len(settings.WEBSEARCH_API_KEY) > 6 else "****"
                        if settings.WEBSEARCH_API_KEY else ""),
            "mcp_url": settings.WEBSEARCH_MCP_URL,
            "tool": self.tool_name,
            "max_results": settings.WEBSEARCH_MAX_RESULTS,
            "available": self.available,
            "source_mode": self.source_mode,
            "available_providers": available_providers(),
            "providers": {
                "bailian": {
                    "label": "阿里云百炼 EnhancedSearch",
                    "enabled": bool(settings.WEBSEARCH_ENABLED),
                    "configured": bool(settings.WEBSEARCH_API_KEY),
                    "endpoint": settings.WEBSEARCH_MCP_URL,
                    "tool": settings.WEBSEARCH_TOOL,
                },
                "tavily": {
                    "label": "Tavily Search",
                    "enabled": bool(settings.WEBSEARCH_ENABLED),
                    "configured": bool(settings.TAVILY_API_KEY),
                    "api_key": (settings.TAVILY_API_KEY[:6] + "****"
                                if len(settings.TAVILY_API_KEY) > 6 else "****"
                                if settings.TAVILY_API_KEY else ""),
                    "search_depth": settings.TAVILY_SEARCH_DEPTH,
                },
            },
            "rerank": {
                "enabled": bool(settings.WEBSEARCH_RERANK),
                "model": settings.SILICONFLOW_RERANK_MODEL,
                "configured": bool(settings.SILICONFLOW_API_KEY),
            },
            **{k: v for k, v in _STATS.items() if not k.startswith("_")},
        }

    async def _run_provider(self, name: str, query: str, k: int,
                            category: str | None, time_range: str | None) -> ProviderResult:
        provider = get_provider(name)
        if provider is None:
            return ProviderResult(name, skipped=True, detail=f"未知检索源 {name}")
        res = await provider.search(
            query, top_k=k, category=category, time_range=time_range,
        )
        if not res.skipped:
            async with _STATS_LOCK:
                slot = _STATS["by_provider"].setdefault(
                    name, {"calls": 0, "ok": 0, "failed": 0})
                slot["calls"] += 1
                slot["ok" if res.ok else "failed"] += 1
        return res

    @staticmethod
    def _combined_code(results: list[ProviderResult]) -> tuple[str | None, str | None]:
        """多源全失败时归并错误码：鉴权问题优先暴露，其次不可用，再次解析。"""
        codes = [r.code for r in results if r.code]
        for want in (ErrorCode.WEB_AUTH.value, ErrorCode.WEB_UNAVAILABLE.value,
                     ErrorCode.WEB_PARSE.value):
            if want in codes:
                return want, "；".join(
                    f"{r.provider}: {r.detail or r.code}" for r in results if r.detail
                )[:220]
        return (codes[0] if codes else None), None

    async def _degraded(self, code: str | None, detail: str | None, latency_ms: float,
                        query: str, providers: list[ProviderResult]) -> dict:
        async with _STATS_LOCK:
            _STATS["total_degraded"] += 1
            _STATS["last_code"] = code
            _STATS["last_detail"] = detail
        return {
            "ok": False, "degraded": True, "code": code, "detail": detail,
            "query": query, "results": [], "latency_ms": round(latency_ms, 1),
            "providers": [p.as_dict() for p in providers],
            "observation": "抱歉，联网检索暂时不可用，我先根据本店商品资料为您解答。",
        }

    async def _rerank(self, query: str, items: list[dict], k: int) -> tuple[list[dict], dict]:
        """多源合并后重排；reranker 不可用/失败时保持原序（degraded 只做标注）。"""
        if not (settings.WEBSEARCH_RERANK and items):
            return items, {"used": False}
        try:
            from app.core.rerank_client import get_reranker
            reranker = get_reranker()
            if not reranker.available:
                return items, {"used": False, "reason": "reranker 未配置"}
            docs = [f"{it.get('title','')}\n{it.get('snippet','')}" for it in items]
            out = await reranker.rerank(query, docs, top_n=k)
            if not out.get("ok"):
                return items[:k], {"used": False, "degraded": True,
                                   "reason": out.get("detail")}
            ranked = []
            for row in out.get("results", []):
                idx = row.get("index")
                if isinstance(idx, int) and 0 <= idx < len(items):
                    ranked.append({**items[idx], "rerank_score": row.get("score")})
            return (ranked or items[:k]), {"used": True, "model": out.get("model")}
        except Exception as e:  # noqa: BLE001 — 重排永远是增强，不是依赖
            logger.warning("[WEB] 重排降级：%s %s", type(e).__name__, str(e)[:120])
            return items[:k], {"used": False, "degraded": True,
                               "reason": f"{type(e).__name__}"}

    def _observation(self, query: str, items: list[dict], top_k: int,
                     providers: list[ProviderResult]) -> str:
        if not items:
            return "公开网络上暂未检索到与您问题直接相关的内容，我先根据本店商品资料为您解答。"
        used = [p.provider for p in providers if p.ok and p.results]
        src_label = {
            "bailian": "百炼", "tavily": "Tavily",
        }
        scope = "、".join(src_label.get(n, n) for n in used) or "公开网络"
        lines = [
            f"以下信息来自公开网络检索（{scope}），仅供参考；本店商品的参数、价格与库存以页面为准：",
            "",
        ]
        for i, it in enumerate(items[:top_k], start=1):
            title = it.get("title") or "（无标题）"
            host = f" · {it['hostname']}" if it.get("hostname") else ""
            tag = src_label.get(it.get("provider", ""), it.get("provider", ""))
            snippet = " ".join(str(it.get("snippet", "")).split())[:160]
            lines.append(f"{i}. {title}{host}{f'（{tag}）' if tag else ''}")
            if snippet:
                lines.append(f"   摘要：{snippet}")
            if it.get("url"):
                lines.append(f"   来源：{it['url']}")
        others = [o for it in items for o in (it.get("other_sources") or [])]
        if others:
            lines.append("")
            lines.append("（部分条目在多个检索源同时命中，已合并去重。）")
        lines.append("")
        lines.append("如需选购本店商品，我可以继续为您匹配具体型号与规格。")
        return "\n".join(lines)

    async def search(
        self,
        query: str,
        *,
        category: str | None = None,
        time_range: str | None = None,
        top_k: int | None = None,
        source: str | None = None,
    ) -> dict:
        """联网检索（多源）。返回 dict 含 ok/degraded/observation/results，永不抛异常。"""
        q = (query or "").strip()
        t0 = time.perf_counter()
        async with _STATS_LOCK:
            _STATS["total_calls"] += 1

        if not q:
            bad = await self._degraded(ErrorCode.WEB_BAD_INPUT.value, "检索问题不能为空。",
                                       (time.perf_counter() - t0) * 1000, q, [])
            bad["observation"] = "请描述您想了解的外部信息，例如「磁轴键盘是什么原理」。"
            return bad

        mode = (source or self.source_mode or "auto").strip().lower()
        if mode not in _MODES:
            mode = self.source_mode
        if not settings.WEBSEARCH_ENABLED:
            return await self._degraded(
                ErrorCode.WEB_AUTH.value, "WEBSEARCH_ENABLED=false，已跳过联网检索。",
                (time.perf_counter() - t0) * 1000, q, [])
        if not available_providers():
            return await self._degraded(
                ErrorCode.WEB_AUTH.value,
                "未配置任何检索源（WEBSEARCH_API_KEY / TAVILY_API_KEY 均为空）。",
                (time.perf_counter() - t0) * 1000, q, [])

        k = max(1, min(int(top_k or settings.WEBSEARCH_MAX_RESULTS), 10))
        rerank_on = bool(settings.WEBSEARCH_RERANK)
        cache_key = _cache_key(q, mode, category or "", time_range or "", k, rerank_on)

        cached = await get_json(cache_key)
        if isinstance(cached, dict) and cached.get("results") is not None:
            async with _STATS_LOCK:
                _STATS["total_cached"] += 1
                _STATS["cached_results"] = len(cached["results"])
            latency = round((time.perf_counter() - t0) * 1000, 1)
            return {
                "ok": True, "degraded": False, "code": None, "detail": None,
                "query": q, "results": cached["results"], "cached": True,
                "mode": cached.get("mode", mode),
                "latency_ms": latency,
                "providers": cached.get("providers", []),
                "rerank": cached.get("rerank", {"used": False}),
                "observation": self._observation(q, cached["results"], k, []),
            }

        primary, secondary = _pick_primary(q, mode)
        primary = [p for p in primary if p in available_providers()]
        if secondary and secondary not in available_providers():
            secondary = None
        if not primary:
            primary, secondary = ([secondary], None) if secondary else ([], None)
        if not primary:
            return await self._degraded(
                ErrorCode.WEB_AUTH.value, "所选检索源未配置或不可用。",
                (time.perf_counter() - t0) * 1000, q, [])

        results: list[ProviderResult] = []
        runs = await asyncio.gather(*[
            self._run_provider(name, q, k, category, time_range) for name in primary
        ])
        results.extend(runs)

        # auto 模式：主源结果不足或失败时补次源（不重复打已跑过的源）
        weak = (not any(r.ok and r.results for r in runs)
                or sum(len(r.results) for r in runs if r.ok) < settings.WEBSEARCH_MIN_RESULTS)
        if secondary and weak and settings.WEBSEARCH_FALLBACK and secondary not in primary:
            async with _STATS_LOCK:
                _STATS["total_fallback"] += 1
            extra = await self._run_provider(secondary, q, k, category, time_range)
            results.append(extra)

        groups = [r.results for r in results if r.ok and r.results]
        merged = merge_results(groups)

        if not merged:
            code, detail = self._combined_code(results)
            return await self._degraded(code, detail, (time.perf_counter() - t0) * 1000,
                                        q, results)

        reranked, rerank_meta = await self._rerank(q, merged, k)
        final = reranked[:k]

        latency = round((time.perf_counter() - t0) * 1000, 1)
        payload = {"results": final, "mode": mode,
                   "providers": [p.as_dict() for p in results], "rerank": rerank_meta}
        await cache_json(cache_key, payload, ex=settings.WEBSEARCH_CACHE_TTL)

        async with _STATS_LOCK:
            _STATS["total_success"] += 1
            _STATS["last_code"] = None
            _STATS["last_detail"] = None
            _STATS["last_latency_ms"] = latency
            _STATS["cached_results"] = len(final)
            _STATS["_latency_sum"] += latency
            _STATS["_latency_n"] += 1
            _STATS["avg_latency_ms"] = round(_STATS["_latency_sum"] / _STATS["_latency_n"], 1)

        partial = [p for p in results if not p.ok and not p.skipped]
        return {
            "ok": True,
            "degraded": bool(partial) and not any(p.ok for p in results),
            "code": partial[0].code if partial else None,
            "detail": partial[0].detail if partial else None,
            "query": q, "results": final, "cached": False, "mode": mode,
            "latency_ms": latency,
            "providers": [p.as_dict() for p in results],
            "rerank": rerank_meta,
            "observation": self._observation(q, final, k, results),
        }


_CLIENT: WebSearchClient | None = None


def get_websearch() -> WebSearchClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = WebSearchClient()
    return _CLIENT
