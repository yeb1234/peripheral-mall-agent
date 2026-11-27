"""轻量压测：给「工程化」补上可复现的并发证据。

三个目标，按需要选：
  products  纯 DB 读（/api/products），测 FastAPI + PG 连接池基线，不花钱
  vector    向量检索（/api/vector/search），会调 embedding API，注意费用
  chat      SSE 对话（/api/chat），走完整 Agent + LLM，最贵也最真实

用法：
  python scripts/loadtest.py --target products -c 20 -n 200
  python scripts/loadtest.py --target vector   -c 5  -n 30
  python scripts/loadtest.py --target chat     -c 3  -n 10 --message "KB-PRO87 多少钱"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]


async def _hit(client: httpx.AsyncClient, target: str, message: str) -> tuple[bool, float, int]:
    t0 = time.perf_counter()
    try:
        if target == "products":
            r = await client.get("/api/products")
        elif target == "vector":
            r = await client.get("/api/vector/search", params={"q": "机械键盘 热插拔", "top_k": 3})
        else:
            payload = {"message": message, "buyer_id": "loadtest_buyer", "channel": "web"}
            async with client.stream("POST", "/api/chat", json=payload) as r:
                async for _ in r.aiter_lines():
                    pass
        ok = r.status_code == 200
        return ok, (time.perf_counter() - t0) * 1000, r.status_code
    except Exception:
        return False, (time.perf_counter() - t0) * 1000, 0


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    idx = min(len(values) - 1, max(0, int(round((p / 100) * (len(values) - 1)))))
    return round(values[idx], 1)


async def run(args) -> dict:
    sem = asyncio.Semaphore(args.concurrency)
    latencies: list[float] = []
    codes: dict[int, int] = {}

    async with httpx.AsyncClient(base_url=args.base_url, timeout=args.timeout) as client:
        async def worker(n: int):
            for _ in range(n):
                async with sem:
                    ok, ms, code = await _hit(client, args.target, args.message)
                latencies.append(ms)
                codes[code] = codes.get(code, 0) + 1

        per_worker = max(1, args.requests // args.concurrency)
        t0 = time.perf_counter()
        await asyncio.gather(*[worker(per_worker) for _ in range(args.concurrency)])
        elapsed = time.perf_counter() - t0

    total = len(latencies)
    ok = codes.get(200, 0)
    return {
        "target": args.target,
        "base_url": args.base_url,
        "concurrency": args.concurrency,
        "requests": total,
        "ok": ok,
        "error": total - ok,
        "rps": round(total / elapsed, 1) if elapsed else 0,
        "latency_ms": {
            "avg": round(statistics.mean(latencies), 1) if latencies else 0,
            "p50": _pct(latencies, 50),
            "p95": _pct(latencies, 95),
            "p99": _pct(latencies, 99),
            "max": round(max(latencies), 1) if latencies else 0,
        },
        "status_codes": codes,
        "elapsed_s": round(elapsed, 2),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=["products", "vector", "chat"], default="products")
    ap.add_argument("-c", "--concurrency", type=int, default=10)
    ap.add_argument("-n", "--requests", type=int, default=100)
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--message", default="KB-PRO87 多少钱")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    result = asyncio.run(run(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    out = Path(args.out) if args.out else ROOT / "docs" / "reports" / f"loadtest_{args.target}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[loadtest] saved -> {out}")


if __name__ == "__main__":
    main()
