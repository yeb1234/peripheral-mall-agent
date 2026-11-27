"""
LLM 提供方注册表 —— 运行时可切换，无需重启服务。

三种提供方：
- local    ：LM Studio / vLLM 等本机 OpenAI 协议端点（默认 http://127.0.0.1:1234/v1）
- external ：外部 LLM（如 DeepSeek V4.1 Flash via SenseNova）
- mock     ：内置确定性决策，无网络、无密钥，适合离线学习整个 Agent 编排

设计要点：
- 非机密配置（active / base_url / model / label）持久化在 backend/llm_config.json；
- 所有密钥只从 .env 读取（OPENAI_API_KEY / LOCAL_LLM_API_KEY），
  本文件、接口响应、前端页面都不保存明文 key。
- 对外返回时 API key 一律脱敏，并附 key_source 说明来自哪个环境变量。
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

# 密钥来源：只认 .env。llm_config.json 只存 base_url / model 这类非机密配置。
KEY_ENV = {
    "local": "LOCAL_LLM_API_KEY",
    "external": "OPENAI_API_KEY",
    "mock": "",
}


def resolve_key(pid: str) -> str:
    """按提供方从环境配置取密钥；空值表示未配置。"""
    env_name = KEY_ENV.get(pid, "")
    if not env_name:
        return ""
    value = getattr(settings, env_name, "") or ""
    return str(value)


def _defaults() -> dict:
    return {
        "active": "external" if settings.LLM_BACKEND == "openai" else "mock",
        "providers": {
            "local": {
                "label": "LM Studio（本地）",
                "base_url": "http://127.0.0.1:1234/v1",
                "model": "",
            },
            "external": {
                "label": "外部 LLM（DeepSeek / SenseNova）",
                "base_url": settings.OPENAI_BASE_URL,
                "model": settings.OPENAI_MODEL,
            },
            "mock": {
                "label": "Mock（离线确定性）",
                "base_url": "",
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
                    # 旧版文件里可能残留明文 api_key，这里一律忽略（密钥只从 .env 读）
                    base["providers"][pid].update(
                        {k: v for k, v in saved[pid].items()
                         if k in ("label", "base_url", "model")}
                    )
        except Exception:  # 配置损坏就回退默认值，不让服务起不来
            pass
        return base

    def save(self) -> None:
        with self._lock:
            # 落盘只写非机密字段；api_key 永远来自 .env
            payload = {
                "_note": "密钥只从 backend/.env 读取，本文件不保存 key",
                "active": self.data["active"],
                "providers": {
                    pid: {k: v for k, v in cfg.items() if k in ("label", "base_url", "model")}
                    for pid, cfg in self.data["providers"].items()
                },
            }
            tmp = _CONFIG_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, _CONFIG_PATH)

    @property
    def active(self) -> str:
        return self.data["active"]

    def get(self, pid: str) -> dict:
        """返回运行时配置：非机密字段 + 从 .env 解析出的 key（不落盘）。"""
        with self._lock:
            cfg = dict(self.data["providers"].get(pid, {}))
        cfg["api_key"] = resolve_key(pid)
        return cfg

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
            # 只接受非机密字段；api_key 一律忽略，密钥只在 .env 里配
            for k in ("label", "base_url", "model"):
                if k not in patch or patch[k] is None:
                    continue
                v = patch[k]
                cfg[k] = str(v).strip() if isinstance(v, str) else v

    def public(self) -> dict:
        """给前端的安全视图：key 只回脱敏值与来源，前端不再能改写 key。"""
        with self._lock:
            providers = {}
            for pid, cfg in self.data["providers"].items():
                key = resolve_key(pid)
                providers[pid] = {
                    **cfg,
                    "has_key": bool(key),
                    "api_key": _mask(key),
                    "key_source": f".env · {KEY_ENV[pid]}" if KEY_ENV.get(pid) else "",
                }
            return {"active": self.active, "providers": providers, "config_path": _CONFIG_PATH}


_registry: LLMRegistry | None = None


def get_registry() -> LLMRegistry:
    global _registry
    if _registry is None:
        _registry = LLMRegistry()
    return _registry
