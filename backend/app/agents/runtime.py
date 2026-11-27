"""
运行期上下文。

LangGraph 的节点签名只收 state，但我们每个请求都有自己的 AsyncSession 与 ToolContext。
用 contextvar 在调用图之前注入，节点内读取——图只编译一次，DB 会话按请求隔离。
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass, field

from app.agents.tools.base import ToolContext

_runtime = contextvars.ContextVar("agent_runtime", default=None)


@dataclass
class Runtime:
    db: object
    ctx: ToolContext
    extra: dict = field(default_factory=dict)


def set_runtime(rt: Runtime) -> object:
    return _runtime.set(rt)


def get_runtime() -> Runtime:
    rt = _runtime.get()
    if rt is None:
        raise RuntimeError("Agent 运行期未初始化（请在调用图前 set_runtime）")
    return rt


def reset_runtime(token) -> None:
    _runtime.reset(token)
