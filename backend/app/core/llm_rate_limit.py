"""
LLM 限流控制系统（与 app/core/rate_limit.py 的「写操作频率限制」职责不同）。

为什么不用「各请求各自重试」：低额度 token 的 RPM/TPM 是全局共享的，多个会话同时
重试只会互相把额度打满，形成 429 风暴。这里改成：

  全局串行锁（同一时刻只有 1 个 LLM 请求在飞）
    + 两次调用间最小间隔
    + 自适应：撞 429 自动拉长间隔，连续成功缓慢回落
    + 指数退避 + 抖动 + 尊重 Retry-After
    + 排队超时（不无限等，返回 LLM001）
    + 全量统计，供 /api/llm/ping 观测

一个 slot 覆盖「一次调用 + 它所有的重试」，保证重试期间别的请求不会插队撞限流。
切到付费/自建端点时，把 LLM_MIN_INTERVAL 调 0、LLM_MAX_RETRIES 调小即可。
"""
from __future__ import annotations

import asyncio
import random
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass

from app.config import settings
from app.core.errors import AppError, ErrorCode


@dataclass
class Slot:
    """一次调用资格；position = 排队时前面还有几个请求。"""

    position: int = 0


class LLMRateLimiter:
    def __init__(self) -> None:
        self._loop_id: int | None = None
        self._lock: asyncio.Lock | None = None
        self._waiting = 0
        self._busy = False
        self._last_end = 0.0
        self._base_interval = float(settings.LLM_MIN_INTERVAL)
        self._interval = float(settings.LLM_MIN_INTERVAL)
        self._ok_streak = 0
        # 统计
        self.total_calls = 0
        self.total_success = 0
        self.total_rate_limited = 0
        self.total_retries = 0
        self.total_quota_exceeded = 0
        self.last_latency_ms: float | None = None
        self.last_limit_at: float | None = None

    def _ensure_lock(self) -> asyncio.Lock:
        """asyncio.Lock 绑定事件循环；重载 / TestClient 换循环时自动重建。"""
        loop_id = id(asyncio.get_running_loop())
        if self._lock is None or self._loop_id != loop_id:
            self._lock = asyncio.Lock()
            self._loop_id = loop_id
            self._waiting = 0
            self._busy = False
            self._last_end = 0.0
        return self._lock

    @asynccontextmanager
    async def slot(self):
        lock = self._ensure_lock()
        self._waiting += 1
        position = self._waiting - 1
        try:
            await asyncio.wait_for(lock.acquire(), timeout=float(settings.LLM_QUEUE_TIMEOUT))
        except asyncio.TimeoutError:
            self._waiting -= 1
            raise AppError(
                ErrorCode.LLM_RATE_LIMIT,
                "模型队列繁忙（等待超时），请稍后重试。",
                detail="queue timeout",
            )
        self._waiting -= 1
        self._busy = True
        self.total_calls += 1
        try:
            wait = self._interval - (time.monotonic() - self._last_end)
            if wait > 0:
                await asyncio.sleep(wait)
            yield Slot(position=position)
        finally:
            self._last_end = time.monotonic()
            self._busy = False
            lock.release()

    def on_success(self, latency_s: float, had_retries: bool = False) -> None:
        self.total_success += 1
        self.last_latency_ms = round(latency_s * 1000, 1)
        # 只有「一次就成功」才算健康信号；重试后才成功不积累信用，避免限流稍缓
        # 就误判为额度充足、把间隔一路降回去又撞 429。
        if had_retries:
            self._ok_streak = 0
            return
        self._ok_streak += 1
        # 连续 2 次首发成功即回落 25%，让限流缓解后能较快恢复速度
        if settings.LLM_ADAPTIVE and self._ok_streak >= 2:
            self._interval = max(self._base_interval, round(self._interval * 0.75, 3))
            self._ok_streak = 0

    def on_rate_limited(self) -> None:
        self.total_rate_limited += 1
        self.total_retries += 1
        self.last_limit_at = time.time()
        self._ok_streak = 0
        if settings.LLM_ADAPTIVE:
            # 自适应：撞一次限流就把全局最小间隔拉长 1.6 倍（上限 12s）
            self._interval = min(12.0, max(self._base_interval, self._interval * 1.6))

    def on_quota_exceeded(self) -> None:
        """周期额度耗尽：只记账，不做退避、不拉长全局间隔（重试没有意义）。"""
        self.total_quota_exceeded += 1
        self.last_limit_at = time.time()

    async def backoff(self, attempt: int, exc: Exception | None = None) -> None:
        """指数退避 + 抖动；服务端给了 Retry-After 就取较大值。"""
        delay = min(
            float(settings.LLM_RETRY_MAX),
            float(settings.LLM_RETRY_BASE) * (2 ** (attempt - 1)),
        )
        headers = getattr(getattr(exc, "response", None), "headers", None) or {}
        try:
            delay = max(delay, float(headers.get("Retry-After") or 0))
        except (TypeError, ValueError):
            pass
        delay += random.uniform(0, 0.35 * delay)  # jitter：避免多请求同步重试
        await asyncio.sleep(delay)

    def set_min_interval(self, value: float) -> None:
        """切换 LLM 提供方时调整节流：本地 / 离线无额度限制 → 0，外部端点恢复配置值。

        仍然保留全局串行锁（保证排队顺序与统计），只是不再人为插入等待。
        """
        v = max(0.0, float(value))
        if abs(self._base_interval - v) < 1e-6:
            return
        self._base_interval = v
        self._interval = v
        self._ok_streak = 0

    def waiting_count(self) -> int:
        return self._waiting + (1 if self._busy else 0)

    def status(self) -> dict:
        return {
            "enabled": self._base_interval > 0,
            "adaptive": bool(settings.LLM_ADAPTIVE),
            "base_interval": round(self._base_interval, 2),
            "current_interval": round(self._interval, 2),
            "max_retries": int(settings.LLM_MAX_RETRIES),
            "retry_max_seconds": float(settings.LLM_RETRY_MAX),
            "queue_timeout": float(settings.LLM_QUEUE_TIMEOUT),
            "waiting": self._waiting,
            "busy": self._busy,
            "total_calls": self.total_calls,
            "total_success": self.total_success,
            "total_rate_limited": self.total_rate_limited,
            "total_retries": self.total_retries,
            "total_quota_exceeded": self.total_quota_exceeded,
            "last_latency_ms": self.last_latency_ms,
        }


_limiter = LLMRateLimiter()


def get_llm_limiter() -> LLMRateLimiter:
    return _limiter
