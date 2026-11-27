"""P3 跑分对比（远程 MCP 客户端）。

P4 本身是 MCP 服务端（对外暴露资产库）；这里反过来做客户端，
在售前对话里调 P3 的 `compare_products` / `get_product_facts`。

为什么不用现成 MCP 适配器：P4 的工具层是自建注册表（JSON Schema 校验 +
读/写分流 + agents 过滤），把 P3 的四个工具包成两个业务工具更贴合现有执行链，
也避免把 MCP 的原始 JSON 直接塞给模型。

失败策略与联网检索一致：P3 不可达 / 超时 / 返回 error，都只降级提示，不抛给用户。
"""
from __future__ import annotations

import re

from app.agents.tools.base import ToolContext, register_tool
from app.core import p3_client

# 跑分请求的确定性护栏：小模型实测会把「跑分对比」答成打招呼，
# 这层规则保证工具一定被调起来，模型只负责组织话术。
_BENCH_SIGNALS = (
    "跑分", "geekbench", "gb6", "3dmark", "timespy", "time spy", "steel nomad",
    "port royal", "fire strike", "单核", "多核", "安兔兔", "antutu", "天梯", "帧数",
    "强多少", "差多少", "性能对比", "图形分", "显卡分",
)

_MODEL_PATTERNS = (
    re.compile(r"(?:RTX|GTX|RX)\s?\d{3,4}\s?(?:XTX|XT|SUPER|Ti(?:\s?Super)?)?", re.I),
    re.compile(r"(?:Ryzen|Threadripper)\s?\d\s?\d{4}[A-Z0-9]*", re.I),
    re.compile(r"\b\d{4,5}[A-Z0-9]{1,4}\b"),                    # 9800X3D / 14600KF
    re.compile(r"\b[2357]\d{2}[A-Z]{1,2}\b"),                   # 285K / 14600K 之外的短型号
    re.compile(r"\b(?:Ultra\s?)?[579]\s?\d{3}[A-Z]{0,2}\b", re.I),  # 285K
    re.compile(r"\bM[1-4]\s?(?:Pro|Max|Ultra)?\b"),
    re.compile(r"\bSnapdragon\s?\d\s?Gen\s?\d\b", re.I),
    re.compile(r"\biPhone\s?\d{1,2}\s?(?:Pro\s?Max|Pro)?\b", re.I),
)


def _extract_models(text: str) -> list[str]:
    seen: list[str] = []
    for pat in _MODEL_PATTERNS:
        for m in pat.findall(text or ""):
            name = " ".join(m.split()).strip()
            if name and name.lower() not in {s.lower() for s in seen}:
                seen.append(name)
    # "RTX 5080" 与 "5080" 会同时命中不同正则，丢掉被更长型号包含的片段，
    # 否则 P3 单独拿到 "5080" 无法判断品类，整次对比直接失败。
    out: list[str] = []
    for name in seen:
        low = name.lower()
        if any(low != other.lower() and low in other.lower() for other in seen):
            continue
        out.append(name)
    return out


def _guess_kind(models: list[str], text: str = "") -> str | None:
    """按型号形态猜品类，省得用户说「9800X3D」时 P3 还要反问一次。"""
    low = (text or "").lower()
    if any(re.match(r"(RTX|GTX|RX)\s", m, re.I) for m in models):
        return "gpu"
    if any(re.search(r"(snapdragon|iphone|天玑|dimensity|exynos)", m, re.I) for m in models) \
            or any(w in low for w in ("手机", "soc", "骁龙", "天玑", "iphone")):
        return "soc"
    if all(re.search(r"(ryzen|threadripper|ultra\s?\d)", m, re.I)
           or re.fullmatch(r"[0-9]{3,5}[A-Z0-9]{1,4}", m) for m in models):
        return "cpu"
    return None


def rule_select_benchmark(text: str, tools: list[dict]) -> dict | None:
    """命中跑分信号且能抠出 ≥2 个型号时，直接指定 compare_benchmarks。"""
    low = (text or "").lower()
    if not any(sig in low for sig in _BENCH_SIGNALS):
        return None
    names = {t.get("name") for t in tools}
    if "compare_benchmarks" not in names:
        return None
    models = _extract_models(text)
    if len(models) < 2:
        return None
    args: dict = {"products": models}
    kind = _guess_kind(models, text)
    if kind:
        args["kind"] = kind
    # 场景 / 口径能从句子里直接读出来时，别让 P3 再猜一遍
    for key, label in _SCENE_LABELS.items():
        if label.lower() in low:
            args["scene"] = key
            break
    if re.search(r"(图形分|显卡分|graphics\s*score)", low):
        args["metric"] = "graphics"
    elif re.search(r"(处理器分|cpu\s*score)", low):
        args["metric"] = "cpu"
    return {"action": "tool", "tool": "compare_benchmarks", "args": args}

