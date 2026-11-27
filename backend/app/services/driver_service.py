"""驱动下载服务。

演示场景：没有真实二进制，下载接口动态生成一个带元信息的占位文件，
并在种子阶段计算其**真实 SHA256** 存入库，让「下载 → 校验」在 demo 中闭环可用。
"""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import select

from app.db.models import Driver


async def get_driver(db, driver_id: str) -> Driver | None:
    return await db.get(Driver, driver_id)


def _content_text(d: Driver) -> str:
    """占位驱动文件正文（确定性，不含自身哈希，避免循环依赖）。"""
    return (
        f"P4 Peripheral Driver (DEMO PLACEHOLDER)\n"
        f"Name     : {d.name}\n"
        f"Version  : {d.version}\n"
        f"OS/Arch  : {d.os} / {d.arch}\n"
        f"Size     : {d.size_mb} MB\n"
        f"Released : {d.release_date}\n"
        f"Changelog: {d.changelog}\n"
    )


def build_driver_file_content(d: Driver) -> bytes:
    """生成占位驱动文件内容（真实项目这里应返回二进制安装包）。"""
    return _content_text(d).encode("utf-8")


def driver_sha256(d: Driver) -> str:
    """该驱动占位文件的真实 SHA256（种子阶段计算并入库，供前端展示与下载校验）。"""
    return hashlib.sha256(build_driver_file_content(d)).hexdigest()


async def list_all_drivers(db) -> list[dict]:
    from app.db.models import Product

    rows = (
        await db.execute(
            select(Driver, Product.sku, Product.name, Product.image)
            .join(Product, Driver.product_id == Product.id)
            .order_by(Driver.product_id, Driver.os)
        )
    ).all()
    return [
        {
            "id": d.id, "name": d.name, "os": d.os, "arch": d.arch, "version": d.version,
            "size_mb": d.size_mb, "release_date": d.release_date, "file_url": d.file_url,
            "sha256": d.sha256, "product_id": d.product_id, "product_sku": sku,
            "product_name": pname, "product_image": pimg,
        }
        for d, sku, pname, pimg in rows
    ]
