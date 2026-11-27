"""
构建「P4 产品资源库」：从商品库生成一份自包含、可分发的资产目录。

产出（项目根 p4-assets/）：
  manifest.json              资源库索引 / 版本 / 统计 / MCP 入口
  schema.json                资产包 JSON Schema（别的项目照此解析）
  README.md                  字段说明与接入指南
  products/{SKU}.json        26 份完整资产包：参数 + 文案 + 图片 + 分镜 + 下载引用
  images/products/{SKU}.svg  产品卡图（从 backend/static 复制）
  images/brand/hero.png      品牌主视觉
  images/visuals/{cat}-16x9.svg  6 张品类 16:9 主视觉（视频封面/背景用）

设计要点：
- 资源库是**纯文件**，不依赖数据库/Python，别的项目可以直接拷贝、git 管理、CDN 托管。
- 生成过程确定性（品类模板 + 真实规格），可重复执行，diff 干净。
- 文案里的事实全部来自 DB，不引入未标注参数；分镜直接引用本库图片相对路径。

运行：python -m tools.build_assets
"""
from __future__ import annotations

import asyncio
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.db.models import Driver, Manual, Product, Resource
from app.db.session import AsyncSessionLocal
from app.services import asset_copy

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "p4-assets"
STATIC = Path(__file__).resolve().parents[1] / "static"

ASSET_BASE_URL = "http://127.0.0.1:8000/p4-assets"
VERSION = "1.0.0"

_CATEGORY_ORDER = ["keyboard", "mouse", "headset", "webcam", "dock", "gamepad"]


def _visual_svg(cat: str) -> str:
    """生成 1600×900 品类主视觉（浅色专业风），供视频封面 / 背景板使用。"""
    cn = asset_copy.CATEGORY_CN.get(cat, cat)
    en = {"keyboard": "KEYBOARD", "mouse": "MOUSE", "headset": "HEADSET",
          "webcam": "WEBCAM", "dock": "DOCK", "gamepad": "GAMEPAD"}[cat]
    tagline = asset_copy._CATEGORY[cat]["tagline"]
    # 右侧产品轮廓：按品类画一组几何剪影，保持极简、可无损放大
    shape = {
        "keyboard": """<rect x="980" y="330" width="500" height="220" rx="18" fill="#ffffff" stroke="#15161a" stroke-width="3"/>
      <g fill="#15161a" opacity=".16">""" + "".join(
            f'<rect x="{1000 + c * 33}" y="{355 + r * 42}" width="26" height="30" rx="4"/>'
            for r in range(5) for c in range(14)) + "</g>",
        "mouse": """<ellipse cx="1230" cy="450" rx="150" ry="215" fill="#ffffff" stroke="#15161a" stroke-width="3"/>
      <path d="M1230 240 V390" stroke="#15161a" stroke-width="3" opacity=".5"/>
      <rect x="1218" y="330" width="24" height="60" rx="12" fill="#d92626"/>
      <path d="M1080 450 H1380" stroke="#15161a" stroke-width="2" opacity=".15"/>""",
        "headset": """<path d="M1050 500 V400 a180 180 0 0 1 360 0 V500" fill="none" stroke="#15161a" stroke-width="26" stroke-linecap="round"/>
      <rect x="1020" y="450" width="72" height="150" rx="28" fill="#ffffff" stroke="#15161a" stroke-width="3"/>
      <rect x="1368" y="450" width="72" height="150" rx="28" fill="#ffffff" stroke="#15161a" stroke-width="3"/>
      <circle cx="1056" cy="525" r="12" fill="#d92626"/><circle cx="1404" cy="525" r="12" fill="#d92626"/>""",
        "webcam": """<rect x="1030" y="330" width="400" height="200" rx="26" fill="#ffffff" stroke="#15161a" stroke-width="3"/>
      <circle cx="1230" cy="430" r="78" fill="#f5f6f8" stroke="#15161a" stroke-width="3"/>
      <circle cx="1230" cy="430" r="44" fill="#15161a" opacity=".85"/>
      <circle cx="1252" cy="410" r="12" fill="#ffffff" opacity=".7"/>
      <rect x="1180" y="530" width="100" height="70" rx="8" fill="#ffffff" stroke="#15161a" stroke-width="3"/>""",
        "dock": """<rect x="980" y="390" width="500" height="120" rx="16" fill="#ffffff" stroke="#15161a" stroke-width="3"/>
      <g fill="#15161a" opacity=".78">""" + "".join(
            f'<rect x="{1010 + i * 58}" y="425" width="42" height="{26 if i % 2 else 34}" rx="5"/>'
            for i in range(8)) + """</g><circle cx="1440" cy="450" r="10" fill="#d92626"/>""",
        "gamepad": """<path d="M1030 380 q200 -60 400 0 q70 30 60 130 q-10 110 -110 90 q-90 -18 -150 -18 q-60 0 -150 18 q-100 20 -110 -90 q-10 -100 60 -130 Z"
        fill="#ffffff" stroke="#15161a" stroke-width="3"/>
      <circle cx="1130" cy="470" r="34" fill="#f5f6f8" stroke="#15161a" stroke-width="3"/>
      <circle cx="1330" cy="470" r="34" fill="#f5f6f8" stroke="#15161a" stroke-width="3"/>
      <circle cx="1200" cy="420" r="12" fill="#d92626"/><circle cx="1260" cy="420" r="12" fill="#15161a" opacity=".6"/>""",
    }[cat]

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="900" viewBox="0 0 1600 900">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#f8f9fb"/><stop offset="1" stop-color="#e9ecf1"/>
    </linearGradient>
    <pattern id="grid" width="48" height="48" patternUnits="userSpaceOnUse">
      <path d="M48 0H0V48" fill="none" stroke="#15161a" stroke-width="1" opacity=".045"/>
    </pattern>
  </defs>
  <rect width="1600" height="900" fill="url(#bg)"/>
  <rect width="1600" height="900" fill="url(#grid)"/>
  <rect x="90" y="120" width="8" height="120" fill="#d92626"/>
  <text x="128" y="205" font-family="Arial, 'Microsoft YaHei', sans-serif" font-size="34"
        letter-spacing="8" fill="#8a8f98">{en} / P4 ASSET LIBRARY</text>
  <text x="126" y="360" font-family="Arial, 'Microsoft YaHei', sans-serif" font-size="96"
        font-weight="700" fill="#15161a">{cn}</text>
  <text x="130" y="440" font-family="Arial, 'Microsoft YaHei', sans-serif" font-size="38"
        fill="#15161a" opacity=".78">{tagline}</text>
  <text x="130" y="510" font-family="Arial, 'Microsoft YaHei', sans-serif" font-size="22"
        fill="#8a8f98">1600 × 900 · 16:9 · 视频封面 / 分镜背景板</text>
  {shape}
  <line x1="90" y1="760" x2="1510" y2="760" stroke="#15161a" stroke-width="1" opacity=".15"/>
  <text x="90" y="820" font-family="Arial, sans-serif" font-size="40" font-weight="700" fill="#15161a">P4</text>
  <text x="170" y="818" font-family="Arial, 'Microsoft YaHei', sans-serif" font-size="22" fill="#8a8f98">为每一次操作负责</text>
  <text x="1510" y="818" text-anchor="end" font-family="Arial, sans-serif" font-size="20"
        fill="#8a8f98" letter-spacing="3">{cat.upper()} / VISUAL 01</text>
