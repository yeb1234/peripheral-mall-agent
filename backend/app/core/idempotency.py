"""
幂等（写操作第四道关）。

业务键 = 会话 + 动作 + 目标对象哈希。重复请求（用户连点两下）返回首次结果，
绝不二次扣款 / 二次改单。基于缓存 setnx + 结果缓存实现。
"""
from __future__ import annotations

import hashlib
import json

from app.config import settings
from app.core.cache import get_cache


def business_key(session_id: str, action: str, target: str) -> str:
    h = hashlib.sha256(f"{session_id}|{action}|{target}".encode()).hexdigest()[:16]
    return f"idem:{action}:{h}"


async def idempotent(key: str, fn, ttl: int | None = None) -> dict:
    """
    先查缓存里是否已有该业务键的结果；有则直接返回（并标记 reused=True）。
    无则执行 fn()，把结果写回缓存后返回。
    """
    cache = get_cache()
    existing = await cache.get(key)
    if existing:
        result = json.loads(existing)
        result["idempotent_reused"] = True
        return result
    result = await fn()
    result = dict(result)
    result["idempotent_reused"] = False
    await cache.set(key, json.dumps(result, ensure_ascii=False), ex=ttl or settings.IDEMPOTENCY_TTL)
    return result