_FIELD_LABELS = {
    "bench.gb6.single": "GB6 单核",
    "bench.gb6.multi": "GB6 多核",
    "bench.antutu.v11": "安兔兔 v11",
    "bench.antutu.v10": "安兔兔 v10",
    "spec.cores": "核心",
    "spec.threads": "线程",
    "spec.tdp_w": "TDP",
    "spec.node_nm": "工艺",
    "spec.vram_gb": "显存",
}

_SCENE_LABELS = {
    "steel_nomad": "Steel Nomad", "steel_nomad_light": "Steel Nomad Light",
    "speed_way": "Speed Way", "time_spy": "Time Spy", "port_royal": "Port Royal",
    "wild_life": "Wild Life", "solar_bay": "Solar Bay", "fire_strike": "Fire Strike",
}
_METRIC_LABELS = {"overall": "综合分", "graphics": "图形分", "cpu": "处理器分"}

_UNITS = {
    "spec.tdp_w": "W",
    "spec.node_nm": "nm",
    "spec.vram_gb": "GB",
}


def _label(field: str, scene: str | None) -> str:
    if field in _FIELD_LABELS:
        return _FIELD_LABELS[field]
    if field.startswith("bench.3dmark."):
        parts = field.split(".")
        if len(parts) >= 4:          # bench.3dmark.<scene>.<metric>
            scene_key, metric = parts[2], parts[3]
            return f"3DMark {_SCENE_LABELS.get(scene_key, scene_key.replace('_', ' '))} " \
                   f"{_METRIC_LABELS.get(metric, metric)}"
        name = parts[-1].replace("_", " ")   # 旧数据：bench.3dmark.<scene>
        return f"3DMark {name} 综合分"
    return field


def _fmt_value(field: str, value) -> str:
    if value is None:
        return "—"
    unit = _UNITS.get(field)
    if field.startswith("bench."):
        unit = "分"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return f"{value}{unit}" if unit else str(value)


async def _mcp_call(tool: str, args: dict) -> dict:
    """兼容旧调用点；协议细节已收口到 core.p3_client。"""
    return await p3_client.call(tool, args)


def _render_compare(data: dict) -> str:
    if data.get("error"):
        return f"跑分库返回错误：{data['error']}"
    scene = data.get("scene")
    lines: list[str] = []
    # 跨平台限制必须出现在最前面，模型转述时不会漏
    for warning in data.get("warnings") or []:
        lines.append(f"⚠ {warning}")
    metric = data.get("metric_label") or _METRIC_LABELS.get(data.get("metric") or "", "")
    if scene and metric:
        lines.append(f"3DMark 口径：{_SCENE_LABELS.get(scene, scene)} · {metric}")
    for p in data.get("products", []):
        facts = p.get("facts") or {}
        parts = [f"{_label(f, scene)} {_fmt_value(f, v)}"
                 for f, v in sorted(facts.items())]
        coverage = p.get("coverage")
        suffix = f"（覆盖 {coverage:.0%}）" if isinstance(coverage, (int, float)) else ""
        lines.append(f"【{p.get('name')}】" + "；".join(parts) + suffix)

    for d in data.get("dimensions", []):
        vals = d.get("values") or {}
        if not vals:
            continue
        if "跨平台" in (d.get("name") or ""):
            label = "综合性能指数（跨平台 GB6，单核50%+多核50%，最高=100）"
        else:
            label = f"{d.get('name')}指数（组内最高=100）"
        items = " / ".join(f"{k.split('|')[-1].replace('-', ' ')} {v}" for k, v in vals.items())
        lines.append(f"{label}：{items}")

    # 把「谁高谁低」写成确定性的句子，避免小模型把 93.8/90.4 当成单核/多核
    for field in ("bench.gb6.single", "bench.gb6.multi"):
        entries = [(p.get("name"), (p.get("facts") or {}).get(field))
                   for p in data.get("products", [])]
        entries = [(n, v) for n, v in entries if isinstance(v, (int, float))]
        if len(entries) >= 2:
            entries.sort(key=lambda x: x[1], reverse=True)
            head, tail = entries[0], entries[-1]
            if head[1] != tail[1]:
                lines.append(f"{_label(field, scene)}高低：{head[0]} {_fmt_value(field, head[1])} > "
                             f"{tail[0]} {_fmt_value(field, tail[1])}")

    if data.get("verdict"):
        # P3 的结论里带一段「仍有 N 个字段未检索到（含 spec.vram_gb）」，
        # 对 CPU 来说是无关字段，模型会照着念出来；这里只保留结论主体。
        verdict = re.split(r"仍有\s*\d+\s*个字段", data["verdict"])[0].rstrip("。；; ")
        lines.append(f"结论：{verdict}")
    if data.get("conflicts"):
        lines.append(f"注意：有 {len(data['conflicts'])} 处来源冲突，以上取置信度最高的一条。")
    return "\n".join(lines)