</svg>
"""


async def _downloads(db, product_id: str) -> list[dict]:
    out: list[dict] = []
    for model, kind in ((Driver, "driver"), (Manual, "manual"), (Resource, "resource")):
        rows = (await db.execute(select(model).where(model.product_id == product_id))).scalars().all()
        for m in rows:
            out.append({
                "kind": kind,
                "title": getattr(m, "title", None) or getattr(m, "name", ""),
                "file_url": m.file_url,
                "sha256": m.sha256,
            })
    return out


def _images(sku: str, cat: str) -> dict[str, dict]:
    def item(path: str, mime: str, purpose: str, w: int, h: int, note: str = "") -> dict:
        return {
            "path": path,
            "url": f"{ASSET_BASE_URL}/{path}",
            "mime_type": mime,
            "width": w, "height": h,
            "purpose": purpose,
            "note": note,
        }
    return {
        "product_card": item(f"images/products/{sku}.svg", "image/svg+xml", "产品卡图 / 视频内嵌素材", 600, 400,
                             "矢量，可无损放大；浅色卡片含品牌、品类图标与产品名"),
        "category_visual": item(f"images/visuals/{cat}-16x9.svg", "image/svg+xml", "16:9 视频封面 / 分镜背景板", 1600, 900),
        "brand_hero": item("images/brand/hero.png", "image/png", "品牌主视觉 / 片尾", 1536, 1024,
                           "暗调电竞产品渲染，适合片尾品牌帧"),
    }


def _schema() -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://p4.example/assets/schema.json",
        "title": "P4 产品资产包",
        "description": "单个 SKU 的完整宣传素材：参数、文案、图片、短视频分镜、下载引用",
        "type": "object",
        "required": ["schema_version", "sku", "identity", "specs", "copy", "images", "downloads"],
        "properties": {
            "schema_version": {"type": "string"},
            "sku": {"type": "string"},
            "identity": {"type": "object", "required": ["sku", "name", "brand", "category", "price"],
                         "properties": {"sku": {"type": "string"}, "name": {"type": "string"},
                                        "brand": {"type": "string"}, "category": {"type": "string"},
                                        "category_cn": {"type": "string"}, "price": {"type": "number"},
                                        "currency": {"type": "string"}, "featured": {"type": "boolean"}}},
            "specs": {"type": "object", "description": "分组原始规格（DB 原样）"},
            "specs_flat": {"type": "object", "description": "压平后的 {属性: 值}，便于字幕取值"},
            "copy": {"type": "object", "required": ["tagline", "short_copy", "long_copy", "video_script"],
                     "properties": {
                         "tagline": {"type": "string"}, "lead": {"type": "string"},
                         "short_copy": {"type": "string"}, "long_copy": {"type": "string"},
                         "selling_points": {"type": "array", "items": {"type": "object"}},
                         "scenes": {"type": "array", "items": {"type": "object"}},
                         "video_script": {"type": "object", "required": ["shots"],
                                          "properties": {"shots": {"type": "array"}}},
                         "keywords": {"type": "array", "items": {"type": "string"}},
                     }},
            "images": {"type": "object", "description": "每类图片含 path/url/mime_type/width/height/purpose",
                       "additionalProperties": {"type": "object"}},
            "downloads": {"type": "array", "items": {"type": "object"}},
            "provenance": {"type": "object"},
        },
    }


def _readme(manifest: dict) -> str:
    tool_list = " / ".join(f"`{t}`" for t in manifest["mcp"]["tools"])
    return f"""# P4 产品资源库 · P4 Asset Library

