"""直测新增的 5 个业务服务工具（不走 LLM、不走图，只测 handler 事实层）。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.tools.base import ToolContext, TOOLS
import app.agents.tools.read_tools  # noqa: F401
import app.agents.tools.service_tools  # noqa: F401
from app.db.session import AsyncSessionLocal

CASES = [
    ("compare_products", {"skus": ["KB-PRO87", "KB-60HE"]}),
    ("recommend_config", {"budget": 3000, "usage": "打电竞，FPS 为主"}),
    ("check_compatibility", {"product_sku": "KB-PRO87", "target": "macOS"}),
    ("check_compatibility", {"product_sku": "HS-ANC7", "target": "PS5"}),
    ("diagnose_issue", {"product_sku": "HS-ANC7", "symptom": "左耳没声音"}),
    ("query_policy", {"topic": "保修多久", "product_sku": "KB-PRO87"}),
    ("query_policy", {"topic": "几天能到", "region": "广东"}),
]


async def main():
    print("registered:", len(TOOLS), "->", sorted(TOOLS))
    async with AsyncSessionLocal() as db:
        ctx = ToolContext(db=db, session_id="probe", buyer_id="buyer_demo", user_text="")
        for name, args in CASES:
            tool = TOOLS.get(name)
            if not tool:
                print(f"\n### {name} MISSING")
                continue
            try:
                res = await tool["handler"](db, ctx, args, None)
                obs = res.get("observation") if isinstance(res, dict) else res
                print(f"\n### {name} {args}\n{obs}")
            except Exception as e:
                import traceback
                print(f"\n### {name} {args} ERROR: {e}")
                traceback.print_exc()


asyncio.run(main())
