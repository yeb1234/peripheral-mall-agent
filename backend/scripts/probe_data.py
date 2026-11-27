import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select
from app.db.models import Product
from app.db.session import AsyncSessionLocal


async def main():
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(Product))).scalars().all()
        for p in rows:
            print(f"{p.sku:14} {p.category:9} ¥{p.price:<6.0f} stock={p.stock:<4} {p.name}")
        print("\n--- HS-ANC7 specs ---")
        p = (await db.execute(select(Product).where(Product.sku == "HS-ANC7"))).scalar_one()
        print(p.specs[:900])
        print("\n--- MS-LITE / 相关鼠标 description ---")
        for p in rows:
            if p.category == "mouse":
                print(f"{p.sku}: {p.description[:80]} | specs={p.specs[:200]}")

asyncio.run(main())
