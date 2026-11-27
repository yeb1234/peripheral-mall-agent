"""商品与驱动查询接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.db.session import get_db
from app.services import product_service

router = APIRouter(prefix="/api/products", tags=["products"])


@router.get("")
async def list_products(
    db=Depends(get_db),
    category: str | None = Query(None),
    q: str | None = Query(None),
    featured: bool | None = Query(None),
):
    return await product_service.list_products(db, category=category, q=q, featured=featured)


@router.get("/categories")
async def categories(db=Depends(get_db)):
    from sqlalchemy import select, distinct
    from app.db.models import Product
    rows = (await db.execute(select(distinct(Product.category)))).scalars().all()
    return [{"key": c, "label": c} for c in rows]


@router.get("/{ident}")
async def product_detail(ident: str, db=Depends(get_db)):
    prod = await product_service.get_product(db, ident)
    if not prod:
        raise HTTPException(status_code=404, detail="商品不存在")
    return prod
