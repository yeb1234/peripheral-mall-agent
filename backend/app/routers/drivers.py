"""驱动下载接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from urllib.parse import quote

from app.db.session import get_db
from app.services import driver_service

router = APIRouter(prefix="/api/drivers", tags=["drivers"])


@router.get("")
async def all_drivers(db=Depends(get_db)):
    return await driver_service.list_all_drivers(db)


@router.get("/{driver_id}/download")
async def download_driver(driver_id: str, db=Depends(get_db)):
    d = await driver_service.get_driver(db, driver_id)
    if not d:
        raise HTTPException(status_code=404, detail="驱动不存在")
    content = driver_service.build_driver_file_content(d)
    # HTTP header 仅支持 latin-1，中文文件名走 RFC 5987 的 filename*=UTF-8'' 编码，
    # 同时保留 ASCII 兜底（filename）。
    ascii_name = f"{d.version}_{d.os}.txt"
    utf8_name = f"{d.name}_{d.version}_{d.os}.txt"
    return StreamingResponse(
        iter([content]),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(utf8_name)}',
                 "X-Checksum-Sha256": d.sha256},
    )
