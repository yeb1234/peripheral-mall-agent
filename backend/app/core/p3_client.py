"""P3 事实库 MCP 客户端（P4 的「第一信源」通道）。

信源定位：
- P3 是内部参数/跑分/评测库，事实带来源、置信度与冲突标记，是「严格对比」的第一信源；
- P4 商品库是自营 SKU 的经营事实（价格/库存/规格）；
- 公开网络只在上面两层都覆盖不到时补位，是第二信源。

这里只做协议调用与错误归一：连不上/超时/非 JSON 都返回 {"error": ...}，
由上层决定降级，不把异常抛给 Agent 节点。

调用侧加了两道保护（P3 的 run_traces 主键是「秒级时间戳 + payload 哈希」，
同一秒内重复同一请求会撞唯一键并返回 UnexpectedToolError）：
- 同参数 60s 内直接复用上次结果（`P3_CACHE_TTL`，设 0 关闭）；
- 同参数并发只发一次（in-flight 合并），避免并发双发。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from app.config import settings

log = logging.getLogger(__name__)

_cache: dict[str, tuple[float, dict]] = {}
_inflight: dict[str, asyncio.Task] = {}
_MAX_CACHE = 512


def enabled() -> bool:
    return bool(settings.P3_MCP_ENABLED)


def _is_error(data: object) -> bool:
    return isinstance(data, dict) and bool(data.get("error"))


async def _call_once(tool: str, args: dict, timeout: float | None) -> dict:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def _run() -> dict:
        async with streamable_http_client(settings.P3_MCP_URL) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool, args)
        if getattr(result, "isError", False):
            text = result.content[0].text if result.content else "P3 返回错误"
            return {"error": text}
        text = result.content[0].text if result.content else ""
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"error": f"P3 响应不是 JSON：{text[:200]!r}"}

    try:
        return await asyncio.wait_for(_run(), timeout=timeout or settings.P3_MCP_TIMEOUT)
    except Exception as exc:
        log.warning("P3 MCP %s failed: %s: %s", tool, type(exc).__name__, exc)
        return {"error": f"{type(exc).__name__}: {exc}"[:240]}


async def call(tool: str, args: dict | None = None, timeout: float | None = None) -> dict:
    """调一个 P3 工具，带 TTL 缓存与并发合并；错误不缓存。"""
    args = args or {}
    if not enabled():
        return {"error": "P3_MCP_DISABLED"}

    ttl = float(getattr(settings, "P3_CACHE_TTL", 60.0) or 0)
    key = f"{tool}|{json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)}"
    if ttl > 0:
        cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < ttl:
            return cached[1]

    task = _inflight.get(key)
    if task is not None and not task.done():
        return await asyncio.shield(task)

    task = asyncio.create_task(_call_once(tool, args, timeout))
    _inflight[key] = task
    try:
        data = await task
    finally:
        if _inflight.get(key) is task:
            _inflight.pop(key, None)

    if ttl > 0 and not _is_error(data):
        if len(_cache) >= _MAX_CACHE:
            _cache.clear()
        _cache[key] = (time.monotonic(), data)
    return data


async def list_tools() -> list[dict]:
    """探测 P3 暴露的工具，供运维/冒烟脚本核对。"""
    if not enabled():
        return []
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(settings.P3_MCP_URL) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            tools = await session.list_tools()
            return [{"name": t.name, "description": (t.description or "")[:200]} for t in tools.tools]


async def compare(products: list[str], kind: str | None = None,
                  scene: str | None = None, metric: str | None = None) -> dict:
    payload: dict = {"products": products}
    if kind:
        payload["kind"] = kind
    if scene:
        payload["scene"] = scene
    if metric:
        payload["metric"] = metric
    return await call("compare_products", payload)


async def facts(product_id: str) -> dict:
    return await call("get_product_facts", {"product_id": product_id})


async def search(query: str, kind: str | None = None, limit: int = 5) -> dict:
    payload: dict = {"query": query, "limit": limit}
    if kind:
        payload["kind"] = kind
    return await call("search_products", payload)


async def ranking(kind: str, field: str, limit: int = 10) -> dict:
    return await call("benchmark_ranking", {"kind": kind, "field": field, "limit": limit})