版本 `{VERSION}` · {manifest['product_count']} 个 SKU · 自包含静态资产目录。
**这个文件夹可以直接拷给别的项目 / 传 CDN / 进 git，不依赖 P4 商城的数据库或 Python 环境。**

## 里面有什么

| 目录 / 文件 | 内容 |
|---|---|
| `manifest.json` | 索引：版本、生成时间、品类、每个 SKU 的文件路径与摘要、MCP 入口 |
| `schema.json` | 资产包 JSON Schema（draft 2020-12），别的项目照此解析 |
| `products/{{SKU}}.json` | 单个产品的完整资产包 |
| `images/products/{{SKU}}.svg` | 产品卡图（矢量，600×400） |
| `images/visuals/{{category}}-16x9.svg` | 品类 16:9 主视觉（1600×900，视频封面 / 背景板） |
| `images/brand/hero.png` | 品牌主视觉（片尾品牌帧） |

## 资产包字段（`products/{{SKU}}.json`）

- `identity`：SKU、名称、品牌、品类、价格、是否主推
- `specs` / `specs_flat`：**参数**（DB 原始分组规格 + 压平版，字幕直接取 `specs_flat`）
- `copy`：**文案**
  - `tagline` 主标语、`short_copy` 电商短文案、`long_copy` 详情页长文案
  - `selling_points[]` 卖点（`fact` 是真实参数值，`benefit` 是利益点，`copy` 可直接上字幕）
  - `scenes[]` 使用场景、`keywords[]` 检索标签
  - `video_script` **短视频分镜**：30 秒 / 16:9 / 5 镜头，每镜头含 `timecode`、`visual`、`overlay_text`、`voiceover`、`asset_ref`
- `images`：每类图片的 `path`（相对本目录）、`url`（HTTP）、`mime_type`、尺寸、用途
- `downloads`：该型号的驱动 / 说明书 / 资源引用（含 SHA256）
- `provenance`：来源与生成信息

> 事实约束：文案与分镜字幕里的参数全部来自商品库真实 `specs`，模板不会编造未标注性能。
> 生成脚本 `backend/tools/build_assets.py` 确定性可重跑，改模板后可整库重建。

## 做宣传视频怎么用

1. 读 `manifest.json` 挑 SKU（可按 `category` / `featured` 过滤）
2. 读 `products/{{SKU}}.json` 的 `copy.video_script.shots[]`，按 `timecode` 排期
3. 每个镜头的 `asset_ref` 对应 `images` 里的键（`product_card` / `category_visual` / `brand_hero`），取 `path` 或 `url` 拿素材
4. `overlay_text` / `voiceover` 直接做字幕与配音稿；`bgm_hint` / `style` 给剪辑风格参考
5. 需要更多参数时查 `specs_flat`，需要合规说明看 `copy.video_script.compliance_note`

## 通过 MCP 读取（推荐）

P4 商城把这份资源库以 MCP 协议供出去，别的项目不用解析文件也能读：

- **Streamable HTTP**：`http://127.0.0.1:8000/mcp`（服务在线即可连）
- **独立 HTTP 端口**：`python -m tools.mcp_http`（默认 `http://127.0.0.1:8001/mcp`）
- **stdio**（配到客户端 `mcp.json`）：`python -m tools.mcp_server`

提供的工具：{tool_list}。
资源：`p4assets://manifest`、`p4assets://products/{{sku}}`、`p4assets://products/{{sku}}/copy` 等。
Prompt：`product_promo_video(sku)` 直接产出一段视频脚本创作提示词。

