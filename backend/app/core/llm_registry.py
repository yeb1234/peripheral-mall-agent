"""
LLM 提供方注册表 —— 运行时可切换，无需重启服务。

三种提供方：
- local    ：LM Studio / vLLM 等本机 OpenAI 协议端点（默认 http://127.0.0.1:1234/v1）
- external ：外部 LLM（如 DeepSeek V4.1 Flash via SenseNova）
- mock     ：内置确定性决策，无网络、无密钥，适合离线学习整个 Agent 编排

设计要点：
- 配置持久化在 backend/llm_config.json，**不写回 .env**：.env 仍是启动默认值，
  运行时的切换单独存，互不污染。
- 对外返回时 API key 一律脱敏；前端把脱敏值原样回传时保留原 key（不会把密钥洗掉）。
- fingerprint 让 get_llm() 的客户端缓存自动失效，切换后下一次调用立刻用新配置。
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any

from app.config import settings

_CONFIG_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "llm_config.json")
)
PROVIDER_IDS = ("local", "external", "mock")


def _defaults() -> dict:
    return {
        "active": "external" if settings.LLM_BACKEND == "openai" else "mock",
        "providers": {
            "local": {
                "label": "LM Studio（本地）",
                "base_url": "http://127.0.0.1:1234/v1",
                "api_key": "lm-studio",
                "model": "",
            },
            "external": {
                "label": "外部 LLM（DeepSeek / SenseNova）",
                "base_url": settings.OPENAI_BASE_URL,
                "api_key": settings.OPENAI_API_KEY,
                "model": settings.OPENAI_MODEL,
            },
            "mock": {
                "label": "Mock（离线确定性）",
                "base_url": "",
                "api_key": "",
                "model": "mock-deterministic",
            },
        },
    }


def _mask(key: str) -> str:
    if not key:
        return ""
    return (key[:6] + "****") if len(key) > 6 else "****"


class LLMRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.data: dict[str, Any] = self._load()

    def _load(self) -> dict:
        base = _defaults()
        if not os.path.isfile(_CONFIG_PATH):
            return base
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if raw.get("active") in PROVIDER_IDS:
                base["active"] = raw["active"]
            saved = raw.get("providers") or {}
            for pid in PROVIDER_IDS:
                if isinstance(saved.get(pid), dict):
                    base["providers"][pid].update(
                        {k: v for k, v in saved[pid].items()
                         if k in ("label", "base_url", "api_key", "model")}
                    )
        except Exception:  # 配置损坏就回退默认值，不让服务起不来
            pass
        return base

    def save(self) -> None:
        with self._lock:
            tmp = _CONFIG_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, _CONFIG_PATH)

    @property
    def active(self) -> str:
        return self.data["active"]

    def get(self, pid: str) -> dict:
        with self._lock:
            return dict(self.data["providers"].get(pid, {}))

    def active_config(self) -> tuple[str, dict]:
        pid = self.active
        return pid, self.get(pid)

    def fingerprint(self, pid: str) -> str:
        cfg = self.get(pid)
        return json.dumps(cfg, sort_keys=True, ensure_ascii=False)

    def set_active(self, pid: str) -> None:
        if pid not in PROVIDER_IDS:
            raise ValueError(f"未知提供方：{pid}")
        with self._lock:
            self.data["active"] = pid

    def update(self, pid: str, patch: dict) -> None:
        if pid not in PROVIDER_IDS:
            raise ValueError(f"未知提供方：{pid}")
        with self._lock:
            cfg = self.data["providers"][pid]
            for k in ("label", "base_url", "api_key", "model"):
                if k not in patch or patch[k] is None:
                    continue
                v = patch[k]
                # 前端回传的是脱敏值（或空）→ 保留原 key，避免把密钥洗掉
                if k == "api_key" and (not v or "****" in str(v)):
                    continue
                cfg[k] = str(v).strip() if isinstance(v, str) else v

    def public(self) -> dict:
        """给前端的安全视图：key 脱敏 + has_key 标记。"""
        with self._lock:
            providers = {}
            for pid, cfg in self.data["providers"].items():
                providers[pid] = {
                    **cfg,
                    "has_key": bool(cfg.get("api_key")),
                    "api_key": _mask(cfg.get("api_key", "")),
                }
            return {"active": self.active, "providers": providers, "config_path": _CONFIG_PATH}


_registry: LLMRegistry | None = None


def get_registry() -> LLMRegistry:
    global _registry
    if _registry is None:
        _registry = LLMRegistry()
    return _registry
