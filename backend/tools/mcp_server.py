"""
P4 产品资源库 MCP Server —— 把参数 / 文案 / 图片 / 短视频分镜供出去给别的项目读。

协议：MCP（Model Context Protocol），基于官方 `mcp` SDK（v2 `MCPServer`）。
传输：
  - stdio          ：python -m tools.mcp_server            （配到客户端 mcp.json）
  - Streamable HTTP：挂在主站 /mcp，或 python -m tools.mcp_http（独立 8001 端口）

暴露内容：
  Tools     按需读取（list/get/search，以及只取文案 / 参数 / 图片 / 分镜 / 品牌 kit）
  Resources p4assets:// URI，支持 resources/read 语义（含 URI 模板参数）
  Prompts   直接产出「宣传视频脚本」创作提示词，下游视频项目拿来即用

数据源是 p4-assets/ 纯文件目录，不连数据库；别的项目也可以直接拷目录用。
"""
from __future__ import annotations

from typing import Any

try:  # mcp v2（官方新 SDK）
    from mcp.server.mcpserver import MCPServer  # type: ignore
except ModuleNotFoundError:  # mcp 1.x（当前 requirements 解析到的版本）兼容层
    from mcp.server.fastmcp import FastMCP

    class MCPServer(FastMCP):  # type: ignore[misc]
        """把 v2 MCPServer 的构造/ASGI 签名适配到 1.x FastMCP，业务装饰器完全一致。"""

        def __init__(self, name=None, title=None, version=None, description=None,
                     instructions=None, **kwargs):
            super().__init__(
                name=name,
                instructions=instructions,
                streamable_http_path=kwargs.pop("streamable_http_path", "/mcp"),
                json_response=kwargs.pop("json_response", False),
                stateless_http=kwargs.pop("stateless_http", False),
            )

        def streamable_http_app(self, streamable_http_path=None,
                                json_response=None, stateless_http=None):
            if streamable_http_path is not None:
                self.settings.streamable_http_path = streamable_http_path
            if json_response is not None:
                self.settings.json_response = json_response
            if stateless_http is not None:
                self.settings.stateless_http = stateless_http
            return super().streamable_http_app()

from app.services import asset_library as lib

server = MCPServer(
    name="p4-assets",
    title="P4 产品资源库",
    version="1.0.0",
    description="P4 外设产品的参数、营销文案、图片与 30 秒短视频分镜素材，供宣传视频等项目读取",
    instructions=(
        "这是一个只读的产品资产库。做宣传视频时建议的顺序："
        "① get_manifest 了解版本与品类；② list_products / search_products 选型号；"
        "③ get_video_storyboard 拿 30 秒分镜（含画面、字幕、口播、素材引用）；"
        "④ get_images 拿图片 URL/路径，get_specs 核对参数；"
        "⑤ get_marketing_copy 补充文案。所有数字参数均来自商品库真实规格，请勿自行追加未标注性能。"
    ),
)


def _ok(data: Any, **extra) -> dict:
    return {"ok": True, **extra, "data": data}


def _err(e: Exception) -> dict:
    """工具层不抛异常：下游项目总能拿到结构化结果，便于 Agent 自愈。"""
    return {"ok": False, "error": type(e).__name__, "detail": str(e)}


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
@server.tool()
def get_manifest() -> dict:
    """获取资源库索引：版本、生成时间、26 个 SKU 摘要、品类统计、MCP 入口与图片 base URL。"""
    try:
        return _ok(lib.load_manifest())
    except Exception as e:
        return _err(e)


@server.tool()
def list_products(category: str = "", featured: bool | None = None, limit: int = 50) -> dict:
    """列出产品摘要。category 可选 keyboard/mouse/headset/webcam/dock/gamepad；
    featured=true 只看主推款。返回 SKU、名称、品类、价格、标语与资产文件路径。"""
    try:
        items = lib.list_products(category=category, featured=featured, limit=limit)
        return _ok(items, count=len(items))
    except Exception as e:
        return _err(e)


@server.tool()
def search_products(query: str, category: str = "", top_k: int = 8) -> dict:
    """按自然语言/型号/卖点检索产品（SKU、名称、品类、标语、卖点、关键词都会匹配）。
    例如「轻量无线鼠标」「磁轴键盘」「带 ANC 的耳机」「KB-PRO87」。"""
    try:
        items = lib.search_products(query, category=category, top_k=top_k)
        return _ok(items, count=len(items), query=query)
    except Exception as e:
        return _err(e)


