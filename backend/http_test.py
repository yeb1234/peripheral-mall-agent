"""
HTTP / SSE 集成测试（零依赖：mock LLM + memory 缓存 + 独立 SQLite）。

关键点：必须用 `with TestClient(app) as c:` 进入上下文，
否则 Starlette 不会触发 lifespan（init_db + seed 不会执行，会报 no such table）。

运行：
    python http_test.py
"""
import json
import os

# 必须在 import app 之前设置，让 session.py 在 import 时构建正确的 engine 路径
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.abspath("./http_test.db")
os.environ.setdefault("CACHE_BACKEND", "memory")
os.environ.setdefault("LLM_BACKEND", "mock")

from fastapi.testclient import TestClient

from app.main import app

# 关键：with 块会触发 lifespan（建表 + seed）
with TestClient(app) as c:
    print("health:", c.get("/api/health").json())

    r = c.get("/api/products?featured=true")
    print("products featured count:", len(r.json()))

    r = c.get("/api/products/KB-PRO87")
    p = r.json()
    print("product KB-PRO87 drivers:", len(p.get("drivers", [])))

    # SSE chat
    print("\n--- SSE chat ---")
    with c.stream("POST", "/api/chat", json={"buyer_id": "buyer_demo", "message": "KB-PRO87 支持 macOS 吗"}) as resp:
        assert resp.status_code == 200, resp.status_code
        buf = ""
        meta = None
        for line in resp.iter_lines():
            if line.startswith("data:"):
                data = line[5:].strip()
                if data and data != "{}":
                    try:
                        obj = json.loads(data)
                        if isinstance(obj, dict) and "event" not in obj and ("session_id" in obj or "intent" in obj):
                            meta = obj
                    except Exception:
                        buf += data
        print("streamed response head:", buf[:60].replace("\n", " "))
        print("meta:", meta)

    # create order
    r = c.post("/api/orders", json={
        "buyer_id": "buyer_demo",
        "lines": [{"sku": "MS-ULTRA", "qty": 1}],
        "address": {"province": "广东省", "city": "深圳市", "district": "南山区",
                    "detail": "科技园 9 号", "contact": "测试", "phone": "13800001234"},
    })
    j = r.json()
    print("\ncreate order:", r.status_code, j.get("status"), j.get("total"))

    # workbench audits
    r = c.get("/api/workbench/audits")
    print("audit events:", len(r.json()))

print("\nHTTP TEST OK")
