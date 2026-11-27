"""Agent 运行态缓存门面（确认单锁、工具结果缓存）。

Harness 约束：agents 层不得直接依赖缓存驱动。这里把 core.cache 收在 services，
工具层只看到语义化方法；将来换 Redis 集群/加 key 前缀，也只改这一处。
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from app.core.cache import cache_json, get_cache, get_json, with_lock


async def set_raw(key: str, value: str, ex: int | None = None) -> None:
    await get_cache().set(key, value, ex=ex)


async def get_raw(key: str) -> str | None:
    return await get_cache().get(key)


async def delete(key: str) -> None:
    await get_cache().delete(key)


async def get_json_value(key: str) -> Any | None:
    return await get_json(key)


async def set_json(key: str, obj: Any, ex: int | None = None) -> None:
    await cache_json(key, obj, ex=ex)


async def lock(key: str, fn: Callable[[], Awaitable[Any]], ttl: int = 30) -> Any:
    return await with_lock(key, fn, ttl=ttl)
