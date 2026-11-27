"""
多源联网检索 Provider 层。

设计分工（与《电商客服 Agent · 中小企业加固版设计》的口径一致）：
- 百炼 EnhancedSearch MCP：中文 / 国内语境强，适合"磁轴是什么原理""国产品牌新品"这类问题；
- Tavily：英文 / 海外源强，适合 "Hall Effect keyboard switch"、"RTINGS review" 这类问题；
- 两者结果统一成同一结构，交由 router 合并、去重、可选重排。

可靠性口径与本项目其余外部依赖一致：任何 provider 失败都只返回
degraded + 错误码，绝不抛异常穿透到客服链路。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from app.config import settings
from app.core.errors import ErrorCode

logger = logging.getLogger(__name__)

# 允许透传给百炼 MCP 的分类 / 时间范围白名单（模型不可信，先在代码层收口）
_CATEGORIES = {"finance", "law", "medical", "internet", "tax",
               "news_province", "news_center"}
_TIME_RANGES = {"OneDay", "OneWeek", "OneMonth", "OneYear", "NoLimit"}


@dataclass
class ProviderResult:
    """单个检索源的一次调用结果（统一结构，不含业务话术）。"""

    provider: str
    ok: bool = False
    degraded: bool = True
    code: str | None = None
    detail: str | None = None
    results: list[dict] = field(default_factory=list)
    latency_ms: float | None = None
    skipped: bool = False   # True = 未启用 / 未配置，属于"没跑"而不是"跑挂"

    def as_dict(self) -> dict:
        return {
            "provider": self.provider, "ok": self.ok, "degraded": self.degraded,
            "code": self.code, "detail": self.detail,
            "count": len(self.results), "latency_ms": self.latency_ms,
            "skipped": self.skipped,
        }


def _item(title: str, url: str, hostname: str, snippet: str, provider: str,
          score: float | None = None) -> dict | None:
    title = (title or "").strip()
    url = (url or "").strip()
    hostname = (hostname or "").strip()
    snippet = (snippet or "").strip()
    if not (title or url or snippet):
        return None
    if not hostname and url:
        try:
            hostname = urlparse(url).hostname or ""
        except ValueError:
            hostname = ""
    return {
        "title": title or hostname or "（无标题）",
        "url": url,
        "hostname": hostname,
        "snippet": " ".join(snippet.split()),
        "provider": provider,
        **({"score": score} if score is not None else {}),
    }


def canonical_url_key(url: str) -> str:
    """把同一篇文章的不同写法归一成一把键，用于跨源去重。"""
    if not url:
        return ""
    try:
        parts = urlparse(url.strip())
        host = (parts.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        # 丢掉追踪参数，保留可能与内容相关的 query
        query = "&".join(
            p for p in (parts.query or "").split("&")
            if p and not p.lower().startswith(("utm_", "spm=", "from=", "share="))
        )
        path = (parts.path or "").rstrip("/")
        return urlunparse(("", host, path, "", query, ""))
    except ValueError:
        return url.strip().lower()


def merge_results(groups: list[list[dict]]) -> list[dict]:
    """跨源合并：同 URL 只保留信息最全的一条，并记录 other_sources。"""
    merged: dict[str, dict] = {}
    order: list[str] = []
    for group in groups:
        for it in group:
            key = canonical_url_key(it.get("url", "")) or f"t:{it.get('title','')}"
            if key in merged:
                cur = merged[key]
                # 摘要更长的那条留作正文（另一条只补来源标注）
                if len(it.get("snippet", "")) > len(cur.get("snippet", "")):
                    other = cur.get("provider")
                    it = dict(it)
                    cur_other = cur.get("other_sources") or []
                    it["other_sources"] = list({*cur_other, other} - {it.get("provider")})
                    merged[key] = it
                else:
                    others = cur.get("other_sources") or []
                    merged[key] = {
                        **cur,
                        "other_sources": sorted({*others, it.get("provider", "")} - {cur.get("provider")}),
                    }
                continue
            merged[key] = dict(it)
            order.append(key)
    return [merged[k] for k in order]


# ============ 百炼 EnhancedSearch MCP ============


def _flatten_exc(exc: BaseException) -> list[BaseException]:
    """展开 anyio ExceptionGroup 与因果链，收集全部叶子异常，便于按状态码分类。"""
    out: list[BaseException] = []
    seen: set[int] = set()

    def walk(e: BaseException) -> None:
        if id(e) in seen:
            return
        seen.add(id(e))
        out.append(e)
        for sub in getattr(e, "exceptions", []) or []:
            walk(sub)
        for nxt in (e.__cause__, e.__context__):
            if nxt is not None:
                walk(nxt)

    walk(exc)
    return out


def _exc_status(exc: BaseException) -> int | None:
    for e in _flatten_exc(exc):
        resp = getattr(e, "response", None)
        status = getattr(resp, "status_code", None)
        if isinstance(status, int):
            return status
        code = getattr(e, "status_code", None)
        if isinstance(code, int):
            return code
    return None


@asynccontextmanager
async def _mcp_session(url: str, headers: dict[str, str], timeout: float):
    """兼容 MCP SDK 1.x / 2.x 的 Streamable HTTP 会话。"""
    from mcp import ClientSession

    try:
        from mcp.client.streamable_http import streamablehttp_client as legacy_client
    except ImportError:
        legacy_client = None

    if legacy_client is not None:
        async with legacy_client(url, headers=headers, timeout=float(timeout)) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                yield session
        return

    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

    http_client = create_mcp_http_client(headers=headers, timeout=float(timeout))
    async with http_client:
        async with streamable_http_client(url, http_client=http_client) as (read, write):
            async with ClientSession(read, write) as session:
                yield session


class BailianProvider:
    """阿里云百炼 EnhancedSearch MCP（默认工具 search_pro）。"""

    name = "bailian"

    @property
    def available(self) -> bool:
        return bool(settings.WEBSEARCH_ENABLED and settings.WEBSEARCH_API_KEY)

    def _normalize_args(self, query: str, category: str | None,
                        time_range: str | None) -> dict:
        args: dict[str, Any] = {"query": query}
        if category:
            cats = [c.strip().lower() for c in str(category).split(",") if c.strip()]
            cats = [c for c in cats if c in _CATEGORIES]
            if cats:
                args["category"] = ",".join(cats)
        if time_range and str(time_range) in _TIME_RANGES:
            args["timeRange"] = str(time_range)
        else:
            args["timeRange"] = "NoLimit"
        return args

    @staticmethod
    def _parse_pages(data: Any) -> list[dict]:
        if not isinstance(data, dict):
            raise ValueError("MCP 返回顶层不是 JSON 对象")
        pages = data.get("pages")
        if not isinstance(pages, list):
            raise ValueError("MCP 返回缺少 pages 列表")
        items: list[dict] = []
        for p in pages:
            if not isinstance(p, dict):
                continue
            it = _item(
                str(p.get("title") or ""), str(p.get("url") or ""),
                str(p.get("hostname") or ""), str(p.get("snippet") or ""),
                "bailian",
            )
            if it:
                items.append(it)
        return items

    async def search(self, query: str, *, top_k: int,
                     category: str | None = None,
                     time_range: str | None = None) -> ProviderResult:
        if not self.available:
            reason = ("WEBSEARCH_ENABLED=false" if not settings.WEBSEARCH_ENABLED
                      else "未配置 WEBSEARCH_API_KEY")
            return ProviderResult(self.name, skipped=True, code=ErrorCode.WEB_AUTH.value,
                                  detail=f"{reason}，已跳过百炼检索。")

        t0 = time.perf_counter()
        args = self._normalize_args(query, category, time_range)
        try:
            headers = {"Authorization": f"Bearer {settings.WEBSEARCH_API_KEY}"}
            async with _mcp_session(
                settings.WEBSEARCH_MCP_URL, headers, settings.WEBSEARCH_TIMEOUT,
            ) as session:
                await session.initialize()
                res = await session.call_tool(settings.WEBSEARCH_TOOL, args)

            if getattr(res, "isError", False):
                text = " ".join(getattr(c, "text", "") for c in getattr(res, "content", [])).strip()
                return ProviderResult(
                    self.name, code=ErrorCode.WEB_UNAVAILABLE.value,
                    detail=f"MCP 工具返回错误：{text[:160] or 'unknown'}",
                    latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                )

            raw_text = "".join(
                getattr(b, "text", "") for b in getattr(res, "content", [])
                if getattr(b, "type", "") == "text"
            )
            items = self._parse_pages(json.loads(raw_text) if raw_text.strip() else {})
            return ProviderResult(
                self.name, ok=True, degraded=False, results=items,
                latency_ms=round((time.perf_counter() - t0) * 1000, 1),
            )
        except json.JSONDecodeError as e:
            return ProviderResult(self.name, code=ErrorCode.WEB_PARSE.value,
                                  detail=f"返回不是合法 JSON：{e}",
                                  latency_ms=round((time.perf_counter() - t0) * 1000, 1))
        except ValueError as e:
            return ProviderResult(self.name, code=ErrorCode.WEB_PARSE.value, detail=str(e),
                                  latency_ms=round((time.perf_counter() - t0) * 1000, 1))
        except Exception as e:  # noqa: BLE001 — 网络/超时/鉴权/SDK 异常统一降级
            status = _exc_status(e)
            code = (ErrorCode.WEB_AUTH if status in (401, 403) else ErrorCode.WEB_UNAVAILABLE)
            logger.warning("[WEB][bailian] 检索失败：%s %s", type(e).__name__, str(e)[:160])
            return ProviderResult(
                self.name, code=code.value, detail=f"{type(e).__name__}: {str(e)[:160]}",
                latency_ms=round((time.perf_counter() - t0) * 1000, 1),
            )


# ============ Tavily ============


class TavilyProvider:
    """Tavily Search API：英文 / 海外源强，聚合新闻与评测站点。"""

    name = "tavily"

    @property
    def available(self) -> bool:
        return bool(settings.WEBSEARCH_ENABLED and settings.TAVILY_API_KEY)

    async def search(self, query: str, *, top_k: int,
                     category: str | None = None,
                     time_range: str | None = None) -> ProviderResult:
        if not self.available:
            reason = ("WEBSEARCH_ENABLED=false" if not settings.WEBSEARCH_ENABLED
                      else "未配置 TAVILY_API_KEY")
            return ProviderResult(self.name, skipped=True, code=ErrorCode.WEB_AUTH.value,
                                  detail=f"{reason}，已跳过 Tavily 检索。")

        t0 = time.perf_counter()
        url = settings.TAVILY_BASE_URL.rstrip("/") + "/search"
        payload = {
            "query": query,
            "max_results": max(1, min(int(top_k), 10)),
            "search_depth": settings.TAVILY_SEARCH_DEPTH,
            "include_answer": False,
            "include_raw_content": False,
            "topic": settings.TAVILY_TOPIC,
        }
        # days 只在 news topic 且显式给了时间范围时透传
        if settings.TAVILY_TOPIC == "news" and time_range:
            payload["days"] = {"OneDay": 1, "OneWeek": 7, "OneMonth": 30, "OneYear": 365}.get(time_range, 30)
        headers = {
            "Authorization": f"Bearer {settings.TAVILY_API_KEY}",
            "Content-Type": "application/json",
        }

        attempts = max(1, int(settings.TAVILY_MAX_RETRIES) + 1)
        last_detail = ""
        for attempt in range(attempts):
            if attempt:
                await asyncio.sleep(0.5 * (2 ** (attempt - 1)))
            try:
                async with httpx.AsyncClient(timeout=float(settings.TAVILY_TIMEOUT)) as client:
                    resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code in (401, 403):
                    return ProviderResult(self.name, code=ErrorCode.WEB_AUTH.value,
                                          detail=f"Tavily 鉴权失败（HTTP {resp.status_code}）",
                                          latency_ms=round((time.perf_counter() - t0) * 1000, 1))
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_detail = f"HTTP {resp.status_code}: {resp.text[:120]}"
                    continue
                if resp.status_code >= 400:
                    return ProviderResult(self.name, code=ErrorCode.WEB_UNAVAILABLE.value,
                                          detail=f"HTTP {resp.status_code}: {resp.text[:160]}",
                                          latency_ms=round((time.perf_counter() - t0) * 1000, 1))

                data = resp.json()
                raw = data.get("results") if isinstance(data, dict) else None
                if not isinstance(raw, list):
                    return ProviderResult(self.name, code=ErrorCode.WEB_PARSE.value,
                                          detail="Tavily 响应缺少 results 数组",
                                          latency_ms=round((time.perf_counter() - t0) * 1000, 1))
                items: list[dict] = []
                for r in raw:
                    if not isinstance(r, dict):
                        continue
                    it = _item(str(r.get("title") or ""), str(r.get("url") or ""),
                               str(r.get("hostname") or ""), str(r.get("content") or ""),
                               "tavily", r.get("score"))
                    if it:
                        items.append(it)
                return ProviderResult(self.name, ok=True, degraded=False, results=items,
                                      latency_ms=round((time.perf_counter() - t0) * 1000, 1))
            except (httpx.TimeoutException, httpx.RequestError) as e:
                last_detail = f"{type(e).__name__}: {str(e)[:120]}"
                continue
            except Exception as e:  # noqa: BLE001
                logger.warning("[WEB][tavily] 检索失败：%s %s", type(e).__name__, str(e)[:160])
                return ProviderResult(self.name, code=ErrorCode.WEB_PARSE.value,
                                      detail=f"{type(e).__name__}: {str(e)[:160]}",
                                      latency_ms=round((time.perf_counter() - t0) * 1000, 1))

        return ProviderResult(self.name, code=ErrorCode.WEB_UNAVAILABLE.value,
                              detail=last_detail or "Tavily 多次重试仍失败",
                              latency_ms=round((time.perf_counter() - t0) * 1000, 1))


_BAILIAN = BailianProvider()
_TAVILY = TavilyProvider()

PROVIDERS = {"bailian": _BAILIAN, "tavily": _TAVILY}


def get_provider(name: str):
    return PROVIDERS.get((name or "").strip().lower())


def available_providers() -> list[str]:
    return [n for n, p in PROVIDERS.items() if p.available]
