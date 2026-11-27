"""文件资产库：文档/驱动正文落盘，DB 只存元数据与 sha256。

存储根目录由 ``STORAGE_DIR`` 控制（compose 里是挂载卷 /data/storage）。
换成 MinIO/S3 时，只需把 ``_write`` 与 ``abspath`` 换成对象存储客户端，
FileAsset.rel_path 语义改为 object_key，表结构与调用方都不用动。
"""
from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.db.models import Driver, FileAsset, Product
from app.services import knowledge_service

log = logging.getLogger(__name__)


def root_dir() -> Path:
    return Path(settings.STORAGE_DIR).expanduser().resolve()


def _safe(name: str) -> str:
    name = re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+", "-", str(name)).strip("-")
    return (name or "file")[:120]


def _write(kind: str, sku: str, filename: str, content: str) -> tuple[str, int, str]:
    folder = root_dir() / _safe(kind) / _safe(sku or "general")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / _safe(filename)
    path.write_text(content, encoding="utf-8")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    rel = path.relative_to(root_dir()).as_posix()
    return rel, len(content.encode("utf-8")), digest


def _manual_doc(src: dict) -> str:
    item = src["item"]
    head = (
        f"# {item.get('product_name', '')} {item.get('title', '')}\n\n"
        f"- SKU：{item.get('product_sku', '')}\n"
        f"- 类型：{src['kind']}\n"
        f"- 版本：{item.get('version', '')}　语言：{item.get('lang', 'zh')}\n"
        f"- 来源地址：{item.get('file_url', '') or '（仓库内生成）'}\n\n"
        "---\n\n"
    )
    return head + src["full_text"].strip() + "\n"


def _driver_doc(p: Product | None, d: Driver) -> str:
    sku = p.sku if p else ""
    name = p.name if p else d.name
    return (
        f"# {sku} {name} — {d.os}/{d.arch} 驱动 {d.version}\n\n"
        f"- 文件名：{d.name}\n"
        f"- 版本：{d.version}　平台：{d.os}/{d.arch}\n"
        f"- 大小：{d.size_mb:g} MB　发布日期：{d.release_date}\n"
        f"- SHA256：{d.sha256 or '（未提供）'}\n"
        f"- 原始地址：{d.file_url or '（演示占位）'}\n\n"
        "## 更新说明\n\n"
        f"{d.changelog or '无'}\n\n"
        "## 安装步骤\n\n"
        "1. 关闭占用设备的应用（键盘/鼠标驱动助手等）。\n"
        "2. 以管理员/root 运行安装包，按提示重启。\n"
        "3. 在系统设备管理器确认固件版本与本文档一致。\n\n"
        "> 演示说明：仓库不分发真实二进制。生产接入对象存储后，把 file_url 指向\n"
        "> 带签名的下载地址即可；本文件仅作为可检索、可下载的驱动说明包。\n"
    )


async def ensure_seed(db) -> dict:
    """把说明书/资源/驱动幂等落盘，并登记 FileAsset。内容没变就跳过。"""
    written = skipped = failed = 0
    try:
        sources = await knowledge_service._sources(db)
    except Exception as e:
        log.error("file seed: collect sources failed: %s", e)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}

    for src in sources:
        item = src["item"]
        kind = src["kind"]
        try:
            content = _manual_doc(src)
            rel, size, digest = _write(kind, item.get("product_sku", ""),
                                       f"{item.get('product_sku', 'general')}-{kind}-{item['id'][:8]}.md",
                                       content)
            existing = await db.scalar(select(FileAsset).where(
                FileAsset.kind == kind, FileAsset.source_id == item["id"]))
            if existing and existing.sha256 == digest:
                skipped += 1
                continue
            if existing:
                existing.title = item.get("title", "")
                existing.filename = Path(rel).name
                existing.rel_path = rel
                existing.size_bytes = size
                existing.sha256 = digest
                existing.product_sku = item.get("product_sku", "")
            else:
                db.add(FileAsset(
                    kind=kind, product_sku=item.get("product_sku", ""),
                    title=item.get("title", ""), filename=Path(rel).name,
                    rel_path=rel, mime="text/markdown", size_bytes=size,
                    sha256=digest, source_kind=kind, source_id=item["id"],
                ))
            written += 1
        except Exception as e:
            failed += 1
            log.warning("file seed failed for %s: %s", item.get("id"), e)

    # 驱动：存在 Driver 表但没有可分发文件，生成说明包（真实二进制接对象存储）
    drivers = (await db.execute(select(Driver))).scalars().all()
    products = {p.id: p for p in (await db.execute(select(Product))).scalars().all()}
    for d in drivers:
        try:
            p = products.get(d.product_id)
            content = _driver_doc(p, d)
            rel, size, digest = _write("driver", p.sku if p else "",
                                       f"{(p.sku if p else 'driver')}-{d.os}-{d.version}.md", content)
            existing = await db.scalar(select(FileAsset).where(
                FileAsset.kind == "driver", FileAsset.source_id == d.id))
            if existing and existing.sha256 == digest:
                skipped += 1
                continue
            if existing:
                existing.title = f"{d.name} {d.version}"
                existing.rel_path = rel
                existing.filename = Path(rel).name
                existing.size_bytes = size
                existing.sha256 = digest
                existing.product_sku = p.sku if p else ""
            else:
                db.add(FileAsset(
                    kind="driver", product_sku=p.sku if p else "",
                    title=f"{d.name} {d.version}", filename=Path(rel).name,
                    rel_path=rel, mime="text/markdown", size_bytes=size,
                    sha256=digest, source_kind="driver", source_id=d.id,
                ))
            written += 1
        except Exception as e:
            failed += 1
            log.warning("driver seed failed for %s: %s", d.id, e)

    await db.commit()
    total = await db.scalar(select(FileAsset.id).limit(1))
    return {"ok": True, "written": written, "skipped": skipped, "failed": failed,
            "root": str(root_dir()), "has_assets": total is not None}


async def manifest(db, kind: str | None = None, sku: str | None = None) -> list[dict]:
    stmt = select(FileAsset)
    if kind:
        stmt = stmt.where(FileAsset.kind == kind)
    if sku:
        stmt = stmt.where(FileAsset.product_sku == sku.upper())
    rows = (await db.execute(stmt.order_by(FileAsset.kind, FileAsset.product_sku))).scalars().all()
    return [to_dict(a) for a in rows]


def to_dict(a: FileAsset) -> dict:
    return {
        "id": a.id,
        "kind": a.kind,
        "sku": a.product_sku,
        "title": a.title,
        "filename": a.filename,
        "mime": a.mime,
        "size_bytes": a.size_bytes,
        "sha256": a.sha256,
        "url": f"/api/files/{a.id}",
        "source_kind": a.source_kind,
        "source_id": a.source_id,
        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
    }


def abspath(rel_path: str) -> Path | None:
    path = (root_dir() / rel_path).resolve()
    if root_dir() not in path.parents and path != root_dir():
        return None
    return path if path.is_file() else None
