"""资源下载接口：驱动 + 说明书 + 资源统一入口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from urllib.parse import quote

from app.db.session import get_db
from app.services import resource_service as svc

router = APIRouter(prefix="/api/resources", tags=["resources"])


@router.get("")
async def list_resources(
    keyword: str = "",
    category: str = "",
    kind: str = "all",
    db=Depends(get_db),
):
    """汇总驱动 / 说明书 / 资源。

    - keyword：按型号(SKU) / 名称 / 标题模糊匹配
    - category：商品品类过滤（keyboard / mouse / headset / webcam / dock / gamepad）
    - kind：driver / manual / resource / all
    """
    return await svc.list_resources(db, keyword=keyword, category=category, kind=kind)


@router.get("/{kind}/{item_id}/download")
async def download(kind: str, item_id: str, db=Depends(get_db)):
    res = await svc.get_item(db, kind, item_id)
    if not res:
        raise HTTPException(status_code=404, detail="资源不存在")
    item, k = res
    content = svc.build_file_content(k, item)
    # HTTP header 仅支持 latin-1，中文文件名需走 RFC 5987 的 filename*=UTF-8'' 编码，
    # 同时保留 ASCII 兜底（filename），避免 Starlette 抛 UnicodeEncodeError。
    ascii_name = f"{item['product_sku']}_{k}.txt"
    utf8_name = f"{item['product_sku']}_{item['title']}_{k}.txt"
    return StreamingResponse(
        iter([content]),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(utf8_name)}",
            "X-Checksum-Sha256": item["sha256"],
        },
    )
