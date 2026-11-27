"""
读类工具：答错只是体验问题，所以直通（但仍受身份闸约束的订单类读操作）。

商品类（公开）：query_spec / query_stock / query_price / query_shipping / search_driver（驱动检索）
订单类（需绑定，私域）：query_order / query_logistics（缓存 10 分钟）
"""
from __future__ import annotations

import json

from sqlalchemy import select

from app.agents.tools.base import ToolContext, register_tool
from app.core.llm_registry import get_registry
from app.core.policy_engine import PolicyEngine, region_from_address
from app.db.models import Driver, Manual, Order, Product, Resource
from app.services import agent_cache, driver_service, knowledge_service, rag_service
from app.services.resource_service import RESOURCE_KIND_LABELS


def _find_product(db, sku: str) -> Product | None:
    if not sku:
        return None
    return (db.execute(select(Product).where(Product.sku == sku)).scalar_one_or_none())


def _flat_specs(specs: dict) -> dict:
    """把分组规格压平为 {属性名: 值}（属性名跨组唯一），便于按关键词检索。"""
    out: dict = {}

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, dict):
                    walk(v)
                elif isinstance(v, list):
                    # 标量列表（如兼容系统）直接保留；字典列表不常见，跳过
                    if v and not isinstance(v[0], dict):
                        out[k] = v
                else:
                    out[k] = v
        elif isinstance(node, list):
            for v in node:
                if isinstance(v, dict):
                    walk(v)

    walk(specs)
    return out


def _fmt(v) -> str:
    if isinstance(v, list):
        return "、".join(map(str, v))
    if isinstance(v, bool):
        return "支持" if v else "不支持"
    return str(v)


async def _query_spec(db, ctx: ToolContext, args: dict, order=None) -> dict:
    sku = args.get("product_sku")
    prod = (await db.execute(select(Product).where(Product.sku == sku))).scalar_one_or_none() if sku else None
    if not prod:
        return {"observation": "请告诉我具体型号（如 KB-PRO87），我再为您查规格。"}
    specs = json.loads(prod.specs)
    # 分组规格：按「组名 -> {属性: 值}」组织，返回专业参数表
    lines = [f"【{prod.name}】{prod.brand} · 公示价 ¥{prod.price}"]
    if isinstance(specs, dict):
        for gname, gitems in specs.items():
            if isinstance(gitems, dict):
                lines.append(f"▸ {gname}")
                for k, v in gitems.items():
                    lines.append(f"  · {k}：{_fmt(v)}")
            elif isinstance(gitems, (list, tuple)):
                # 兼容系统这类值是列表，早先被整组跳过，导致「支持 macOS 吗」答不出结论
                lines.append(f"▸ {gname}")
                lines.append("  · " + " / ".join(_fmt(x) for x in gitems))
    return {
        "observation": "\n".join(lines),
        "data": {"sku": prod.sku, "specs": specs, "flat": _flat_specs(specs)},
    }


async def _query_stock(db, ctx: ToolContext, args: dict, order=None) -> dict:
    sku = args.get("product_sku")
    prod = (await db.execute(select(Product).where(Product.sku == sku))).scalar_one_or_none() if sku else None
    if not prod:
        return {"observation": "请告诉我具体型号，我帮您查库存。"}
    state = "有货" if prod.stock > 0 else "暂时缺货"
    return {"observation": f"{prod.name} 当前库存 {prod.stock} 件，{state}。"}


async def _query_price(db, ctx: ToolContext, args: dict, order=None) -> dict:
    sku = args.get("product_sku")
    prod = (await db.execute(select(Product).where(Product.sku == sku))).scalar_one_or_none() if sku else None
    if not prod:
        return {"observation": "请告诉我具体型号，我帮您查价格与优惠。"}
    # 模型不得自行承诺优惠：只给公示价 + 合规说明
    return {"observation": f"{prod.name} 页面公示价 ¥{prod.price}。具体优惠以页面优惠券为准，我无法自行承诺折扣。",
            "data": {"price": prod.price}}


async def _query_shipping(db, ctx: ToolContext, args: dict, order=None) -> dict:
    region = args.get("region", "其他")
    eng = PolicyEngine(db)
    dec = await eng.shipping_sla(region)
    if not dec.allowed:
        return {"observation": "暂无该地区的时效信息。"}
    d = dec.data
    return {"observation": f"发往「{region}」由{d['carrier']}承运，预计 {d['promise_days']} 天送达（截单时间 {d['cutoff_hour']}:00 前当日发）。具体以物流更新为准。",
            "data": d}


