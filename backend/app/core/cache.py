"""
缓存 / 分布式原语抽象层。

为什么抽象：演示「异步 + 高性能」时切到 Redis；单机快速体验时用内存实现，零依赖即可跑。
所有 Agent 的并发原语（幂等键、限流、二次确认卡、会话写锁）都只依赖这个接口，
将来换 Redis 集群 / 多 worker，业务代码一处不动。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from app.config import settings


class Cache(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ex: int | None = None) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def exists(self, key: str) -> bool: ...
    async def setnx(self, key: str, value: str, ex: int | None = None) -> bool: ...
    async def expire(self, key: str, ex: int) -> None: ...
    async def incr(self, key: str, ex: int | None = None) -> int: ...


class MemoryCache:
    """进程内缓存：单事件循环下 dict 天然线程安全，足够单机演示。

    注意：asyncio.Lock 不可重入，因此 setnx 内部直接写字典，绝不调用 set()。
    """

    def __init__(self) -> None:
        self._store: dict[str, tuple[str, float]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> str | None:
        async with self._lock:
            item = self._store.get(key)
            if not item:
                return None
            val, exp = item
            if exp and time.time() > exp:
                self._store.pop(key, None)
                return None
            return val

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        async with self._lock:
            exp = time.time() + ex if ex else None
            self._store[key] = (value, exp)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._store.pop(key, None)

    async def exists(self, key: str) -> bool:
        return (await self.get(key)) is not None

    async def setnx(self, key: str, value: str, ex: int | None = None) -> bool:
        async with self._lock:
            if key in self._store:
                return False
            exp = time.time() + ex if ex else None
            self._store[key] = (value, exp)
            return True

    async def expire(self, key: str, ex: int) -> None:
        async with self._lock:
            item = self._store.get(key)
            if item:
                self._store[key] = (item[0], time.time() + ex)

    async def incr(self, key: str, ex: int | None = None) -> int:
        """计数器：用于滑动窗口限流。首次写入时按 ex 设过期，后续自增不改过期时间。"""
        async with self._lock:
            item = self._store.get(key)
            now = time.time()
            if item and (not item[1] or now <= item[1]):
                val = int(item[0]) + 1
                self._store[key] = (str(val), item[1])
                return val
            exp = now + ex if ex else None
            self._store[key] = ("1", exp)
            return 1


class RedisCache:
    """Redis 后端：真正的跨 worker 并发控制与高并发缓存。"""

    def __init__(self, url: str) -> None:
        import redis.asyncio as aioredis
        self._r = aioredis.from_url(url, decode_responses=True)

    async def get(self, key: str) -> str | None:  # type: ignore[return]
        return await self._r.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        await self._r.set(key, value, ex=ex)

    async def delete(self, key: str) -> None:
        await self._r.delete(key)

    async def exists(self, key: str) -> bool:
        return bool(await self._r.exists(key))

    async def setnx(self, key: str, value: str, ex: int | None = None) -> bool:
        return bool(await self._r.set(key, value, nx=True, ex=ex))

    async def expire(self, key: str, ex: int) -> None:
        await self._r.expire(key, ex)

    async def incr(self, key: str, ex: int | None = None) -> int:
        val = int(await self._r.incr(key))
        if ex and val == 1:
            await self._r.expire(key, ex)
        return val


_cache: Cache | None = None


def get_cache() -> Cache:
    """单例缓存：按配置返回 Memory 或 Redis 实现。"""
    global _cache
    if _cache is None:
        if settings.CACHE_BACKEND == "redis":
            _cache = RedisCache(settings.REDIS_URL)
        else:
            _cache = MemoryCache()
    return _cache


async def cache_json(key: str, obj: Any, ex: int | None = None) -> None:
    await get_cache().set(key, json.dumps(obj, ensure_ascii=False), ex=ex)


async def get_json(key: str) -> Any | None:
    raw = await get_cache().get(key)
    return json.loads(raw) if raw else None


async def with_lock(key: str, fn: Callable[[], Awaitable[Any]], ttl: int = 30) -> Any:
    """用 setnx 实现分布式锁，保护临界区（如会话级串行写）。"""
    cache = get_cache()
    lock_key = f"lock:{key}"
    # 乐观自旋，最多 ~ttl 秒
    waited = 0
    while not await cache.setnx(lock_key, "1", ex=ttl):
        if waited >= ttl:
            raise RuntimeError("获取锁超时（会话写操作正忙，请稍后再试）")
        await asyncio.sleep(0.2)
        waited += 0.2
    try:
        return await fn()
    finally:
        await cache.delete(lock_key)
