"""P6（VidFlow 商品视频管线）客户端：P4 → P6 的文案生成通道。

P4 只传 SKU 与渠道，商品事实由 P6 反向调 P4 的 ``/api/catalog/{sku}/card`` 取，
保证文案永远基于同一份商品数据，不在两个系统里各存一份商品库。
"""
from __future__ import annotations

import logging

import httpx

from app.config import settings

log = logging.getLogger(__name__)


def base_url() -> str:
    return str(settings.P6_BASE_URL).rstrip("/")


async def health() -> dict:
    if not settings.P6_COPY_ENABLED:
        return {"ok": False, "reason": "disabled"}
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{base_url()}/health")
        return {"ok": r.status_code == 200, "status_code": r.status_code}
    except Exception as e:
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"[:160]}


async def generate_copy(sku: str, channel: str = "detail", tone: str = "专业") -> dict:
    """调 P6 生成商品文案。失败返回 degraded，绝不抛出到 Agent 节点。"""
    if not settings.P6_COPY_ENABLED:
        return {"ok": False, "degraded": True, "reason": "p6_copy_disabled"}
    payload = {"sku": sku.upper(), "channel": channel, "tone": tone}
    try:
        async with httpx.AsyncClient(timeout=settings.P6_TIMEOUT) as client:
            r = await client.post(f"{base_url()}/api/copy/generate", json=payload)
        if r.status_code != 200:
            return {"ok": False, "degraded": True,
                    "reason": f"p6_http_{r.status_code}", "detail": r.text[:200]}
        data = r.json()
        return {"ok": True, "degraded": False, "source": "p6", **data}
    except Exception as e:
        log.warning("P6 copy failed: %s: %s", type(e).__name__, e)
        return {"ok": False, "degraded": True, "reason": f"{type(e).__name__}: {e}"[:160]}