async def _query_order(db, ctx: ToolContext, args: dict, order: Order | None = None) -> dict:
    if not order:
        return {"need_bind": True, "observation": "未查询到您绑定的订单。请先告知收货手机后四位完成核验，或登录后自动绑定。"}
    return {"observation": f"订单 {order.order_no[-4:]} 状态：{order.status.value}，金额 ¥{order.total}。", "data": order.to_public_dict()}


async def _query_logistics(db, ctx: ToolContext, args: dict, order: Order | None = None) -> dict:
    if not order:
        return {"need_bind": True, "observation": "未查询到您绑定的订单，无法查物流。"}
    if not order.tracking_no:
        return {"observation": f"订单 {order.order_no[-4:]} 暂无物流单号（状态：{order.status.value}）。"}
    cache_key = f"logi:{order.id}"
    cached = await agent_cache.get_json_value(cache_key)
    if cached:
        return {"observation": f"物流（缓存）：{cached['text']}", "data": cached}
    # 模拟外部物流接口
    text = f"{order.carrier} {order.tracking_no[-4:]}：已揽收，预计 {order.city} 1-2 天送达。"
    await agent_cache.set_json(cache_key, {"text": text, "carrier": order.carrier}, ex=600)  # 缓存 10 分钟
    return {"observation": f"物流：{text}", "data": {"text": text}}


async def _search_products(db, ctx: ToolContext, args: dict, order=None) -> dict:
    """RAG 工具：自然语言诉求 → 商品知识库召回 → SiliconFlow Reranker 精排 → Top-K。

    这是「检索增强」而非「检索依赖」：key 缺失/超时/429 时 rag_service 会自动降级，
    客服仍然拿到召回顺序的商品资料，不会出现空气泡。
    """
    query = (args.get("query") or args.get("product_sku") or "").strip()
    if not query:
        return {"observation": "请描述一下您的需求，例如「适合大手的无线轻量鼠标」或「带 ANC 的耳机」。"}
    try:
        top_k = int(args.get("top_k") or 0) or None
    except (TypeError, ValueError):
        top_k = None
    # Mock 模式定位是「离线确定性」，不发起任何外部 HTTP：
    # 关掉 rerank，召回也退回纯关键词；其余模式走 bge-m3 + bge-reranker-v2-m3
    offline = get_registry().active == "mock"
    use_rerank = not offline
    out = await rag_service.search(
        db, query, top_k=top_k, use_rerank=use_rerank,
        recall_mode="keyword" if offline else "hybrid",
    )
    data = {
        "query": out.get("query"),
        "rerank": out.get("rerank"),
        "degraded": out.get("degraded"),
        "code": out.get("code"),
        "items": [
            {"sku": r["sku"], "name": r["name"], "score": r["score"], "rank": r["rank"]}
            for r in out.get("results", [])
        ],
    }
    return {"observation": out.get("observation") or "暂未检索到匹配商品。", "data": data}


async def _search_driver(db, ctx: ToolContext, args: dict, order=None) -> dict:
    """按商品型号(SKU)/名称检索外设的官方驱动、说明书与资源（快速入门/保修卡等）。

    演示数据均为占位文件，但返回真实可下载的元数据（file_url / sha256），
    用户可直接下载并在本地校验完整性。SKU 必须已在 NLU 层校验过真实性
    （只能来自原话或记忆），因此按 SKU 精确检索。kind 由规则护栏推导：
    driver / manual / resource / all。
    """
    sku = (args.get("product_sku") or "").strip().upper()
    os_q = (args.get("os") or "").strip()
    query = (args.get("query") or "").strip()
    kind = (args.get("kind") or "driver").strip().lower()

    os_norm = ""
    if os_q:
        os_norm = {"win": "Windows", "windows": "Windows", "mac": "macOS",
                   "macos": "macOS", "os x": "macOS", "linux": "Linux"}.get(os_q.lower(), os_q)

    items: list[dict] = []
    if kind in ("driver", "all"):
        items += await _collect_drivers(db, sku, os_norm, query)
    if kind in ("manual", "all"):
        items += await _collect_manuals(db, sku, query)
    if kind in ("resource", "all"):
        items += await _collect_resources(db, sku, query)

    if not items:
        hint = "请告诉我具体型号（如 KB-PRO87）或系统（Windows/macOS/Linux），我再帮您定位对应的驱动、说明书或资源。"
        return {"observation": f"暂未找到匹配的资料。{hint}"}

    labels = {"driver": "驱动", "manual": "说明书", "resource": "资源"}
    names = {i["product_name"] for i in items}
    prod_label = next(iter(names)) if len(names) == 1 else f"{len(names)} 款商品"
    kind_label = "资料" if kind == "all" else labels.get(kind, "资料")
    lines = [f"为您找到 {prod_label} 的 {len(items)} 个官方{kind_label}（可直接下载，并用 SHA256 校验完整性）："]
    for i in items:
        meta = []
        if i.get("os"):
            meta.append(f"{i['os']} {i.get('arch', '')}")
        if i.get("version"):
            meta.append(f"v{i['version']}")
        if i.get("pages"):
            meta.append(f"{i['pages']}页")
        if i.get("resource_kind"):
            meta.append(RESOURCE_KIND_LABELS.get(i["resource_kind"], i["resource_kind"]))
        meta_s = "｜".join(m for m in meta if m)
        lines.append(f"  · [{labels.get(i['kind'])}] {i['title']} {meta_s}｜{i['size_mb']}MB")
        lines.append(f"    下载：{i['file_url']}")
        lines.append(f"    SHA256：{i['sha256']}")
    return {
        "observation": "\n".join(lines),
        "data": {"count": len(items), "items": items},
    }


