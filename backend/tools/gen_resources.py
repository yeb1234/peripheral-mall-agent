"""补丁：为已存在的商品生成说明书 + 资源（快速入门 / 保修卡）。

- 若某商品已有对应 Manual / Resource 则跳过，保证可重复执行、不破坏现有数据。
- 复用 resource_service.fill_sha256 计算真实 SHA256，与种子逻辑完全一致。
运行：python -m tools.gen_resources
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.db.models import Driver, Manual, Product, Resource
from app.db.session import AsyncSessionLocal, init_db
from app.services import resource_service as rsvc


async def _fix_driver_file_urls(db) -> int:
    """自愈：修正已存在驱动的 file_url（历史数据误用 product_id，应为 driver 自身 id）。"""
    fixed = 0
    drivers = (await db.execute(select(Driver))).scalars().all()
    for d in drivers:
        want = f"/api/drivers/{d.id}/download?os={d.os}"
        if d.file_url != want:
            d.file_url = want
            fixed += 1
    return fixed


async def main() -> None:
    await init_db()
    async with AsyncSessionLocal() as db:
        products = (await db.execute(select(Product))).scalars().all()
        created = 0
        fixed_drivers = await _fix_driver_file_urls(db)
        for p in products:
            # 说明书：每个商品 1 份中文手册（按 product_id 去重）
            if not (await db.execute(
                select(Manual).where(Manual.product_id == p.id)
            )).first():
                m = Manual(
                    product_id=p.id, title=f"{p.name} 用户手册", lang="zh",
                    version="1.0", pages=24, size_mb=round(2.0 + (p.price % 5) * 0.1, 1),
                    release_date="2026-09-15", file_url="", sha256="",
                )
                db.add(m)
                await db.flush()
                m.file_url = f"/api/resources/manual/{m.id}/download"
                rsvc.fill_sha256("manual", m, p)
                created += 1

            for kind, title, size, date in (
                ("quick_start", f"{p.name} 快速入门指南", 1.2, "2026-09-10"),
                ("warranty", f"{p.name} 保修服务卡", 0.5, "2026-08-01"),
            ):
                if not (await db.execute(
                    select(Resource).where(Resource.product_id == p.id, Resource.kind == kind)
                )).first():
                    r = Resource(
                        product_id=p.id, title=title, kind=kind,
                        size_mb=size, release_date=date, file_url="", sha256="",
                    )
                    db.add(r)
                    await db.flush()
                    r.file_url = f"/api/resources/resource/{r.id}/download"
                    rsvc.fill_sha256("resource", r, p)
                    created += 1

        await db.commit()
        print(f"资源补丁完成：新增 {created} 条（说明书 / 资源）；修正驱动下载地址 {fixed_drivers} 条。已存在的商品不会重复生成。")


if __name__ == "__main__":
    asyncio.run(main())
