"""限时抢购 / 预售排队。

设计取舍（面试讲解要点）：
- 为什么不用 Celery / RabbitMQ：本场景是「瞬时高并发抢购 + 原子库存扣减」，
  Celery/RabbitMQ 适合「耗时后台任务」，杀鸡不用牛刀。
- 正确选型是 Redis 原子命令：单进程内用 asyncio.Lock 即可；多 worker / 多实例部署时，
  用 Redis 的原子扣减（DECR + Lua）保证「库存扣减 + 排队序号」全局一致，
  这正是本项目在 CACHE_BACKEND=redis 时走的模式。
- 中签后给一个短窗口（hold_seconds）完成下单，超时未下单则库存回滚、队首递补。

存储后端：
- CACHE_BACKEND=memory（默认，零依赖）：内存 + asyncio.Lock，单机演示直接跑通。
- CACHE_BACKEND=redis：原子 Lua 脚本扣减 + Redis list 排队，天然支持多实例水平扩展。
  若 Redis 连接失败，自动降级回内存模式（保证 demo 永远能跑）。
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field

from app.config import settings

# ---------------------------------------------------------------------------
# 可选 Redis 后端（lazy import，未装/未配置时自动走内存）
# ---------------------------------------------------------------------------
_REDIS = None
_REDIS_DISABLED = False


def _get_redis():
    global _REDIS, _REDIS_DISABLED
    if _REDIS_DISABLED or settings.CACHE_BACKEND != "redis":
        return None
    if _REDIS is None:
        try:
            import redis.asyncio as aioredis
            _REDIS = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        except Exception:
            _REDIS_DISABLED = True
            return None
    return _REDIS


# Lua：原子「入队 + 扣库存 + 发 token」。保证并发下库存与排队序号一致。
_LUA_RESERVE = """
local base = KEYS[1]
local launch = tonumber(redis.call('GET', base..':launch') or '0')
if launch > tonumber(ARGV[2]) then
  return cjson.encode({ok=false, reason='not_launched', launch_at=launch})
end
local hold = tonumber(redis.call('GET', base..':hold') or '120')
local qkey = base..':queue'
local rkey = base..':res'
local pos = redis.call('LPOS', qkey, ARGV[1])
if pos then
  local raw = redis.call('HGET', rkey, ARGV[1])
  local tok, exp = ARGV[3], '0'
  if raw then tok, exp = raw:match('([^|]+)|(.+)') end
  return cjson.encode({ok=true, position=pos+1, stock_left=tonumber(redis.call('GET', base..':remaining')), token=tok, hold_seconds=hold, repeated=true})
end
local remaining = tonumber(redis.call('GET', base..':remaining') or '0')
if remaining <= 0 then
  redis.call('RPUSH', qkey, ARGV[1])
  return cjson.encode({ok=false, reason='sold_out_waitlist', position=redis.call('LLEN', qkey), stock_left=0, waitlist=true})
end
redis.call('DECR', base..':remaining')
redis.call('RPUSH', qkey, ARGV[1])
redis.call('HSET', rkey, ARGV[1], ARGV[3]..'|'..tostring(tonumber(ARGV[2])+hold))
return cjson.encode({ok=true, position=redis.call('LLEN', qkey), stock_left=tonumber(redis.call('GET', base..':remaining')), token=ARGV[3], hold_seconds=hold})
"""

# Lua：中签超时未下单 → 回滚库存、移出队列与预约，候补递补。
_LUA_RELEASE = """
local base = KEYS[1]
local rkey = base..':res'
local raw = redis.call('HGET', rkey, ARGV[1])
if not raw then return '0' end
local _, exp = raw:match('([^|]+)|(.+)')
if tonumber(exp) <= tonumber(ARGV[2]) then
  redis.call('HDEL', rkey, ARGV[1])
  redis.call('LREM', base..':queue', 1, ARGV[1])
  redis.call('INCR', base..':remaining')
  return '1'
