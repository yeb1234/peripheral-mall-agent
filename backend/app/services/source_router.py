"""信源路由：内部事实优先，公开网络兜底。

严格对比的取值顺序（不可调换）：
1. P3 内部事实库（参数/跑分/评测，带来源与置信度）
2. P4 商品库（自营 SKU 的价格/库存/规格，属经营事实）
3. websearch 公开网络（只补内部库没有的部分，回复必须标注来源）

这一层只判断 P3 是否覆盖，不替调用方做联网决定：
- P3 覆盖 → source=p3，可直接出结论；
- 部分覆盖 → source=p3_partial，缺口单独列出，避免把内部数值和网文混成一张表；
- 未覆盖/解析失败 → source=none，附 fallback_query 给工具层走第二信源。
"""
from __future__ import annotations

from app.core import p3_client

P3_MIN_COVERAGE = 2


def fallback_query(models: list[str]) -> str:
    return " ".join(models) + " 参数 评测 对比"


async def resolve_models(models: list[str], kind: str | None = None) -> dict:
    cleaned = [str(m).strip() for m in (models or []) if str(m).strip()]
    if len(cleaned) < 2:
        return {"ok": False, "source": "none", "reason": "need_two_models", "fallback_query": ""}
    if not p3_client.enabled():
        return {"ok": False, "source": "none", "reason": "p3_disabled",
                "fallback_query": fallback_query(cleaned)}

    data = await p3_client.compare(cleaned, kind=kind)
    if data.get("error"):
        return {"ok": False, "source": "none", "reason": str(data["error"])[:240],
                "fallback_query": fallback_query(cleaned), "p3_error": data}

    products = data.get("products") or []
    covered = [p for p in products if p.get("facts")]
    missing = [p.get("name") or p.get("id") for p in products if not p.get("facts")]
    ratio = round(len(covered) / max(1, len(cleaned)), 2)

    if len(covered) >= min(P3_MIN_COVERAGE, len(cleaned)):
        return {"ok": True, "source": "p3", "data": data, "coverage": ratio,
                "missing": missing, "fallback_query": ""}
    if covered:
        return {"ok": False, "source": "p3_partial", "data": data, "coverage": ratio,
                "missing": missing, "reason": "coverage_low",
                "fallback_query": fallback_query(missing or cleaned)}
    return {"ok": False, "source": "none", "reason": "p3_no_facts", "data": data,
            "missing": missing, "fallback_query": fallback_query(cleaned)}
