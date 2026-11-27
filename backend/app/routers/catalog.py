"""商品目录卡：给 P6（VidFlow）等下游一次性取齐做文案所需的商品信息。

数据来源两层：
- 运营口径（价格、库存、状态）走数据库；
- 营销口径（卖点、短/长文案、场景、关键词）走 p4-assets 资产包，
  同一份内容也是 MCP `p4-assets` 供出去的那份，避免两套文案漂移。
"""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select

from app.db.models import Driver, FileAsset, Manual, Product, Resource
from app.db.session import get_db
from app.services import asset_library, file_store

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


def _specs(raw: str) -> dict:
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _flat_specs(specs: dict) -> dict:
    out: dict[str, str] = {}
    for k, v in specs.items():
        if isinstance(v, dict):
            for k2, v2 in v.items():
                out[f"{k}·{k2}"] = str(v2)
        elif isinstance(v, (list, tuple)):
            out[k] = "、".join(str(x) for x in v)
        else:
            out[k] = str(v)
    return out


def _asset(sku: str) -> dict:
    try:
        return asset_library.load_product(sku) or {}
    except Exception:
        return {}


def _asset_points(asset: dict) -> list[str]:
    copy = asset.get("copy") or {}
    points: list[str] = []
    for sp in copy.get("selling_points") or []:
        if isinstance(sp, dict):
            text = sp.get("copy") or f"{sp.get('headline', '')}｜{sp.get('benefit', '')}"
            text = str(text).strip("｜ ")
            if text:
                points.append(text)
        elif isinstance(sp, str) and sp.strip():
            points.append(sp.strip())
    return points


def _selling_points(p: Product, flat: dict, asset: dict) -> list[str]:
    """营销卖点优先（资产包），描述句子补位，规格只用来凑数且不带分组前缀。"""
    pts = _asset_points(asset)
    if len(pts) < 3:
        for seg in re.split(r"[。；;\n]", p.description or ""):
            seg = seg.strip()
            if 6 <= len(seg) <= 40 and seg not in pts:
                pts.append(seg)
            if len(pts) >= 3:
                break
    if len(pts) < 3:
        for k, v in flat.items():
            if len(pts) >= 5:
                break
            label = k.split("·")[-1]
            if v and len(v) <= 30 and label not in {"价格", "库存"}:
                pts.append(f"{label}：{v}")
    return pts[:6]


async def _card(db, p: Product) -> dict:
    flat = _flat_specs(_specs(p.specs))
    asset = _asset(p.sku)
    acopy = asset.get("copy") or {}

    manuals = (await db.execute(select(Manual).where(Manual.product_id == p.id))).scalars().all()
    resources = (await db.execute(select(Resource).where(Resource.product_id == p.id))).scalars().all()
    drivers = (await db.execute(select(Driver).where(Driver.product_id == p.id))).scalars().all()

    files = (await db.execute(
        select(FileAsset).where(FileAsset.product_sku == p.sku)
    )).scalars().all()
    if not files:
        await file_store.ensure_seed(db)
        files = (await db.execute(
            select(FileAsset).where(FileAsset.product_sku == p.sku)
        )).scalars().all()

    points = _selling_points(p, flat, asset)
    short = acopy.get("short_copy") or (f"{p.name}，{points[0]}。" if points else p.name)
    keywords = acopy.get("keywords") or [p.brand, p.category, p.sku] + list(flat.keys())[:3]

    raw_images = asset.get("images") or []
    asset_images = [
        (img.get("url") if isinstance(img, dict) else str(img))
        for img in raw_images
        if (img.get("url") if isinstance(img, dict) else img)
    ]
    images = [p.image] + asset_images if p.image else asset_images

    return {
        "ok": True,
        "sku": p.sku,
        "name": p.name,
        "brand": p.brand,
        "category": p.category,
        "price": p.price,
        "currency": p.currency,
        "stock": p.stock,
        "description": acopy.get("long_copy") or p.description,
        "specs": _specs(p.specs),
        "specs_flat": flat,
        "images": images,
        "selling_points": points,
        "scenes": (asset.get("copy") or {}).get("scenes") or [],
        "copy": {
            "tagline": acopy.get("tagline", ""),
            "lead": acopy.get("lead", ""),
            "headline": f"{p.brand} {p.name}",
            "short": short,
            "long": acopy.get("long_copy") or p.description,
            "keywords": keywords,
        },
        "files": [file_store.to_dict(f) for f in files],
        "manuals": [{"id": m.id, "title": m.title, "version": m.version,
                     "lang": m.lang, "file_url": m.file_url} for m in manuals],
        "resources": [{"id": r.id, "title": r.title, "kind": r.kind,
                       "file_url": r.file_url} for r in resources],
        "drivers": [{"id": d.id, "name": d.name, "version": d.version, "os": d.os,
                     "arch": d.arch, "size_mb": d.size_mb, "file_url": d.file_url}
                    for d in drivers],
        "source": {"product": "p4-postgres", "copy": "p4-assets"},
    }


@router.get("/search")
async def search_catalog(
    q: str = Query("", description="SKU / 名称 / 品牌 / 描述关键词"),
    limit: int = Query(5, ge=1, le=20),
    db=Depends(get_db),
):
    stmt = select(Product)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(
            Product.sku.ilike(like), Product.name.ilike(like),
            Product.brand.ilike(like), Product.description.ilike(like),
        ))
    rows = (await db.execute(stmt.limit(limit))).scalars().all()
    return {"ok": True, "count": len(rows), "items": [
        {"sku": p.sku, "name": p.name, "brand": p.brand, "category": p.category,
         "price": p.price, "stock": p.stock, "image": p.image}
        for p in rows
    ]}


@router.get("/{sku}/card")
async def product_card(sku: str, db=Depends(get_db)):
    p = await db.scalar(select(Product).where(Product.sku == sku.upper()))
    if not p:
        raise HTTPException(status_code=404, detail="product not found")
    return await _card(db, p)
