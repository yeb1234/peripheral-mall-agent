"""
写操作频率限制（文档：单会话写操作每 10 分钟不超过 2 次）。

令牌桶实现，按 session_id 维度存于缓存。注意：这是「体验/滥用」防护，
真正的安全靠六道关，限流只是最后一道缓冲。
"""
from __future__ import annotations

import time

from app.config import settings
from app.core.cache import get_cache


async def check_write_rate(session_id: str) -> tuple[bool, int]:
    """
    返回 (是否放行, 剩余可用次数)。
    窗口内写次数超过上限则返回 False。
    """
    cache = get_cache()
    key = f"wrate:{session_id}"
    raw = await cache.get(key)
    if raw:
        count, window_start = raw.split(":")
        count = int(count)
        if time.time() - float(window_start) > settings.WRITE_RATE_WINDOW:
            count = 0
            window_start = str(time.time())
    else:
        count = 0
        window_start = str(time.time())

    if count >= settings.WRITE_RATE_LIMIT:
        return False, max(0, settings.WRITE_RATE_LIMIT - count)

    # 放行并 +1
    count += 1
    await cache.set(key, f"{count}:{window_start}", ex=settings.WRITE_RATE_WINDOW)
    return True, settings.WRITE_RATE_LIMIT - count


# ---------------------------------------------------------------------------
# 通用多维限流：对话入口（按身份 + 按来源 IP）
# ---------------------------------------------------------------------------
async def hit(scope: str, ident: str, limit: int, window: int) -> dict:
    """记一次请求并返回 {allowed, count, limit, window, retry_after}。

    key 带窗口长度，窗口滚动时自然换 key；内存实现与 Redis 实现行为一致。
    """
    if limit <= 0 or not ident:
        return {"allowed": True, "count": 0, "limit": limit, "window": window, "retry_after": 0}
    key = f"rl:{scope}:{ident}:{window}"
    count = await get_cache().incr(key, ex=window)
    allowed = count <= limit
    return {
        "allowed": allowed, "count": count, "limit": limit, "window": window,
        "retry_after": window if not allowed else 0,
    }


async def check_many(checks: list[tuple[str, str, int, int]]) -> dict:
    """一次判多个维度，命中任一超限就拒绝（身份维度 + IP 维度互补）。"""
    worst: dict = {"allowed": True, "count": 0, "limit": 0, "window": 0, "retry_after": 0}
    for scope, ident, limit, window in checks:
        res = await hit(scope, ident, limit, window)
        if not res["allowed"]:
            return res
        if res["count"] > worst["count"]:
            worst = res
    return worst
