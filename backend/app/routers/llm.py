"""
LLM 设置与自检接口（给「设置」页面用）。

- GET  /api/llm/providers ：三套提供方配置（key 脱敏）+ 当前生效 + 限流统计
- GET  /api/llm/models    ：探测本地 LM Studio /v1/models，并标注 chat/embedding/reranker
- POST /api/llm/config    ：保存配置并切换生效提供方（运行时，无需重启）
- POST /api/llm/test      ：用「临时配置」握手一次，不改动当前生效配置
- GET  /api/llm/ping      ：当前生效配置的真实握手自检

所有探测/握手失败都返回 HTTP 200 + ok=false + detail，不抛 500。
"""
from __future__ import annotations

import time

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from app.agents.llm import MockLLM, OpenAIClient, get_llm, reset_llm
from app.core.llm_rate_limit import get_llm_limiter
from app.core.llm_registry import PROVIDER_IDS, get_registry

router = APIRouter(prefix="/api/llm", tags=["llm"])


def _model_kind(mid: str) -> str:
    """按名字粗分类型：BGE-M3 / text-embedding / gte 是检索模型，不能聊天。"""
    m = mid.lower()
    if "rerank" in m:
        return "reranker"
    if "embed" in m or "bge-m3" in m or m.startswith("gte-"):
        return "embedding"
    return "chat"


class ProviderPatch(BaseModel):
    label: str | None = None
    base_url: str | None = None
    model: str | None = None


class ConfigPayload(BaseModel):
    active: str | None = None
    local: ProviderPatch | None = None
    external: ProviderPatch | None = None
    mock: ProviderPatch | None = None


class TestPayload(BaseModel):
    provider: str = "local"
    base_url: str | None = None
    model: str | None = None


@router.get("/providers")
async def providers():
    return {
        **get_registry().public(),
        "rate_limit": get_llm_limiter().status(),
    }


@router.get("/models")
async def list_models(base_url: str = "http://127.0.0.1:1234/v1"):
    """读 LM Studio（或任何 OpenAI 协议端点）的 /v1/models。"""
    url = base_url.rstrip("/") + "/models"
    try:
        async with httpx.AsyncClient(timeout=6.0) as c:
            resp = await c.get(url)
            resp.raise_for_status()
            data = resp.json()
        raw = data.get("data", []) if isinstance(data, dict) else []
        items = []
        for m in raw:
            mid = str(m.get("id", "")) if isinstance(m, dict) else str(m)
            if not mid:
                continue
            kind = _model_kind(mid)
            items.append({"id": mid, "kind": kind, "chat": kind == "chat"})
        # 能对话的排前面，检索类沉底
        items.sort(key=lambda x: (x["kind"] != "chat", x["id"]))
        return {"ok": True, "base_url": base_url, "count": len(items), "models": items}
    except Exception as e:  # 本地服务没开 / 端口不对，都友好返回
        return {
            "ok": False, "base_url": base_url, "count": 0, "models": [],
            "detail": f"{type(e).__name__}: {e}",
        }


@router.post("/config")
async def save_config(payload: ConfigPayload):
    reg = get_registry()
    for pid in PROVIDER_IDS:
        patch = getattr(payload, pid, None)
        if patch:
            reg.update(pid, patch.model_dump(exclude_none=True))
    if payload.active:
        if payload.active not in PROVIDER_IDS:
            return {"ok": False, "detail": f"未知提供方：{payload.active}"}
        reg.set_active(payload.active)
    reg.save()
    reset_llm()
    get_llm()  # 立即重建客户端并同步限流间隔，切换后无需等下一次对话才生效
    return {
        "ok": True,
        **reg.public(),
        "rate_limit": get_llm_limiter().status(),
    }


@router.post("/test")
async def test_connection(payload: TestPayload):
    """用表单里的临时配置握手，不改变当前生效提供方。"""
    if payload.provider not in PROVIDER_IDS:
        return {"ok": False, "detail": f"未知提供方：{payload.provider}"}
    reg = get_registry()
    cfg = reg.get(payload.provider)
    base = payload.base_url or cfg.get("base_url")
    key = cfg.get("api_key")   # 只来自 .env，前端不再传 key
    model = payload.model or cfg.get("model")
    out: dict = {
        "ok": False, "provider": payload.provider, "model": model,
        "base_url": base, "latency_ms": None, "handshake": None, "detail": None,
    }
    if payload.provider != "mock" and not model:
        out["detail"] = "请先选择/填写模型名"
        return out
    t0 = time.perf_counter()
    try:
        if payload.provider == "mock":
            llm = MockLLM()
        else:
            llm = OpenAIClient(
                api_key=key or "EMPTY",
                base_url=base,
                model=model,
                provider_id=payload.provider,
            )
        res = await llm.classify_intent("你好，在吗")
        out.update(
            ok=True,
            latency_ms=round((time.perf_counter() - t0) * 1000, 1),
            handshake=res,
        )
    except Exception as e:  # 端点不可达 / 模型未加载 / key 失效
        out["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        out["detail"] = f"{type(e).__name__}: {e}"
    return out


@router.get("/ping")
async def ping():
    reg = get_registry()
    pid, cfg = reg.active_config()
    info = {
        "backend": pid,
        "legacy_backend": "mock" if pid == "mock" else "openai",
        "model": cfg.get("model"),
        "base_url": cfg.get("base_url") or None,
        "status": "ok",
        "latency_ms": None,
        "handshake": None,
        "detail": None,
        "rate_limit": get_llm_limiter().status(),
    }
    try:
        llm = get_llm()
        t0 = time.perf_counter()
        info["handshake"] = await llm.classify_intent("你好，在吗")
        info["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        if pid == "external" and not cfg.get("api_key"):
            info["status"] = "warn"
            info["detail"] = "API key 为空，可能回落到演示行为"
    except Exception as e:
        info["status"] = "error"
        # AppError 的 detail 里才有端点原始报错（如 400/500 响应体），带上便于排障
        extra = getattr(e, "detail", None)
        info["detail"] = f"{type(e).__name__}: {e}" + (f" | {extra}" if extra else "")
        info["handshake"] = None
    return info