async def _collect_drivers(db, sku, os_norm, query) -> list[dict]:
    stmt = select(Driver, Product.sku, Product.name, Product.image, Product.brand, Product.category)
    stmt = stmt.join(Product, Driver.product_id == Product.id)
    if sku:
        stmt = stmt.where(Product.sku == sku)
    elif query:
        like = f"%{query}%"
        stmt = stmt.where((Product.sku.like(like)) | (Product.name.like(like)) | (Driver.name.like(like)))
    if os_norm:
        stmt = stmt.where(Driver.os == os_norm)
    stmt = stmt.order_by(Driver.product_id, Driver.os)
    rows = (await db.execute(stmt)).all()
    return [
        {"id": d.id, "title": d.name, "os": d.os, "arch": d.arch, "version": d.version,
         "size_mb": d.size_mb, "release_date": d.release_date, "file_url": d.file_url, "sha256": d.sha256,
         "product_sku": sku_, "product_name": pname, "product_image": pimg, "brand": brand, "kind": "driver"}
        for d, sku_, pname, pimg, brand, _cat in rows
    ]


async def _collect_manuals(db, sku, query) -> list[dict]:
    stmt = select(Manual, Product.sku, Product.name, Product.image, Product.brand)
    stmt = stmt.join(Product, Manual.product_id == Product.id)
    if sku:
        stmt = stmt.where(Product.sku == sku)
    elif query:
        like = f"%{query}%"
        stmt = stmt.where((Product.sku.like(like)) | (Product.name.like(like)) | (Manual.title.like(like)))
    rows = (await db.execute(stmt)).all()
    return [
        {"id": m.id, "title": m.title, "version": m.version, "pages": m.pages,
         "size_mb": m.size_mb, "release_date": m.release_date, "file_url": m.file_url, "sha256": m.sha256,
         "product_sku": sku_, "product_name": pname, "product_image": pimg, "brand": brand, "kind": "manual"}
        for m, sku_, pname, pimg, brand in rows
    ]


async def _collect_resources(db, sku, query) -> list[dict]:
    stmt = select(Resource, Product.sku, Product.name, Product.image, Product.brand)
    stmt = stmt.join(Product, Resource.product_id == Product.id)
    if sku:
        stmt = stmt.where(Product.sku == sku)
    elif query:
        like = f"%{query}%"
        stmt = stmt.where((Product.sku.like(like)) | (Product.name.like(like)) | (Resource.title.like(like)))
    rows = (await db.execute(stmt)).all()
    return [
        {"id": r.id, "title": r.title, "resource_kind": r.kind,
         "size_mb": r.size_mb, "release_date": r.release_date, "file_url": r.file_url, "sha256": r.sha256,
         "product_sku": sku_, "product_name": pname, "product_image": pimg, "brand": brand, "kind": "resource"}
        for r, sku_, pname, pimg, brand in rows
    ]


async def _search_manual(db, ctx: ToolContext, args: dict, order=None) -> dict:
    """说明书知识库问答：检索章节切片，返回带「型号 + 文档 + 章节」引用的原文片段。

    与 search_driver 的分工：那个负责「把文件给用户」，这个负责「把内容讲给用户」。
    答案来自入库的说明书正文，不是模型凭记忆编的；reranker 不可用时自动降级。
    """
    query = (args.get("query") or "").strip()
    sku = (args.get("product_sku") or "").strip().upper()
    if not query:
        query = f"{sku} 使用说明与常见问题" if sku else ""
    if not query:
        return {"observation": "请描述您想查的使用问题，例如「键盘怎么配对蓝牙」或「鼠标没反应怎么排查」。"}
    try:
        top_k = int(args.get("top_k") or 0) or None
    except (TypeError, ValueError):
        top_k = None
    # Mock 模式定位「离线确定性」，不发起外部 HTTP；其余模式走真实双模型链路
    offline = get_registry().active == "mock"
    use_rerank = not offline
    out = await knowledge_service.search(
        db, query, sku=sku, top_k=top_k, use_rerank=use_rerank,
        recall_mode="keyword" if offline else "hybrid",
    )
    data = {
        "query": out.get("query"),
        "sku": out.get("sku"),
        "doc_count": out.get("doc_count"),
        "degraded": out.get("degraded"),
        "code": out.get("code"),
        "items": [
            {"sku": r["sku"], "doc_title": r["doc_title"],
             "section_no": r["section_no"], "section_title": r["section_title"],
             "score": r["score"], "file_url": r["file_url"]}
            for r in out.get("results", [])
        ],
    }
    return {"observation": out.get("observation") or "暂未检索到相关说明。", "data": data}


