"""一次性补丁：把 products.image 从「品类图」改为「按 SKU 一一对应的图」。

不删除任何数据（保留订单 / 记忆 / 任务等演示数据），只更新 image 字段。
运行：/tmp/p4venv/Scripts/python.exe tools/fix_product_images.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, update

from app.db.models import Product
from app.db.session import AsyncSessionLocal, init_db


async def main() -> None:
    await init_db()
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(Product.id, Product.sku))).all()
        n = 0
        for pid, sku in rows:
            await db.execute(
                update(Product)
                .where(Product.id == pid)
                .values(image=f"/static/products/{sku}.svg")
            )
            n += 1
        await db.commit()
        print(f"updated {n} products -> per-SKU images")


if __name__ == "__main__":
    asyncio.run(main())