`ASSET_BASE_URL`（默认 `{ASSET_BASE_URL}`）可在 `build_assets.py` 里改成实际部署域名，
改完重跑即可批量刷新所有图片 URL。
"""


async def build() -> dict:
    ASSETS.mkdir(parents=True, exist_ok=True)
    for d in ("products", "images/products", "images/visuals", "images/brand"):
        (ASSETS / d).mkdir(parents=True, exist_ok=True)

    # --- 图片资产：复制现有素材 ---
    copied = 0
    src_products = STATIC / "products"
    if src_products.is_dir():
        for f in src_products.glob("*.svg"):
            shutil.copy2(f, ASSETS / "images/products" / f.name)
            copied += 1
    hero = STATIC / "hero.png"
    if hero.is_file():
        shutil.copy2(hero, ASSETS / "images/brand" / "hero.png")
        copied += 1
    for cat in _CATEGORY_ORDER:
        (ASSETS / "images/visuals" / f"{cat}-16x9.svg").write_text(_visual_svg(cat), encoding="utf-8")
        copied += 1

    # --- 产品资产包 ---
    async with AsyncSessionLocal() as db:
        products = (await db.execute(select(Product).order_by(Product.sku))).scalars().all()
        index = []
        for p in products:
            try:
                specs = json.loads(p.specs) if p.specs else {}
            except Exception:
                specs = {}
            base = {
                "sku": p.sku, "name": p.name, "brand": p.brand, "category": p.category,
                "price": p.price, "description": p.description, "featured": bool(p.featured),
            }
            flat = asset_copy._flat_specs(specs)
            copy = asset_copy.build_copy(base, specs)
            bundle = {
                "schema_version": VERSION,
                "sku": p.sku,
                "identity": {
                    **base,
                    "category_cn": asset_copy.CATEGORY_CN.get(p.category, p.category),
                    "currency": p.currency or "CNY",
                    "stock": p.stock,
                },
                "specs": specs,
                "specs_flat": flat,
                "copy": copy,
                "images": _images(p.sku, p.category),
                "downloads": await _downloads(db, p.id),
                "provenance": {
                    "source": "P4 外设商城 · products 表",
                    "generated_by": "backend/tools/build_assets.py",
                    "copy_generator": "backend/app/services/asset_copy.py（确定性模板 + 真实规格）",
                    "asset_base_url": ASSET_BASE_URL,
                },
            }
            (ASSETS / "products" / f"{p.sku}.json").write_text(
                json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
            index.append({
                "sku": p.sku, "name": p.name, "category": p.category,
                "category_cn": asset_copy.CATEGORY_CN.get(p.category, p.category),
                "brand": p.brand, "price": p.price, "featured": bool(p.featured),
                "tagline": copy["tagline"],
                "file": f"products/{p.sku}.json",
                "product_card": f"images/products/{p.sku}.svg",
                "category_visual": f"images/visuals/{p.category}-16x9.svg",
            })

    manifest = {
        "name": "P4 产品资源库",
        "name_en": "P4 Asset Library",
        "version": VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "description": "P4 外设产品的参数、营销文案、图片与短视频分镜素材；供宣传视频等项目读取",
        "brand": asset_copy.BRAND,
        "asset_base_url": ASSET_BASE_URL,
        "schema": "schema.json",
        "product_count": len(index),
        "categories": [
            {"key": c, "name": asset_copy.CATEGORY_CN[c],
             "visual": f"images/visuals/{c}-16x9.svg",
             "count": sum(1 for x in index if x["category"] == c)}
            for c in _CATEGORY_ORDER
        ],
        "mcp": {
            "protocol": "MCP (Streamable HTTP / stdio)",
            "http": "http://127.0.0.1:8000/mcp",
            "http_standalone": "http://127.0.0.1:8001/mcp",
            "stdio": "python -m tools.mcp_server",
            "tools": ["get_manifest", "list_products", "search_products", "get_product",
                      "get_marketing_copy", "get_specs", "get_images", "get_video_storyboard",
                      "get_downloads", "get_brand_kit", "health"],
        },
        "products": index,
    }
    (ASSETS / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (ASSETS / "schema.json").write_text(
        json.dumps(_schema(), ensure_ascii=False, indent=2), encoding="utf-8")
    (ASSETS / "README.md").write_text(_readme(manifest), encoding="utf-8")
    return {"products": len(index), "images": copied, "path": str(ASSETS)}


def main() -> None:
    out = asyncio.run(build())
    print(f"资源库构建完成：{out['products']} 个 SKU 资产包，{out['images']} 个图片/视觉文件。")
    print(f"目录：{out['path']}")
    print("  manifest.json / schema.json / README.md / products/*.json / images/**")


if __name__ == "__main__":
    main()
