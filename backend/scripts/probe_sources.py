"""信源优先级冒烟：P3（第一信源）→ 本店商品库 → 公开网络（第二信源）。

覆盖：
  P3 MCP 工具清单 / 实时 compare
  source_router：P3 覆盖（RTX 5080 vs RX 9070 XT）、P3 未覆盖（外设竞品）
  compare_products：本店 SKU（p4_catalog）、市场型号（p3）、外设竞品（p3→websearch）

用法：
  python scripts/probe_sources.py
  python scripts/probe_sources.py --no-web
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

# 必须先设环境，再 import app（settings 在 import 时读取）
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://p4:p4pass@127.0.0.1:5432/p4agent")
os.environ.setdefault("CACHE_BACKEND", "redis")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("P3_MCP_URL", "http://127.0.0.1:12415/mcp")
os.environ.setdefault("PGVECTOR_ENABLED", "true")

checks: list[dict] = []


def _add(name: str, ok: bool, detail: str = "") -> None:
    checks.append({"name": name, "ok": bool(ok), "detail": detail[:300]})
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail[:160]}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-web", action="store_true", help="跳过第二信源联网（只测 P3/商品库）")
    args = ap.parse_args()

    from app.core import p3_client
    from app.services import source_router

    # 1. P3 MCP 存活与工具面
    try:
        tools = await p3_client.list_tools()
        names = [t["name"] for t in tools]
        _add("p3.mcp.tools", {"compare_products", "get_product_facts", "search_products"} <= set(names),
             f"tools={names}")
    except Exception as exc:
        _add("p3.mcp.tools", False, f"{type(exc).__name__}: {exc}")

    # 2. P3 实时对比（已入库两款 GPU）
    data = await p3_client.compare(["RTX 5080", "RX 9070 XT"])
    _add("p3.compare", not data.get("error") and len(data.get("products") or []) >= 2,
         f"scene={data.get('scene')} metric={data.get('metric')} products={len(data.get('products') or [])}")

    # 3. 路由器：P3 覆盖
    r = await source_router.resolve_models(["RTX 5080", "RX 9070 XT"])
    _add("router.p3", bool(r.get("ok")) and r.get("source") == "p3",
         f"source={r.get('source')} coverage={r.get('coverage')} "
         f"missing={r.get('missing')} reason={str(r.get('reason'))[:100]}")

    # 4. 路由器：外设竞品（P3 无 domain，应判未覆盖）
    r2 = await source_router.resolve_models(["Keychron Q1", "KB-PRO87"])
    _add("router.peripheral_miss", (not r2.get("ok")) and r2.get("source") in ("none", "p3_partial"),
         f"source={r2.get('source')} reason={str(r2.get('reason'))[:120]}")

    # 5. 工具层：本店 SKU 对比走 p4_catalog
    from app.agents.tools.base import ToolContext
    from app.agents.tools.service_tools import _compare_products
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        ctx = ToolContext(db=db, session_id="probe-sources", buyer_id="probe")
        out = await _compare_products(db, ctx, {"skus": ["KB-PRO87", "KB-60HE"]})
        chain = (out.get("data") or {}).get("source_chain")
        _add("tool.local_sku", chain == ["p4_catalog"], f"chain={chain}")

        # 6. 工具层：市场型号 → P3
        out2 = await _compare_products(db, ctx, {"skus": ["RTX 5080", "RX 9070 XT"]})
        chain2 = (out2.get("data") or {}).get("source_chain")
        _add("tool.market_p3", chain2 == ["p3"], f"chain={chain2} obs={out2.get('observation', '')[:120]}")

        # 7. 工具层：外设竞品 → P3 未覆盖 → 第二信源
        if args.no_web:
            _add("tool.peripheral_web", True, "skipped (--no-web)")
        else:
            out3 = await _compare_products(db, ctx, {"skus": ["Keychron Q1", "KB-PRO87"]})
            chain3 = (out3.get("data") or {}).get("source_chain")
            _add("tool.peripheral_web", bool(chain3) and chain3[0] == "p3",
                 f"chain={chain3} obs={out3.get('observation', '')[:120]}")

    passed = sum(1 for c in checks if c["ok"])
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "passed": passed, "total": len(checks), "checks": checks,
    }
    out_path = ROOT / "docs" / "reports" / "source_test_report.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{passed}/{len(checks)} passed -> {out_path}")
    if passed != len(checks):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
