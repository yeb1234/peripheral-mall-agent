"""资源下载服务：驱动 + 说明书 + 资源（快速入门 / FAQ / 保修卡等）统一汇总。

演示场景没有真实二进制，下载接口动态生成带元信息的占位文件，
并在种子阶段计算其真实 SHA256 入库，让「下载 → 校验」在 demo 中闭环可用。
"""
from __future__ import annotations

import hashlib

from sqlalchemy import select

from app.db.models import Driver, Manual, Product, Resource
from app.services import manual_content

KIND_LABELS = {"driver": "驱动", "manual": "说明书", "resource": "资源"}
RESOURCE_KIND_LABELS = {
    "quick_start": "快速入门",
    "faq": "常见问题",
    "warranty": "保修卡",
    "software": "配套软件",
    "template": "模板",
}


def _serialize(m, p, kind_name: str) -> dict:
    base = {
        "id": m.id,
        "kind": kind_name,
        "title": m.name if kind_name == "driver" else m.title,
        "size_mb": m.size_mb,
        "release_date": m.release_date,
        "file_url": m.file_url,
        "sha256": m.sha256,
        "product_id": m.product_id,
        "product_sku": p.sku,
        "product_name": p.name,
        "product_image": p.image,
        "brand": p.brand,
        "category": p.category,
    }
    if kind_name == "driver":
        base.update({"os": m.os, "arch": m.arch, "version": m.version})
    elif kind_name == "manual":
        base.update({"lang": m.lang, "version": m.version, "pages": m.pages})
    elif kind_name == "resource":
        base.update({"resource_kind": m.kind})
    return base


def fill_sha256(kind: str, m, p) -> None:
    """种子阶段调用：根据占位内容计算真实 SHA256 并写回模型实例。"""
    d = _serialize(m, p, kind)
    m.sha256 = item_sha256(kind, d)


def _placeholder_text(kind: str, item: dict) -> str:
    if kind == "driver":
        return (
            f"P4 Peripheral Driver (DEMO PLACEHOLDER)\n"
            f"Name     : {item['title']}\n"
            f"Version  : {item['version']}\n"
            f"OS/Arch  : {item['os']} / {item.get('arch', '')}\n"
            f"Size     : {item['size_mb']} MB\n"
            f"Released : {item['release_date']}\n"
        )
    if kind == "manual":
        return (
            f"P4 Peripheral User Manual (DEMO PLACEHOLDER)\n"
            f"Product  : {item['product_name']} ({item['product_sku']})\n"
            f"Title    : {item['title']}\n"
            f"Language : {item['lang']}\n"
            f"Version  : {item['version']}\n"
            f"Pages    : {item['pages']}\n"
            f"Released : {item['release_date']}\n"
        )
    return (
        f"P4 Peripheral Resource (DEMO PLACEHOLDER)\n"
        f"Product  : {item['product_name']} ({item['product_sku']})\n"
        f"Title    : {item['title']}\n"
        f"Type     : {RESOURCE_KIND_LABELS.get(item['resource_kind'], item['resource_kind'])}\n"
        f"Released : {item['release_date']}\n"
    )


def build_file_content(kind: str, item: dict) -> bytes:
    """说明书 / 资源返回结构化全文，且与知识库切片共用同一份正文。

    driver 仍是二进制占位（没有章节可切）；说明书与资源的下载文件因此
    真正「有内容」，客服引用的知识库章节与用户下载到的文档不会不一致。
    """
    if kind in ("manual", "resource"):
        return manual_content.render_document(kind, item).encode("utf-8")
    return _placeholder_text(kind, item).encode("utf-8")


def item_sha256(kind: str, item: dict) -> str:
    return hashlib.sha256(build_file_content(kind, item)).hexdigest()


async def list_resources(
    db, keyword: str = "", category: str = "", kind: str = "all"
) -> list[dict]:
    out: list[dict] = []
    kw = (keyword or "").strip().lower()

    async def _collect(model, kind_name):
        rows = (
            await db.execute(
                select(model, Product).join(Product, model.product_id == Product.id)
            )
        ).all()
        for m, p in rows:
            if kw:
                name_field = m.name if kind_name == "driver" else m.title
                hay = f"{p.sku} {p.name} {name_field}".lower()
                if kw not in hay:
                    continue
            if category and p.category != category:
                continue
            out.append(_serialize(m, p, kind_name))

    await _collect(Driver, "driver")
    await _collect(Manual, "manual")
    await _collect(Resource, "resource")

    if kind and kind != "all":
        out = [o for o in out if o["kind"] == kind]
    out.sort(key=lambda o: (o["product_sku"], o["kind"]))
    return out


async def get_item(db, kind: str, item_id: str):
    table = {"driver": Driver, "manual": Manual, "resource": Resource}.get(kind)
    if not table:
        return None
    row = (
        await db.execute(
            select(table, Product)
            .join(Product, table.product_id == Product.id)
            .where(table.id == item_id)
        )
    ).first()
    if not row:
        return None
    m, p = row
    return _serialize(m, p, kind), kind