register_tool(
    name="search_manual",
    description="检索说明书知识库，回答「怎么连接/怎么设置/怎么重置/故障排查/快捷键/保修政策」等使用类问题；"
                "返回带型号、文档名、章节号引用的说明书原文片段与原文下载链接；"
                "用户问设备怎么用或出问题时优先用本工具，问「下载说明书」时不要用本工具",
    tool_type="read",
    params={"properties": {
        "query": {"type": "string", "description": "用户的使用问题原话，如 键盘怎么配对蓝牙 / 鼠标没反应怎么排查"},
        "product_sku": {"type": "string", "description": "可选：已知型号时限定在该型号说明书内检索，如 KB-PRO87"},
        "top_k": {"type": "integer", "description": "可选：返回章节数，默认 4"},
    }},
    handler=_search_manual, agents=["presale", "aftersale"],
)


register_tool(
    name="search_driver",
    description="按商品型号(SKU)/名称检索外设的官方驱动、说明书、资源（快速入门/保修卡等），"
                "返回可下载列表（含版本/大小/发布日期/下载地址与 SHA256 校验值）；"
                "用户询问「驱动/驱动程序/固件/说明书/手册/快速入门/保修卡/资源」时优先用本工具",
    tool_type="read",
    params={"properties": {
        "product_sku": {"type": "string", "description": "商品型号 SKU，如 KB-PRO87"},
        "os": {"type": "string", "description": "可选：操作系统筛选，Windows / macOS / Linux（仅驱动类有效）"},
        "query": {"type": "string", "description": "可选：型号片段或自然语言，如 无线鼠标 的资料"},
        "kind": {"type": "string", "description": "可选：driver / manual / resource / all，默认 driver"},
    }},
    handler=_search_driver, agents=["presale"],
)


register_tool(
    name="search_products",
    description="按自然语言需求检索/推荐商品（手型、有线无线、材质、轴体、ANC 等）；"
                "走商品知识库 RAG 召回 + Reranker 精排；不知道具体型号时优先用本工具",
    tool_type="read",
    params={"properties": {
        "query": {"type": "string", "description": "用户的自然语言诉求，如 适合大手的无线轻量鼠标"},
        "product_sku": {"type": "string", "description": "可选：已知型号时作为检索词"},
        "top_k": {"type": "integer", "description": "可选：返回商品数，默认 3"},
    }},
    handler=_search_products, agents=["presale"],
)
register_tool(
    name="query_spec", description="查询商品规格/兼容系统/接口（售前/售中）", tool_type="read",
    params={"properties": {"product_sku": {"type": "string", "description": "商品型号 SKU，如 KB-PRO87"}}},
    handler=_query_spec, agents=["presale", "insale"],
)
register_tool(
    name="query_stock", description="查询商品库存（售前/售中都可用来确认现货）", tool_type="read",
    params={"properties": {"product_sku": {"type": "string", "description": "商品型号 SKU，如 KB-PRO87"}}},
    handler=_query_stock, agents=["presale", "insale"],
)
register_tool(
    name="query_price", description="查询商品价格与优惠（不得承诺折扣）", tool_type="read",
    params={"properties": {"product_sku": {"type": "string", "description": "商品型号 SKU，如 KB-PRO87"}}},
    handler=_query_price, agents=["presale", "insale"],
)
register_tool(
    name="query_shipping", description="查询地区物流时效", tool_type="read",
    params={"properties": {"region": {"type": "string", "description": "收货地区，如 广东"}}},
    handler=_query_shipping, agents=["presale"],
)
register_tool(
    name="query_order", description="查询绑定订单的状态/明细（需核验身份）", tool_type="read",
    params={"properties": {}}, requires_order=True,
    handler=_query_order, agents=["insale", "aftersale"],
)
register_tool(
    name="query_logistics", description="查询绑定订单的物流轨迹（缓存10分钟）", tool_type="read",
    params={"properties": {}}, requires_order=True,
    handler=_query_logistics, agents=["insale"],
)
