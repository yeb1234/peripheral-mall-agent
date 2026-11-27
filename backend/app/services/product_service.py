"""商品与驱动查询服务（异步、只读为主）。"""
from __future__ import annotations

import json

from sqlalchemy import select

from app.db.models import Driver, Product


async def list_products(db, *, category: str | None = None, q: str | None = None,
                        featured: bool | None = None) -> list[dict]:
    stmt = select(Product)
    if category:
        stmt = stmt.where(Product.category == category)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            Product.name.ilike(like) | Product.brand.ilike(like)
            | Product.sku.ilike(like) | Product.description.ilike(like)
        )
    if featured is not None:
        stmt = stmt.where(Product.featured == featured)
    stmt = stmt.order_by(Product.featured.desc(), Product.price.asc())
    rows = (await db.execute(stmt)).scalars().all()
    return [_product_dict(p) for p in rows]


async def get_product(db, ident: str) -> dict | None:
    prod = await db.get(Product, ident)
    if not prod:
        prod = (await db.execute(select(Product).where(Product.sku == ident))).scalar_one_or_none()
    if not prod:
        return None
    d = _product_dict(prod)
    d["drivers"] = await list_drivers(db, prod.id)
    return d


async def list_drivers(db, product_id: str) -> list[dict]:
    rows = (await db.execute(
        select(Driver).where(Driver.product_id == product_id).order_by(Driver.os)
    )).scalars().all()
    return [_driver_dict(d) for d in rows]


def _product_dict(p: Product) -> dict:
    return {
        "id": p.id, "sku": p.sku, "name": p.name, "brand": p.brand,
        "category": p.category, "price": p.price, "currency": p.currency,
        "stock": p.stock, "description": p.description,
        "specs": json.loads(p.specs), "image": p.image, "featured": p.featured,
    }


def _driver_dict(d: Driver) -> dict:
    return {
        "id": d.id, "name": d.name, "version": d.version, "os": d.os, "arch": d.arch,
        "size_mb": d.size_mb, "release_date": d.release_date, "changelog": d.changelog,
        "file_url": d.file_url, "sha256": d.sha256,
    }