@server.tool()
def get_product(sku: str) -> dict:
    """获取某个 SKU 的**完整资产包**：identity / specs / specs_flat / copy / images / downloads / provenance。"""
    try:
        return _ok(lib.load_product(sku), sku=sku.upper())
    except Exception as e:
        return _err(e)


@server.tool()
def get_product_card(sku: str) -> dict:
    """商品卡（组合包）：identity + specs_flat + 营销文案 + 图片 + 下载，一次取齐。

    给 P6（VidFlow）这类下游做商品文案/视频脚本时用，避免连着调 5 个工具。
    """
    try:
        p = lib.load_product(sku)
        ident = p.get("identity") or {}
        return _ok({
            "sku": ident.get("sku", sku.upper()),
            "name": ident.get("name", ""),
            "brand": ident.get("brand", ""),
            "category": ident.get("category", ""),
            "price": ident.get("price"),
            "specs_flat": p.get("specs_flat") or {},
            "copy": p.get("copy") or {},
            "images": p.get("images") or [],
            "downloads": p.get("downloads") or [],
            "provenance": p.get("provenance") or {},
        }, sku=sku.upper())
    except Exception as e:
        return _err(e)


@server.tool()
def get_marketing_copy(sku: str) -> dict:
    """只取营销文案：主标语、短/长文案、卖点（真实参数 + 利益点）、使用场景、关键词、完整视频分镜。"""
    try:
        return _ok(lib.get_section(sku, "copy"), sku=sku.upper())
    except Exception as e:
        return _err(e)


@server.tool()
def get_specs(sku: str) -> dict:
    """只取参数：分组原始规格 + 压平后的 {属性: 值}（做字幕/参数卡直接取 specs_flat）。"""
    try:
        return _ok(lib.get_section(sku, "specs"), sku=sku.upper())
    except Exception as e:
        return _err(e)


@server.tool()
def get_images(sku: str) -> dict:
    """只取图片资产：产品卡图、16:9 品类主视觉、品牌 hero。
    每项含相对 path、HTTP url、mime_type、尺寸、用途，并校验文件是否真实存在。"""
    try:
        return _ok(lib.read_image_meta(sku), sku=sku.upper())
    except Exception as e:
        return _err(e)


@server.tool()
def get_video_storyboard(sku: str) -> dict:
    """取 30 秒 / 16:9 宣传片分镜：5 个镜头（timecode / visual / overlay_text / voiceover / asset_ref）
    + 风格、BGM 建议、CTA、合规说明，并附上对应图片资产。做视频优先调这个工具。"""
    try:
        return _ok(lib.get_section(sku, "storyboard"), sku=sku.upper())
    except Exception as e:
        return _err(e)


@server.tool()
def get_downloads(sku: str) -> dict:
    """取该型号的驱动 / 说明书 / 资源引用（标题、下载地址、SHA256）。"""
    try:
        return _ok(lib.get_section(sku, "downloads"), sku=sku.upper())
    except Exception as e:
        return _err(e)


@server.tool()
def get_brand_kit() -> dict:
    """取品牌级素材：名称、标语、文案基调、色板、6 个品类主视觉与品牌 hero 的 URL。"""
    try:
        return _ok(lib.brand_kit())
    except Exception as e:
        return _err(e)


@server.tool()
def health() -> dict:
    """自检：资源库是否就绪、SKU 数、图片文件是否存在。"""
    try:
        m = lib.load_manifest()
        imgs = lib.read_image_meta(m["products"][0]["sku"])["images"] if m["products"] else {}
        return {
            "ok": True,
            "available": lib.available(),
            "root": str(lib.ASSETS),
            "version": m.get("version"),
            "product_count": m.get("product_count"),
            "image_files_ok": all(v["exists"] for v in imgs.values()) if imgs else False,
        }
    except Exception as e:
        return _err(e)


# ---------------------------------------------------------------------------
# Resources（p4assets:// URI）
# ---------------------------------------------------------------------------
@server.resource("p4assets://manifest", mime_type="application/json",
                 description="资源库索引：版本、品类、全部 SKU 摘要")
def res_manifest() -> str:
    return lib.as_text(lib.load_manifest())


