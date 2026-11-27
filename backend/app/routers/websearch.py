"""
联网搜索自检与演示接口（设置页用）。

- GET  /api/websearch/status：百炼 MCP 配置（key 脱敏）、调用统计、降级口径
- POST /api/websearch/search ：直接调一次 EnhancedSearch，看命中条目与客服收到的 observation

与 /api/rag/* 一致：任何失败都返回 HTTP 200 + ok=false/degraded=true + code + detail，
检索增强允许降级，不把设置页或客服对话带崩。
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.websearch_client import get_websearch

router = APIRouter(prefix="/api/websearch", tags=["websearch"])


class SearchPayload(BaseModel):
    query: str
    category: str | None = None
    time_range: str | None = None
    top_k: int | None = None
    source: str | None = None          # auto | bailian | tavily | both


@router.get("/status")
async def status():
    try:
        return {
            "ok": True,
            "pipeline": "外部通用概念/行业名词/新品行情/时效资讯 → 多源联网检索（百炼 EnhancedSearch MCP / Tavily 按语种路由 + 合并去重 + 可选重排）→ 标题/来源/摘要注入售前 Agent；本店商品规格价格库存以本地库为准",
            **get_websearch().public(),
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "code": "SYS000", "detail": f"{type(e).__name__}: {e}",
                **get_websearch().public()}


@router.post("/search")
async def search(payload: SearchPayload):
    try:
        out = await get_websearch().search(
            payload.query,
            category=payload.category,
            time_range=payload.time_range,
            top_k=payload.top_k,
            source=payload.source,
        )
        return out
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "degraded": True, "code": "SYS000",
                "detail": f"{type(e).__name__}: {e}", "query": payload.query,
                "results": [], "observation": "联网检索暂时不可用。"}