end
return '0'
"""


# ---------------------------------------------------------------------------
# 内存模式（默认）
# ---------------------------------------------------------------------------
_SALES: dict[str, "FlashSale"] = {}
_LOCKS: dict[str, asyncio.Lock] = {}


def _lock(sku: str) -> asyncio.Lock:
    return _LOCKS.setdefault(sku, asyncio.Lock())


@dataclass
class FlashSale:
    sku: str
    total: int
    remaining: int
    launch_at: float | None
    hold_seconds: int
    queue: list[str] = field(default_factory=list)
    reservations: dict[str, dict] = field(default_factory=dict)


def configure(sku: str, total_stock: int, launch_at: float | None = None, hold_seconds: int = 120):
    """配置/重置一个抢购活动（同步入口；内部对 redis 模式用同步 redis 客户端写入）。"""
    r = _get_redis()
    if r is not None:
        try:
            return _configure_redis_sync(sku, total_stock, launch_at, hold_seconds)
        except Exception:
            global _REDIS_DISABLED
            _REDIS_DISABLED = True
    _SALES[sku] = FlashSale(
        sku=sku, total=total_stock, remaining=total_stock,
        launch_at=launch_at, hold_seconds=hold_seconds,
    )
    return _SALES[sku]


def _configure_redis_sync(sku: str, total_stock: int, launch_at, hold_seconds: int):
    """用同步 redis 客户端写配置（configure 在 lifespan/路由里是同步调用的）。"""
    import redis as sync_redis
    client = sync_redis.from_url(settings.REDIS_URL, decode_responses=True)
    base = f"flash:{sku}"
    client.set(base + ":total", total_stock)
    client.set(base + ":remaining", total_stock)
    client.set(base + ":launch", launch_at or 0)
    client.set(base + ":hold", hold_seconds)
    client.delete(base + ":queue", base + ":res")
    client.close()
    return {"ok": True, "sku": sku, "total": total_stock, "backend": "redis"}


async def reserve(sku: str, buyer_id: str) -> dict:
    r = _get_redis()
    if r is not None:
        try:
            return await _reserve_redis(r, sku, buyer_id)
        except Exception:
            global _REDIS_DISABLED
            _REDIS_DISABLED = True
    return await _reserve_mem(sku, buyer_id)


async def _reserve_mem(sku: str, buyer_id: str) -> dict:
    sale = _SALES.get(sku)
    if not sale:
        return {"ok": False, "reason": "未配置抢购活动"}
    if sale.launch_at and time.time() < sale.launch_at:
        return {"ok": False, "reason": "未到开售时间", "launch_at": sale.launch_at}
    async with _lock(sku):
        if buyer_id in sale.queue:
            pos = sale.queue.index(buyer_id) + 1
            token = sale.reservations.get(buyer_id, {}).get("token")
            return {"ok": True, "position": pos, "stock_left": sale.remaining,
                    "token": token, "hold_seconds": sale.hold_seconds, "repeated": True}
        if sale.remaining <= 0:
            sale.queue.append(buyer_id)
            return {"ok": False, "reason": "已售罄，已进入候补队列",
                    "position": len(sale.queue), "stock_left": 0, "waitlist": True}
        sale.queue.append(buyer_id)
        sale.remaining -= 1
        token = uuid.uuid4().hex
        sale.reservations[buyer_id] = {"token": token, "expires": time.time() + sale.hold_seconds}
        return {"ok": True, "position": len(sale.queue), "stock_left": sale.remaining,
                "token": token, "hold_seconds": sale.hold_seconds}


async def _reserve_redis(r, sku: str, buyer_id: str) -> dict:
    base = f"flash:{sku}"
    token = uuid.uuid4().hex
    res = await r.eval(_LUA_RESERVE, 1, base, buyer_id, str(time.time()), token)
    # redis 返回的是 cjson 字符串
    data = json.loads(res) if isinstance(res, str) else res
    data.setdefault("backend", "redis")
    return data


async def status(sku: str, buyer_id: str | None = None) -> dict:
    r = _get_redis()
    if r is not None:
        try:
            return await _status_redis(r, sku, buyer_id)
        except Exception:
            global _REDIS_DISABLED
            _REDIS_DISABLED = True
    return await _status_mem(sku, buyer_id)


async def _status_mem(sku: str, buyer_id: str | None = None) -> dict:
    sale = _SALES.get(sku)
    if not sale:
        return {"ok": False, "reason": "未配置抢购活动"}
    position = None
    won = False
    expires_at = None
    if buyer_id and buyer_id in sale.reservations:
        position = sale.queue.index(buyer_id) + 1
        r = sale.reservations[buyer_id]
        expires_at = r["expires"]
        won = time.time() < expires_at
    return {
        "ok": True, "sku": sku, "position": position, "won": won,
        "stock_left": sale.remaining, "queue_len": len(sale.queue),
        "expires_at": expires_at, "launch_at": sale.launch_at,
        "total": sale.total, "backend": "memory",
    }


async def _status_redis(r, sku: str, buyer_id: str | None = None) -> dict:
    base = f"flash:{sku}"
    remaining = int(await r.get(base + ":remaining") or 0)
    queue_len = await r.llen(base + ":queue")
    launch = float(await r.get(base + ":launch") or 0)
    total = int(await r.get(base + ":total") or 0)
    position = None
    won = False
    expires_at = None
    if buyer_id:
        raw = await r.hget(base + ":res", buyer_id)
        if raw:
            _, exp = raw.split("|", 1)
            expires_at = float(exp)
            pos = await r.lpos(base + ":queue", buyer_id)
            if pos is not None:
                position = pos + 1
                won = time.time() < expires_at
    return {
        "ok": True, "sku": sku, "position": position, "won": won,
        "stock_left": remaining, "queue_len": queue_len,
        "expires_at": expires_at, "launch_at": launch,
        "total": total, "backend": "redis",
    }


async def release_if_expired(sku: str, buyer_id: str) -> bool:
    r = _get_redis()
    if r is not None:
        try:
            res = await r.eval(_LUA_RELEASE, 1, f"flash:{sku}", buyer_id, str(time.time()))
            return str(res) == "1"
        except Exception:
            global _REDIS_DISABLED
            _REDIS_DISABLED = True
    sale = _SALES.get(sku)
    if not sale or buyer_id not in sale.reservations:
        return False
    async with _lock(sku):
        rr = sale.reservations[buyer_id]
        if time.time() >= rr["expires"]:
            sale.reservations.pop(buyer_id, None)
            if buyer_id in sale.queue:
                sale.queue.remove(buyer_id)
            sale.remaining += 1
            return True
    return False