@server.resource("p4assets://schema", mime_type="application/json",
                 description="资产包 JSON Schema（draft 2020-12）")
def res_schema() -> str:
    return (lib.ASSETS / "schema.json").read_text(encoding="utf-8")


@server.resource("p4assets://brand", mime_type="application/json",
                 description="品牌 kit：标语、色板、品类主视觉与 hero")
def res_brand() -> str:
    return lib.as_text(lib.brand_kit())


@server.resource("p4assets://categories", mime_type="application/json",
                 description="6 个品类及各自 SKU 数量与主视觉")
def res_categories() -> str:
    return lib.as_text(lib.load_manifest().get("categories", []))


@server.resource("p4assets://products/{sku}", mime_type="application/json",
                 description="某个 SKU 的完整资产包")
def res_product(sku: str) -> str:
    return lib.as_text(lib.load_product(sku))


@server.resource("p4assets://products/{sku}/{section}", mime_type="application/json",
                 description="按段读取：specs / copy / images / storyboard / downloads / all")
def res_section(sku: str, section: str) -> str:
    return lib.as_text(lib.get_section(sku, section))


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
@server.prompt()
def product_promo_video(sku: str, style: str = "") -> str:
    """生成一条「用 P4 资产库写 30 秒宣传片脚本」的提示词，可直接喂给视频/文案 Agent。"""
    try:
        b = lib.load_product(sku)
    except Exception as e:
        return f"无法读取 SKU {sku}：{e}。请先调用 list_products 获取可用型号。"
    c, ident = b["copy"], b["identity"]
    points = "\n".join(f"  - {p['headline']}：{p['benefit']}" for p in c["selling_points"])
    shots = "\n".join(
        f"  {s['no']}. [{s['timecode']}] 画面：{s['visual']}｜字幕：{s['overlay_text']}｜口播：{s['voiceover']}"
        for s in c["video_script"]["shots"]
    )
    extra = f"\n补充风格要求：{style}" if style else ""
    return (
        f"你要为 {ident['name']}（{ident['sku']}，{ident['brand']}，"
        f"{ident.get('category_cn','')}，¥{ident['price']:.0f}）写一支 30 秒 16:9 产品宣传片脚本。{extra}\n\n"
        f"品牌标语：{c['tagline']}\n文案基调：{c['video_script']['brand_tone']}\n"
        f"视觉风格：{c['video_script']['style']}\nBGM：{c['video_script']['bgm_hint']}\n\n"
        f"可用卖点（均为商品库真实规格，不得自行追加未标注性能）：\n{points}\n\n"
        f"参考分镜（可在此基础上润色，但不要改动参数数字）：\n{shots}\n\n"
        f"素材引用：产品卡图 {b['images']['product_card']['path']}、"
        f"品类主视觉 {b['images']['category_visual']['path']}、"
        f"品牌 hero {b['images']['brand_hero']['path']}。\n"
        f"输出要求：按镜头给出「画面 / 字幕 / 口播 / 素材文件 / 时长」，"
        f"结尾加 CTA：{c['video_script']['cta']}。\n"
        f"合规：{c['video_script']['compliance_note']}"
    )


@server.prompt()
def product_campaign_brief(sku: str) -> str:
    """生成一份投放/内容选题 brief（短文案、卖点、场景、关键词）。"""
    try:
        b = lib.load_product(sku)
    except Exception as e:
        return f"无法读取 SKU {sku}：{e}。"
    c = b["copy"]
    return (
        f"为 {b['identity']['name']}（{b['identity']['sku']}）出一份内容投放 brief。\n"
        f"主标语：{c['tagline']}\n短文案：{c['short_copy']}\n"
        f"长文案：{c['long_copy']}\n"
        f"卖点：{'；'.join(p['copy'] for p in c['selling_points'])}\n"
        f"场景：{'；'.join(s['scene'] for s in c['scenes'])}\n"
        f"关键词：{'、'.join(c['keywords'])}\n"
        "输出：3 个可选标题、1 段社媒正文、3 条短视频选题，并标注每条对应的卖点。"
    )


def create_http_app(path: str = "/mcp", stateless: bool = True):
    """Streamable HTTP ASGI 应用；path='/' 时用于 mount 到主站的 /mcp。"""
    return server.streamable_http_app(
        streamable_http_path=path,
        json_response=True,
        stateless_http=stateless,
    )


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
