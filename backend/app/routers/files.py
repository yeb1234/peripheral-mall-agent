"""文件资产 HTTP 接口：清单、元数据、下载。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from app.db.models import FileAsset
from app.db.session import get_db
from app.services import file_store

router = APIRouter(prefix="/api/files", tags=["files"])


@router.get("/manifest")
async def get_manifest(
    kind: str = Query("", description="manual/resource/driver，空为全部"),
    sku: str = Query("", description="按商品 SKU 过滤"),
    db=Depends(get_db),
):
    """文件库清单。首次访问会幂等落盘，返回可下载 URL。"""
    seed = await file_store.ensure_seed(db)
    items = await file_store.manifest(db, kind=kind or None, sku=sku or None)
    return {"ok": True, "count": len(items), "root": seed.get("root"), "items": items}


@router.get("/{asset_id}/meta")
async def asset_meta(asset_id: str, db=Depends(get_db)):
    asset = await db.get(FileAsset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="asset not found")
    return {"ok": True, "asset": file_store.to_dict(asset)}


@router.get("/{asset_id}")
async def download(asset_id: str, db=Depends(get_db)):
    asset = await db.get(FileAsset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="asset not found")
    path = file_store.abspath(asset.rel_path)
    if path is None:
        raise HTTPException(status_code=404, detail="asset file missing on disk")
    return FileResponse(path, filename=asset.filename, media_type=asset.mime)
