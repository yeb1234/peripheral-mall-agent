"""
P4 产品资源库读取层：只读静态资产目录，不依赖数据库。

MCP Server、HTTP 接口、前端页面都复用这一层，保证「供出去的」和「站内看到的」
是同一份资产。目录结构见 p4-assets/README.md。

安全：SKU 走白名单正则，禁止路径穿越；所有读取都限定在 ASSETS 目录内。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
ASSETS = ROOT / "p4-assets"
PRODUCTS = ASSETS / "products"

_SKU_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,63}$")


def _safe_sku(sku: str) -> str:
    s = (sku or "").strip().upper()
    if not _SKU_RE.match(s):
        raise ValueError(f"非法 SKU：{sku!r}（只允许字母/数字/连字符）")
    return s


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"资源不存在：{path.relative_to(ASSETS) if path.is_relative_to(ASSETS) else path}")
    return json.loads(path.read_text(encoding="utf-8"))


def available() -> bool:
    return (ASSETS / "manifest.json").is_file() and PRODUCTS.is_dir()


def load_manifest() -> dict:
    m = _read_json(ASSETS / "manifest.json")
    m["_available"] = available()
    m["_root"] = str(ASSETS)
    return m


def load_product(sku: str) -> dict:
    return _read_json(PRODUCTS / f"{_safe_sku(sku)}.json")


def list_products(category: str = "", featured: bool | None = None, limit: int = 50) -> list[dict]:
    items = load_manifest().get("products", [])
    if category:
        items = [p for p in items if p["category"] == category]
    if featured is not None:
        items = [p for p in items if bool(p["featured"]) == bool(featured)]
    return items[: max(1, min(int(limit or 50), 200))]


def _score(query: str, bundle: dict) -> float:
    low = query.lower().strip()
    ident, copy = bundle["identity"], bundle["copy"]
    hay = " ".join([
        ident["sku"], ident["name"], ident["brand"], ident["category"],
        ident.get("category_cn", ""), copy.get("tagline", ""),
        copy.get("short_copy", ""), " ".join(copy.get("keywords", [])),
        " ".join(f"{s.get('label','')} {s.get('fact','')}" for s in copy.get("selling_points", [])),
    ]).lower()
    score = 0.0
    for tok in re.findall(r"[a-z0-9.%+\-/]+|[\u4e00-\u9fff]{2}", low):
        if tok in hay:
            score += 4 if len(tok) >= 3 else 2
    if ident["sku"].lower() in low:
        score += 20
    if ident.get("category_cn", "") and ident["category_cn"] in low:
        score += 3
    return score


def search_products(query: str, category: str = "", top_k: int = 8) -> list[dict]:
    """轻量关键词检索（本地、零外部依赖）；返回摘要 + 匹配分。"""
    q = (query or "").strip()
    k = max(1, min(int(top_k or 8), 26))
    out = []
    for f in sorted(PRODUCTS.glob("*.json")):
        b = _read_json(f)
        if category and b["identity"]["category"] != category:
            continue
        s = _score(q, b) if q else 1.0
        if s <= 0:
            continue
        i = b["identity"]
        out.append({
            "sku": i["sku"], "name": i["name"], "category": i["category"],
            "category_cn": i.get("category_cn", ""), "price": i["price"],
            "featured": bool(i.get("featured")), "tagline": b["copy"]["tagline"],
            "score": round(s, 2), "file": f"products/{i['sku']}.json",
        })
    out.sort(key=lambda x: (-x["score"], x["sku"]))
    return out[:k]


def get_section(sku: str, section: str) -> dict:
    """按段读取，方便别的项目只拿需要的部分，减少传输量。"""
    b = load_product(sku)
    mapping = {
        "specs": lambda: {"sku": b["sku"], "specs": b["specs"], "specs_flat": b["specs_flat"]},
        "copy": lambda: {"sku": b["sku"], **b["copy"]},
        "images": lambda: {"sku": b["sku"], "images": b["images"]},
        "storyboard": lambda: {"sku": b["sku"], "video_script": b["copy"]["video_script"],
                               "images": b["images"]},
        "downloads": lambda: {"sku": b["sku"], "downloads": b["downloads"]},
        "all": lambda: b,
    }
    if section not in mapping:
        raise ValueError(f"未知段：{section}（可选 {', '.join(mapping)}）")
    return mapping[section]()


def brand_kit() -> dict:
    m = load_manifest()
    return {
        "brand": m.get("brand", {}),
        "version": m.get("version"),
        "asset_base_url": m.get("asset_base_url"),
        "categories": m.get("categories", []),
        "hero": f"{m.get('asset_base_url', '')}/images/brand/hero.png",
        "visuals": [f"{m.get('asset_base_url', '')}/{c['visual']}" for c in m.get("categories", [])],
        "product_count": m.get("product_count"),
        "usage_note": "色板 / 标语 / 品类主视觉，用于片头片尾与统一视觉规范",
    }


def read_image_meta(sku: str) -> dict:
    """校验图片文件真实存在，返回带 exists 标记的清单（避免给下游死链）。"""
    b = load_product(sku)
    out = {}
    for key, img in b["images"].items():
        p = ASSETS / img["path"]
        out[key] = {**img, "local_path": str(p), "exists": p.is_file()}
    return {"sku": b["sku"], "images": out}


def resource(uri: str) -> tuple[str, str]:
    """MCP resources/read：解析 p4assets:// URI，返回 (mime_type, text)。

    支持：
      p4assets://manifest
      p4assets://schema
      p4assets://brand
      p4assets://categories
      p4assets://products/{sku}[/{section}]
    """
    u = (uri or "").strip()
    if not u.startswith("p4assets://"):
        raise ValueError("仅支持 p4assets:// 资源")
    path = u[len("p4assets://"):].strip("/")
    parts = path.split("/")
    if path == "manifest":
        return "application/json", json.dumps(load_manifest(), ensure_ascii=False, indent=2)
    if path == "schema":
        return "application/json", (ASSETS / "schema.json").read_text(encoding="utf-8")
    if path == "brand":
        return "application/json", json.dumps(brand_kit(), ensure_ascii=False, indent=2)
    if path == "categories":
        m = load_manifest()
        return "application/json", json.dumps(m.get("categories", []), ensure_ascii=False, indent=2)
    if parts[0] == "products" and len(parts) >= 2:
        section = parts[2] if len(parts) >= 3 else "all"
        return "application/json", json.dumps(get_section(parts[1], section), ensure_ascii=False, indent=2)
    raise FileNotFoundError(f"未知资源：{uri}")


def as_text(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)