async def _compare_benchmarks(db, ctx: ToolContext, args: dict, order=None) -> dict:
    products = args.get("products") or []
    if isinstance(products, str):
        products = [products]
    products = [p.strip() for p in products if isinstance(p, str) and p.strip()]
    if len(products) < 2:
        return {"observation": "对比至少要两个型号，比如 9800X3D 和 285K。"}

    payload: dict = {"products": products}
    kind = args.get("kind") or _guess_kind(products, " ".join(products))
    if kind:
        payload["kind"] = kind
    if args.get("scene"):
        payload["scene"] = args["scene"]
    if args.get("metric"):
        payload["metric"] = args["metric"]
    try:
        data = await _mcp_call("compare_products", payload)
    except Exception as exc:  # 超时 / 网络 / 协议错误
        return {"observation": f"跑分库暂时连不上（{type(exc).__name__}），我先按商品库信息回答。",
                "data": {"error": str(exc)}}

    return {"observation": _render_compare(data), "data": data}


async def _get_bench_facts(db, ctx: ToolContext, args: dict, order=None) -> dict:
    pid = (args.get("product_id") or "").strip()
    if not pid:
        return {"observation": "给我产品 ID，例如 amd|ryzen-7-9800x3d。查 ID 可用 search_bench_products。"}
    try:
        data = await _mcp_call("get_product_facts", {"product_id": pid})
    except Exception as exc:
        return {"observation": f"跑分库暂时连不上（{type(exc).__name__}）。", "data": {"error": str(exc)}}
    if data.get("error"):
        return {"observation": data["error"], "data": data}

    lines = [f"【{data.get('name')}】{data.get('kind')}"]
    for field, item in sorted((data.get("facts") or {}).items()):
        lines.append(f"{_label(field, None)} {_fmt_value(field, item.get('value'))}"
                     f"（tier {item.get('tier')}，置信度 {item.get('confidence')}）")
        if item.get("source_url"):
            lines.append(f"  来源 {item['source_url']}")
    return {"observation": "\n".join(lines), "data": data}


async def _search_bench_products(db, ctx: ToolContext, args: dict, order=None) -> dict:
    query = (args.get("query") or "").strip()
    if not query:
        return {"observation": "给我型号片段，比如 5080 / 9800X3D。"}
    payload: dict = {"query": query, "limit": int(args.get("limit") or 5)}
    if args.get("kind"):
        payload["kind"] = args["kind"]
    try:
        data = await _mcp_call("search_products", payload)
    except Exception as exc:
        return {"observation": f"跑分库暂时连不上（{type(exc).__name__}）。", "data": {"error": str(exc)}}

    rows = data.get("results") or []
    if not rows:
        return {"observation": f"跑分库里没有匹配「{query}」的产品。", "data": data}
    lines = [f"{r.get('name')}（{r['id']}）{'已入库' if r.get('exists') else '未入库'}" for r in rows]
    return {"observation": "\n".join(lines), "data": data}


register_tool(
    name="compare_benchmarks",
    description="对比 2–8 个 CPU/GPU/SoC 的真实跑分（Geekbench 6 单核/多核、3DMark 同场景、功耗/工艺），"
                "数据来自内部跑分库（P3），返回事实、维度指数和冲突提示；"
                "用户问「哪个更强/差多少/值不值/跑分多少」且提到具体型号时优先用本工具，"
                "不要凭记忆编造跑分数字。没有具体型号时先问型号或先用 search_bench_products。",
    tool_type="read",
    params={"properties": {
        "products": {"type": "array", "items": {"type": "string"},
                     "description": "2–8 个型号名，如 [\"RTX 5080\", \"RX 9070 XT\"]"},
        "kind": {"type": "string", "enum": ["cpu", "gpu", "soc"],
                 "description": "可选：品类；不确定时不填，由跑分库推断"},
        "scene": {"type": "string",
                  "description": "可选：3DMark 场景，如 steel_nomad / time_spy；不填则自动选两边都有的场景"},
        "metric": {"type": "string", "enum": ["overall", "graphics", "cpu"],
                   "description": "可选：3DMark 口径（综合分/图形分/处理器分）；不填自动，按综合分优先"},
    }},
    handler=_compare_benchmarks, agents=["presale"],
)

register_tool(
    name="get_bench_facts",
    description="按产品 ID 取单个产品的跑分事实与来源（含 Geekbench 6、3DMark、TDP、工艺），"
                "需要给用户看依据或核对某个数字时用。产品 ID 形如 amd|ryzen-7-9800x3d。",
    tool_type="read",
    params={"properties": {
        "product_id": {"type": "string", "description": "稳定产品 ID，如 nvidia|rtx-5080"},
    }},
    handler=_get_bench_facts, agents=["presale"],
)

register_tool(
    name="search_bench_products",
    description="在跑分库里按名称片段找产品，拿到稳定 ID；没入库的型号会标明「未入库」。"
                "用户只给了模糊型号、或需要先确认跑分库有没有这款时用。",
    tool_type="read",
    params={"properties": {
        "query": {"type": "string", "description": "型号片段，如 5080 / 9800X3D / 9070"},
        "kind": {"type": "string", "enum": ["cpu", "gpu", "soc"],
                 "description": "可选：限定品类"},
        "limit": {"type": "integer", "description": "可选：返回条数，默认 5"},
    }},
    handler=_search_bench_products, agents=["presale"],
)
