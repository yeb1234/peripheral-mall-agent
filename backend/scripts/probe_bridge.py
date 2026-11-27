"""P4 ↔ P6 全链路冒烟：HTTP、MCP、Agent 工具三条通道一次跑完。

覆盖：
  P4  health / vector / files / catalog
  P6  health / copy（P6 反查 P4 商品卡）
  P4→P6  p6_client 直连（Agent 工具底层）
  MCP  P4 /mcp  tools/list + get_product_card
  MCP  P6 /mcp  tools/list + generate_product_copy

用法：
  python scripts/probe_bridge.py
  python scripts/probe_bridge.py --p4 http://127.0.0.1:8000 --p6 http://127.0.0.1:12706
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

checks: list[dict] = []


def _add(name: str, ok: bool, detail: str = "") -> None:
    checks.append({"name": name, "ok": bool(ok), "detail": detail[:300]})
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail[:160]}")


async def http_suite(p4: str, p6: str, sku: str) -> dict:
    async with httpx.AsyncClient(timeout=25) as c:
        r = await c.get(f"{p4}/api/health")
        _add("p4.health", r.status_code == 200, r.text[:120])

        r = await c.get(f"{p4}/api/vector/status")
        data = r.json() if r.status_code == 200 else {}
        _add("p4.vector.status", data.get("ok") and data.get("total", 0) > 0,
             f"total={data.get('total')} by_kind={data.get('by_kind')}")

        r = await c.get(f"{p4}/api/vector/search", params={"q": "热插拔 机械键盘", "kind": "product", "top_k": 3})
        data = r.json() if r.status_code == 200 else {}
        _add("p4.vector.search", bool(data.get("results")), f"top={data.get('results', [{}])[0].get('source_id')}")

        r = await c.get(f"{p4}/api/files/manifest")
        data = r.json() if r.status_code == 200 else {}
        _add("p4.files.manifest", data.get("count", 0) > 0, f"count={data.get('count')}")

        r = await c.get(f"{p4}/api/catalog/{sku}/card")
        data = r.json() if r.status_code == 200 else {}
        _add("p4.catalog.card", bool(data.get("selling_points")) and bool(data.get("files")),
             f"points={len(data.get('selling_points') or [])} files={len(data.get('files') or [])}")

        r = await c.get(f"{p6}/api/health")
        data = r.json() if r.status_code == 200 else {}
        _add("p6.health", r.status_code == 200, json.dumps(data, ensure_ascii=False)[:150])

        r = await c.post(f"{p6}/api/copy/generate", json={"sku": sku, "channel": "detail", "tone": "专业"})
        data = r.json() if r.status_code == 200 else {}
        copy = data.get("copy") or {}
        _add("p6.copy.rest", bool(data.get("ok")) and bool(copy.get("selling_points")),
             f"title={copy.get('title')} points={len(copy.get('selling_points') or [])}")

    from app.core import p6_client
    res = await p6_client.generate_copy(sku, channel="short_video", tone="科技感")
    _add("p4.to.p6.client", bool(res.get("ok")),
         f"source={res.get('source')} reason={res.get('reason', '')}")

    # Agent 工具层：确认 generate_product_copy 已注册，且 handler 能拿到完整文案
    from app.agents.tools.base import TOOLS, ToolContext
    from app.agents.tools.copy_tool import _generate_product_copy

    ctx = ToolContext(db=None, session_id="probe", buyer_id="probe")
    out = await _generate_product_copy(None, ctx, {"product_sku": sku, "channel": "detail"})
    _add("p4.tool.generate_product_copy",
         "generate_product_copy" in TOOLS and bool(out.get("data", {}).get("copy")),
         f"registered={'generate_product_copy' in TOOLS} obs={str(out.get('observation'))[:100]}")
    return res


async def _mcp_call(url: str, tool: str, args: dict, label: str) -> None:
    try:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(url, timeout=10) as stream:
            read, write = stream[0], stream[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = [t.name for t in tools.tools]
                _add(f"{label}.mcp.list", tool in names, f"tools={len(names)}")

                result = await session.call_tool(tool, args)
                text = "".join(getattr(item, "text", "") for item in result.content)
                _add(f"{label}.mcp.call", bool(text), text[:180])
    except Exception as e:
        _add(f"{label}.mcp", False, f"{type(e).__name__}: {e}")


async def mcp_suite(p4: str, p6: str, sku: str) -> None:
    await _mcp_call(f"{p4}/mcp", "get_product_card", {"sku": sku}, "p4")
    await _mcp_call(f"{p6}/mcp", "generate_product_copy", {"sku": sku, "channel": "social"}, "p6")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--p4", default="http://127.0.0.1:8000")
    ap.add_argument("--p6", default="http://127.0.0.1:12706")
    ap.add_argument("--sku", default="KB-PRO87")
    args = ap.parse_args()

    t0 = time.perf_counter()
    await http_suite(args.p4.rstrip("/"), args.p6.rstrip("/"), args.sku)
    await mcp_suite(args.p4.rstrip("/"), args.p6.rstrip("/"), args.sku)

    passed = sum(1 for c in checks if c["ok"])
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "p4": args.p4, "p6": args.p6, "sku": args.sku,
        "passed": passed, "total": len(checks),
        "elapsed_s": round(time.perf_counter() - t0, 2),
        "checks": checks,
    }
    out = ROOT / "docs" / "reports" / "bridge_test_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{passed}/{len(checks)} passed -> {out}")
    if passed != len(checks):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
