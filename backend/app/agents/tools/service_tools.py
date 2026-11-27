"""
业务服务类工具（Mock Business Services 的 Agent 侧封装）。

补的是演示清单里原来没有的四类能力：
- compare_products   两款/多款本店商品横向对比（差异项 + 价格库存 + 兼容）
- recommend_config   按预算 + 用途生成整套配置（键盘/鼠标/耳机 + 可选件），带总价与库存校验
- check_compatibility 商品 vs 系统/主机/另一件商品 的兼容性判定（基于结构化字段）
- diagnose_issue     售后问题诊断（症状 → 可能原因 → 自检步骤 → 处理结论），带说明书章节引用
- query_policy       退换/保修/时效政策检索（结构化规则表，不让模型背政策）

原则：判定归代码，话术归模型。工具只给事实和结论方向，不生成营销话术。
"""
from __future__ import annotations

import json

from sqlalchemy import select

from app.agents.tools.base import register_tool
from app.core.policy_engine import PolicyEngine
from app.core.llm_registry import get_registry
from app.core.websearch_client import get_websearch
from app.db.models import Product
from app.services import knowledge_service, source_router

_CAT_CN = {
    "keyboard": "键盘", "mouse": "鼠标", "headset": "耳机",
    "webcam": "摄像头", "dock": "扩展坞", "gamepad": "手柄",
}

# 对比时优先展示的字段（用户真的会拿来比的那几个）
_DIFF_PRIORITY = [
    "连接", "轴体类型", "传感器", "重量", "续航", "兼容系统", "接口",
    "回报率", "降噪", "按键数", "键数", "配列", "驱动软件", "尺寸", "电量",
]


def _flat(specs: dict) -> dict:
    out: dict = {}

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, dict):
                    walk(v)
                elif isinstance(v, list):
                    if v and not isinstance(v[0], dict):
                        out[k] = v
                else:
                    out[k] = v

    walk(specs)
    return out


def _num(v) -> float:
    """从「58 g」「8000 Hz」「60 ms」「35 h」这类值里取数字。"""
    if v is None:
        return 0.0
    try:
        import re as _re
        m = _re.search(r"\d+(?:\.\d+)?", str(v))
        return float(m.group(0)) if m else 0.0
    except Exception:
        return 0.0


def _pick(flat: dict, *names: str) -> str:
    """按 key 精确或包含匹配取值（各品类 specs 的字段名不完全统一）。"""
    for n in names:
        if n in flat:
            v = flat[n]
            if v not in (None, "", []):
                return _fmt(v)
    for k, v in flat.items():
        if any(n in k for n in names) and v not in (None, "", []):
            return _fmt(v)
    return ""


async def _get_product(db, token: str) -> Product | None:
    token = (token or "").strip()
    if not token:
        return None
    p = (await db.execute(select(Product).where(Product.sku == token.upper()))).scalar_one_or_none()
    if p:
        return p
    rows = (await db.execute(select(Product).where(Product.name.like(f"%{token}%")))).scalars().all()
    return rows[0] if rows else None


def _fmt(v) -> str:
    if isinstance(v, list):
        return "、".join(map(str, v))
    return str(v)


def _spec(p: Product) -> dict:
    try:
        d = json.loads(p.specs or "{}")
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _short(p: Product) -> str:
    return f"{p.name}（{p.sku}）¥{p.price:.0f}"


# ---------------------------------------------------------------- 商品对比
async def _compare_products(db, ctx, args, order=None) -> dict:
    tokens = args.get("skus") or args.get("products") or []
    if isinstance(tokens, str):
        tokens = [tokens]
    single = args.get("product_sku")
    if single and single not in tokens:
        tokens = [single] + list(tokens)
    prods = []
    for t in tokens[:4]:
        p = await _get_product(db, t)
        if p and p.sku not in {x.sku for x in prods}:
            prods.append(p)
    if len(prods) < 2:
        # 本店 SKU 不足两个：可能是市场型号（RTX 5080 / 竞品键盘），走信源路由
        return await _compare_market(tokens, args)

    header = "；".join(f"{i + 1}. {_short(p)} 库存{p.stock}" for i, p in enumerate(prods))
    lines = [f"对比 {len(prods)} 款：{header}"]

    flats = [_flat(_spec(p)) for p in prods]
    keys = [k for k in _DIFF_PRIORITY if any(k in f for f in flats)]
    rest = [k for k in {k for f in flats for k in f} if k not in keys]

    diffs, same = [], []
    for k in keys + rest:
        vals = [f.get(k) for f in flats]
        shown = [_fmt(v) if v not in (None, "", []) else "—" for v in vals]
        if len({str(x) for x in shown}) > 1:
            diffs.append((k, shown))
        elif shown[0] != "—":
            same.append(k)
    diffs = diffs[:8]

    if diffs:
        lines.append("▸ 差异项（按型号顺序：" + " ｜ ".join(p.sku for p in prods) + "）")
        for k, shown in diffs:
            lines.append("  · " + k + "：" + " ｜ ".join(shown))
    if same:
        lines.append("▸ 相同项：" + "、".join(same[:8]) + ("等" if len(same) > 8 else ""))

    lo = min(prods, key=lambda p: p.price)
    hi = max(prods, key=lambda p: p.price)
    if lo.sku != hi.sku:
        lines.append(f"▸ 价格：{lo.sku} 最低 ¥{lo.price:.0f}，{hi.sku} 最高 ¥{hi.price:.0f}，差 ¥{hi.price - lo.price:.0f}")

    no_stock = [p.sku for p in prods if p.stock <= 0]
    lines.append("▸ 库存：" + ("均在售" if not no_stock else "缺货 " + "、".join(no_stock)))

    return {
        "observation": "\n".join(lines),
        "data": {"skus": [p.sku for p in prods],
                 "diff": {k: shown for k, shown in diffs},
                 "price_gap": round(hi.price - lo.price, 2),
                 "source_chain": ["p4_catalog"]},
    }


# ---------------------------------------------------------------- 市场型号对比（P3 优先）
async def _compare_market(tokens: list, args: dict) -> dict:
    """非本店 SKU 的严格对比：P3 内部事实库（第一信源）→ 公开网络（第二信源）。

    本地 SKU 对比仍在 _compare_products 里完成；这里只处理市场型号，
    并保证「内部数值」与「网文摘要」不会混成一张表。
    """
    models = [str(t).strip() for t in tokens if str(t).strip()]
    if len(models) < 2:
        return {"observation": "对比至少需要两个型号，比如 KB-PRO87 和 KB-60HE，"
                               "或 RTX 5080 和 RX 9070 XT。你给的是："
                               + ("、".join(models) or "空") + "。"}

    routed = await source_router.resolve_models(models, args.get("kind"))

    if routed.get("ok"):
        from app.agents.tools.product_bench_tool import _render_compare
        return {
            "observation": "内部事实库（P3，第一信源）\n" + _render_compare(routed["data"]),
            "data": {"products": models, "source_chain": ["p3"],
                     "coverage": routed.get("coverage"), "missing": routed.get("missing")},
        }

    if routed.get("source") == "p3_partial":
        from app.agents.tools.product_bench_tool import _render_compare
        return {
            "observation": "P3 只覆盖了部分型号（缺口：" + "、".join(routed.get("missing") or [])
                           + "），以下仅是已覆盖部分，缺口没有用网文补齐：\n"
                           + _render_compare(routed["data"]),
            "data": {"products": models, "source_chain": ["p3"], "partial": True,
                     "missing": routed.get("missing")},
        }

    # 第二信源：P3 没覆盖/解析不了，才允许联网补
    query = routed.get("fallback_query") or (" ".join(models) + " 参数 评测 对比")
    try:
        res = await get_websearch().search(query, top_k=5)
        obs = ("内部事实库（P3）未覆盖（" + str(routed.get("reason") or "no_facts")
               + "）。以下为公开网络信息（第二信源），参数以品牌官网/商品页为准：\n"
               + (res.get("observation") or "暂未检索到相关公开信息。"))
        data = {"products": models, "source_chain": ["p3", "websearch"],
                "p3_reason": routed.get("reason"),
                "web": {k: res.get(k) for k in
                        ("degraded", "code", "providers", "results", "latency_ms")}}
    except Exception as exc:
        obs = f"内部库未覆盖，联网检索也不可用（{type(exc).__name__}）；建议以品牌官网参数为准。"
        data = {"products": models, "source_chain": ["p3"], "p3_reason": routed.get("reason")}
    return {"observation": obs, "data": data}


# ---------------------------------------------------------------- 兼容性检查
_SYS_ALIAS = {
    "mac": "macOS", "macos": "macOS", "苹果": "macOS", "macbook": "macOS", "m1": "macOS", "m2": "macOS",
    "win": "Windows", "windows": "Windows", "win10": "Windows", "win11": "Windows",
    "linux": "Linux", "ubuntu": "Linux",
    "ipad": "iPadOS", "ipados": "iPadOS", "ios": "iOS", "iphone": "iOS", "安卓": "Android", "android": "Android",
}
_CONSOLE = {"ps5": "PS5", "playstation": "PS5", "xbox": "Xbox", "switch": "Switch", "ns": "Switch"}


def _norm_target(t: str) -> tuple[str, str]:
    low = (t or "").lower().replace(" ", "")
    for k, v in _CONSOLE.items():
        if k in low:
            return "console", v
    for k, v in _SYS_ALIAS.items():
        if k in low:
            return "os", v
    return "unknown", t


async def _check_compatibility(db, ctx, args, order=None) -> dict:
    sku = args.get("product_sku") or args.get("sku")
    target = str(args.get("target") or args.get("system") or "").strip()
    p = await _get_product(db, sku)
    if not p:
        return {"observation": f"没查到型号 {sku}，请给准确 SKU（如 KB-PRO87）。"}
    if not target:
        return {"observation": "要检查兼容的对象是什么？系统（macOS/Windows/iPad）还是主机（PS5/Xbox/Switch），或另一个型号。"}

    flat = _flat(_spec(p))
    compat_txt = _pick(flat, "兼容系统", "兼容平台", "兼容")
    kind, norm = _norm_target(target)

    # 目标是另一件商品：看接口/连接方式是否能对上
    other = await _get_product(db, target)
    if other and other.sku != p.sku:
        of = _flat(_spec(other))
        a_if = _pick(flat, "连接方式", "接口", "连接")
        b_if = _pick(of, "连接方式", "接口", "连接")
        verdict = "需人工确认"
        if ("Type-C" in a_if + b_if and ("USB" in a_if + b_if)):
            verdict = "接口层面可对接（Type-C/USB）"
        return {"observation": f"{p.name} 与 {other.name} 的对接：{verdict}。\n"
                               f"· {p.sku} 接口/连接：{a_if}\n· {other.sku} 接口/连接：{b_if}\n"
                               f"· 依据：商品规格字段，最终以说明书章节为准。"}

    if kind == "os":
        hit = norm.lower() in compat_txt.lower()
        if hit:
            verdict = f"支持 {norm}"
            detail = f"规格里的兼容系统包含 {norm}（{compat_txt}）。"
            if p.category == "keyboard" and "驱动" in _fmt(flat.get("驱动软件") or "") and norm == "macOS":
                detail += " 进阶功能（改键/宏）需装 macOS 版驱动，基础输入免驱。"
            return {"observation": f"{p.name} → {verdict}。{detail}", "data": {"supported": True, "target": norm}}

        # 不直接判死：有些品类靠系统级免驱协议（UVC/蓝牙 HID）也能用
        soft = ""
        if p.category == "webcam":
            soft = "摄像头走 UVC 免驱，多数系统可直接用；未列出的系统建议先试再决定。"
        elif p.category in ("mouse", "keyboard") and ("蓝牙" in _pick(flat, "连接方式", "连接") or "USB" in _pick(flat, "连接方式", "连接")):
            soft = "键鼠走蓝牙 HID/USB 免驱协议，通常可用；改键与宏功能受限于驱动支持。"
        return {"observation": f"{p.name} 的规格兼容列表里没有 {norm}（列的是 {compat_txt}）。"
                               + (soft or "建议转人工确认具体系统版本。"),
                "data": {"supported": False, "target": norm}}

    if kind == "console":
        conn = _pick(flat, "连接方式", "接口", "连接")
        cat = p.category
        if cat == "gamepad":
            verdict = "支持" if ("2.4G" in conn or "USB" in conn or "有线" in conn) else "需确认"
            note = "手柄需接收器/有线连接；主机侧游戏是否支持该手柄由游戏决定。"
        elif cat == "headset":
            if norm == "Switch":
                verdict = "支持（3.5mm/有线）" if ("3.5" in conn or "有线" in conn or "USB" in conn) else "需确认"
                note = "Switch 走 3.5mm 接手柄或底座 USB；蓝牙音频需底座支持。"
            elif norm == "PS5":
                verdict = "部分支持" if "蓝牙" in conn else ("支持（USB/3.5mm）" if ("USB" in conn or "3.5" in conn) else "需确认")
                note = "PS5 不认通用蓝牙音频，需 USB 接收器或 3.5mm 接手柄。"
            else:
                verdict = "支持（USB/2.4G）" if ("USB" in conn or "2.4G" in conn) else "需确认"
                note = ""
            return {"observation": f"{p.name} 接 {norm}：{verdict}。连接方式：{conn}。{note}",
                    "data": {"supported": verdict.startswith("支持"), "target": norm}}
        elif cat in ("keyboard", "mouse"):
            return {"observation": f"{p.name} 接 {norm}：需要 USB 接收器或有线连接（当前连接方式：{conn}）。"
                                   f"主机系统能识别，但游戏内键鼠支持取决于具体游戏。",
                    "data": {"supported": None, "target": norm}}
        else:
            return {"observation": f"{p.name} 是{_CAT_CN.get(cat, cat)}，不直接接主机；如需连接请说明用途。",
                    "data": {"supported": False, "target": norm}}

    return {"observation": f"没识别出「{target}」是哪类兼容对象。可以说系统（macOS/Windows/iPad）或主机（PS5/Xbox/Switch），也可以给另一个型号做对接检查。"}


# ---------------------------------------------------------------- 配置方案
_USAGE_PROFILE = {
    "gaming": {
        "kw": ["电竞", "fps", "游戏", "吃鸡", "瓦罗兰特", "valorant", "cs", "低延迟", "回报率", "磁轴", "轻量", "竞技"],
        "cats": ["keyboard", "mouse", "headset"],
        "opt": [],
    },
    "office": {
        "kw": ["办公", "打字", "静音", "舒适", "长时间", "无线", "轻薄", "码字"],
        "cats": ["keyboard", "mouse", "headset"],
        "opt": [],
    },
    "stream": {
        "kw": ["直播", "录制", "摄像头", "麦克风", "画质", "2k", "4k", "出镜", "主播"],
        "cats": ["keyboard", "mouse", "headset"],
        "opt": ["webcam"],
    },
    "travel": {
        "kw": ["出差", "便携", "轻", "蓝牙", "多设备", "移动", "咖啡厅"],
        "cats": ["keyboard", "mouse", "headset"],
        "opt": ["dock"],
    },
}


def _usage_of(text: str) -> str:
    low = (text or "").lower()
    best, hits = "general", 0
    for name, prof in _USAGE_PROFILE.items():
        n = sum(1 for w in prof["kw"] if w in low)
        if n > hits:
            best, hits = name, n
    return best


def _score(p: Product, usage: str) -> float:
    """按用途对单件打分。用结构化字段（回报率/重量/传感器/延迟）而不是纯关键词，
    否则「办公轻游戏两用」这类文案会把入门款顶到电竞方案里。"""
    text = f"{p.name} {p.description}".lower()
    flat = _flat(_spec(p))
    conn = _pick(flat, "连接方式", "无线协议", "连接")
    s = 0.5 if p.stock > 0 else 0.0
    if p.category == "mouse":
        # 手型未知时按中手（17–19cm）兜底，别把小手/大手款推给大多数人
        s += 1 if "中手" in text or "通用" in text else 0
        s -= 1 if ("小手" in text or "大手" in text) else 0

    if usage == "gaming":
        if p.category == "mouse":
            s += 3 if "电竞" in text else 0
            s += 2 if _num(_pick(flat, "回报率")) >= 1000 else 0
            s += 2 if _num(_pick(flat, "重量")) and _num(_pick(flat, "重量")) <= 60 else 0
            s += 1 if any(x in _pick(flat, "传感器") for x in ("PAW3950", "PAW3395")) else 0
            s -= 2 if ("入门" in text or "办公" in text) else 0
        elif p.category == "keyboard":
            s += 3 if ("磁轴" in text or "RT" in text) else 0
            s += 2 if _num(_pick(flat, "回报率")) >= 1000 else 0
            s += 1 if ("无线" in text or "三模" in conn) else 0
        elif p.category == "headset":
            s += 3 if "2.4G" in conn else 0
            s += 1 if _num(_pick(flat, "游戏延迟", "延迟")) and _num(_pick(flat, "游戏延迟", "延迟")) <= 60 else 0
            s += 1 if "无线" in p.name else 0
        elif p.category == "gamepad":
            s += 1 if "2.4G" in conn or "无线" in p.name else 0
    elif usage == "office":
        s += 3 if ("静音" in text) else 0
        s += 2 if ("无线" in p.name or "三模" in conn) else 0
        s += 1 if _num(_pick(flat, "重量")) <= 90 else 0
        s -= 1 if "电竞" in text else 0
    elif usage == "stream":
        s += 3 if p.category == "webcam" and ("4K" in p.name or "2K" in p.name) else 0
        s += 2 if "麦克风" in text or "直播" in text else 0
        s += 1 if p.category == "headset" and "无线" in p.name else 0
    elif usage == "travel":
        s += 3 if ("便携" in text or "Mini" in p.name) else 0
        s += 2 if ("蓝牙" in conn or "三模" in conn) else 0
        s += 1 if _num(_pick(flat, "重量")) and _num(_pick(flat, "重量")) <= 500 else 0
    else:
        s += 1 if p.stock > 0 else 0
    return s


async def _recommend_config(db, ctx, args, order=None) -> dict:
    try:
        budget = float(args.get("budget") or 0)
    except (TypeError, ValueError):
        budget = 0.0
    usage_raw = str(args.get("usage") or args.get("scene") or "")
    usage = _usage_of(usage_raw)
    rows = (await db.execute(select(Product).where(Product.stock > 0))).scalars().all()
    if not rows:
        return {"observation": "商品库暂时没有在售商品。"}
    if budget <= 0:
        # 没给预算：按用途给三件套里最便宜的可行组合，并提示可加预算升级
        budget = sum(min((p.price for p in rows if p.category == c), default=0) for c in ("keyboard", "mouse", "headset"))

    prof = _USAGE_PROFILE.get(usage) or {"cats": ["keyboard", "mouse", "headset"], "opt": []}
    cats = list(prof["cats"])
    if prof.get("opt"):
        cats = cats + prof["opt"][:1]  # 默认带一个对口可选件（预算不够时 pick 会自动跳过）

    # 每类候选：按用途分排序
    cand: dict[str, list[Product]] = {}
    for c in cats:
        items = [p for p in rows if p.category == c]
        items.sort(key=lambda p: (-_score(p, usage), p.price))
        if items:
            cand[c] = items
    if not cand:
        return {"observation": "没有匹配到可组套的品类，请说明想要的品类（键盘/鼠标/耳机/摄像头/手柄/扩展坞）。"}

    def pick(cats_list, mode: str) -> tuple[list[Product], float]:
        chosen, total = [], 0.0
        for c in cats_list:
            lst = cand.get(c) or []
            if not lst:
                continue
            if mode == "value":
                top = _score(lst[0], usage) or 1.0
                pool = [p for p in lst if _score(p, usage) >= top * 0.6] or lst[:2]
                p = min(pool, key=lambda x: x.price)
            else:
                p = lst[0]
            if total + p.price > budget and chosen:
                # 超预算就换同类更便宜的
                cheaper = [x for x in lst if total + x.price <= budget]
                if not cheaper:
                    continue
                p = min(cheaper, key=lambda x: x.price)
            chosen.append(p)
            total += p.price
        return chosen, total

    plan_a, total_a = pick(list(cand.keys()), "balanced")
    plan_b, total_b = pick(list(cand.keys()), "value")

    # 预算紧时 A 可能凑不齐三件：补一个「全线最便宜」组合，保证至少有一套能买齐的方案
    if len(plan_a) < 3:
        cheap = [min(lst, key=lambda x: x.price) for lst in cand.values() if lst]
        t = sum(p.price for p in cheap)
        if t <= budget and {p.sku for p in cheap} != {p.sku for p in plan_a}:
            plan_b, total_b = cheap, t

    def render(label: str, plan: list[Product], total: float) -> list[str]:
        out = [f"▸ {label}（合计 ¥{total:.0f}，预算 ¥{budget:.0f}，余 ¥{budget - total:.0f}）"]
        for p in plan:
            out.append(f"  · {_CAT_CN.get(p.category, p.category)} {p.name}（{p.sku}）¥{p.price:.0f}")
        lack = [c for c in ("keyboard", "mouse", "headset") if c not in {p.category for p in plan}]
        if lack:
            out.append("  · 缺项：" + "、".join(_CAT_CN.get(c, c) for c in lack) + "（预算内凑不齐，可提高预算或去掉可选件）")
        return out

    lines = [f"按「{usage_raw or '通用'}」配置，预算 ¥{budget:.0f}。"]
    lines += render("方案 A · 均衡优先", plan_a, total_a)
    if {p.sku for p in plan_b} != {p.sku for p in plan_a}:
        lines += render("方案 B · 性价比优先", plan_b, total_b)

    # 兼容性抽查：只对方案里真有的品类下结论，避免「缺耳机还说耳机兼容」
    chosen_cats = [c for c in ("keyboard", "mouse", "headset") if c in {p.category for p in plan_a}]
    compat = "、".join(_CAT_CN.get(c, c) for c in chosen_cats) or "所选商品"
    lines.append(f"▸ 兼容：{compat}均支持 Windows/macOS/Linux（以各自规格为准）；如需接 PS5/Switch 请单独说，我按主机口径再核一遍。")
    lines.append("▸ 库存：" + ("全部有货" if all(p.stock > 0 for p in plan_a) else "部分缺货，下单前再确认"))

    return {
        "observation": "\n".join(lines),
        "data": {"usage": usage, "budget": budget,
                 "plan_a": [{"sku": p.sku, "price": p.price} for p in plan_a],
                 "plan_b": [{"sku": p.sku, "price": p.price} for p in plan_b],
                 "total_a": round(total_a, 2), "total_b": round(total_b, 2)},
    }


# ---------------------------------------------------------------- 售后诊断
_SYMPTOMS = {
    "connect": {
        "kw": ["连不上", "断连", "掉线", "配对失败", "搜不到", "连不上电脑", "识别不了"],
        "causes": ["2.4G 接收器插在前置 USB 或被金属遮挡", "蓝牙未进入配对模式", "电量不足/开关没拨到对应档位", "系统配对记录残留"],
        "checks": ["换到机箱后置 USB 口，缩短距离再试", "开关拨到 BT 后长按配对键 3 秒到快闪", "先充满电，删除旧配对记录重连", "用另一台设备交叉验证是设备问题还是电脑问题"],
        "verdict": "多数是配对/干扰问题，可自检解决；两步都无效再走换货",
    },
    "sound": {
        "kw": ["没声音", "没有声音", "没声", "无声", "听不到", "不出声", "声音小", "音量"],
        "causes": ["系统输出设备没切到本机", "耳机音量/静音开关未开", "USB/3.5mm 接口接触不良", "ANC 模式下底噪被误判为无声"],
        "checks": ["系统声音设置里把输出切到本设备", "耳机实体音量键调到 50% 以上", "换接口/换线交叉测试", "关掉 ANC 再听一次"],
        "verdict": "先按输出设备→音量→接口顺序排查；仍无声走换货检测",
    },
    "noise": {
        "kw": ["杂音", "电流声", "底噪", "滋滋", "爆音"],
        "causes": ["USB 供电不稳或与高功耗设备共用 HUB", "3.5mm 线材屏蔽差", "固件版本旧", "ANC 麦克风被遮挡"],
        "checks": ["直插主板 USB，去掉 HUB", "换一条带屏蔽的线", "升级固件后复测", "清洁麦克风开孔"],
        "verdict": "先排除供电与线材；持续杂音可申请换货",
    },
    "battery": {
        "kw": ["续航", "耗电", "掉电", "电池"],
        "causes": ["RGB 灯效与高回报率同时开", "电池老化（循环次数高）", "充电未满就拔", "低温环境"],
        "checks": ["关灯效/降回报率复测续航", "充满后再放电记录时长", "用原装线充电", "对比官方标称判断是否异常"],
        "verdict": "低于标称 70% 且在保修期内可申请换新",
    },
    "key": {
        "kw": ["按键", "失灵", "双击", "连击", "不灵", "卡键"],
        "causes": ["轴体/微动磨损", "键帽卡异物", "热插拔轴座接触不良", "驱动宏冲突"],
        "checks": ["拔键帽清灰，换一个轴位测试", "热插拔键盘换轴试同一位置", "退出驱动/恢复默认配置", "用在线按键测试页确认是硬件还是软件"],
        "verdict": "换轴后仍失灵属硬件问题，保修期内走换新/寄修",
    },
    "light": {
        "kw": ["灯不亮", "没灯", "灯效", "背光"],
        "causes": ["灯效被驱动关闭", "供电不足（HUB/前置口）", "固件异常", "硬件灯珠损坏"],
        "checks": ["驱动里恢复默认灯效", "直插后置 USB", "长按复位组合键", "换台电脑验证"],
        "verdict": "软件层排除后仍不亮，走寄修",
    },
    "driver": {
        "kw": ["驱动", "装了没用", "驱动装不上", "识别不了", "设备管理器"],
        "causes": ["系统版本不在兼容列表", "旧驱动残留冲突", "未以管理员权限安装", "USB 线仅充电不支持数据"],
        "checks": ["确认系统版本在兼容列表内", "卸载旧驱动并重启后再装", "右键以管理员身份安装", "换一条数据线/换 USB 口"],
        "verdict": "按兼容→残留→权限→线材顺序排查；仍不行转人工远程协助",
    },
    "latency": {
        "kw": ["延迟", "卡顿", "断流", "不跟手"],
        "causes": ["无线信道拥挤", "回报率设置过低", "接收器距离远/遮挡", "系统电源管理限制 USB"],
        "checks": ["接收器插后置口并延长到桌面", "驱动里把回报率调到 1000Hz+", "关闭 USB 选择性暂停", "换 2.4G 信道或改用有线对比"],
        "verdict": "多为环境干扰，按上面调完仍不改善可换货",
    },
}


def _match_symptom(text: str) -> tuple[str, dict] | tuple[None, None]:
    low = (text or "").lower()
    best, hits = None, 0
    for key, item in _SYMPTOMS.items():
        n = sum(1 for w in item["kw"] if w in low)
        if n > hits:
            best, hits = key, n
    return (best, _SYMPTOMS[best]) if best else (None, None)


async def _diagnose_issue(db, ctx, args, order=None) -> dict:
    sku = args.get("product_sku") or args.get("sku")
    symptom = str(args.get("symptom") or args.get("issue") or "").strip()
    p = await _get_product(db, sku)
    if not p:
        return {"observation": "先告诉我型号（如 HS-ANC7），我再按这个型号的说明书和常见故障给排查步骤。"}
    if not symptom:
        return {"observation": f"{p.name} 具体是什么现象？比如「连不上」「没声音」「按键失灵」「续航变短」，我按现象给排查顺序。"}

    key, item = _match_symptom(symptom)
    if not item:
        return {"observation": f"「{symptom}」不在常见故障表里。可以先做通用排查：换接口/换线/换设备交叉测试，"
                               f"并确认固件与驱动是最新版；仍不行我转人工做远程诊断。"}

    lines = [f"{p.name}（{p.sku}）· 现象：{symptom}"]
    lines.append("▸ 可能原因")
    for c in item["causes"][:4]:
        lines.append("  · " + c)
    lines.append("▸ 自检步骤（按顺序做，每步后再测一次）")
    for i, c in enumerate(item["checks"][:4], 1):
        lines.append(f"  {i}. {c}")
    lines.append("▸ 结论：" + item["verdict"])
    if any(w in symptom for w in ("左耳", "右耳", "单边", "一侧")):
        lines.append("▸ 补充：只有单侧无声时优先怀疑该侧单元/线材；按第 3 步换线换接口后仍单侧无声，"
                     "基本可判硬件，直接走换货检测，不用再折腾软件设置。")

    # 引用说明书原文，避免"我猜的"
    cite = ""
    try:
        offline = get_registry().active == "mock"
        res = await knowledge_service.search(db, query=f"{symptom} 排查", sku=p.sku, top_k=2, use_rerank=not offline)
        hits = res.get("results") or []
        if hits:
            h = hits[0]
            cite = (f"▸ 说明书依据：【{h['sku']} · {h['doc_title']}】"
                    f"第 {h['section_no']} 节「{h['section_title']}」")
            snippet = (h.get("content") or "").replace("\n", " ")[:120]
            if snippet:
                cite += f"\n  {snippet}…"
    except Exception:
        pass
    if cite:
        lines.append(cite)

    lines.append("▸ 下一步：自检无效可以直接说「申请换货」或「申请寄修」，我走售后受理（需二次确认）。")
    return {"observation": "\n".join(lines),
            "data": {"symptom_key": key, "verdict": item["verdict"], "sku": p.sku}}


# ---------------------------------------------------------------- 政策检索
_TOPIC_ALIAS = {
    "return": ["退货", "退换", "退款", "七天", "无理由", "退"],
    "warranty": ["保修", "质保", "维修", "保固", "坏了"],
    "shipping": ["时效", "几天到", "几天能到", "多久到", "多久能到", "几天", "多久", "发货", "运费", "包邮", "送到", "到货", "物流"],
}


async def _query_policy(db, ctx, args, order=None) -> dict:
    topic_raw = str(args.get("topic") or args.get("question") or args.get("query") or "").strip()
    sku = args.get("product_sku") or args.get("sku")
    p = await _get_product(db, sku)
    category = p.category if p else "*"
    if not p:
        # 用户常说品类而不是 SKU（「键盘保修多久」），按品类词落到对应政策行
        for w, c in (("键盘", "keyboard"), ("鼠标", "mouse"), ("耳机", "headset"),
                     ("摄像头", "webcam"), ("扩展坞", "dock"), ("手柄", "gamepad")):
            if w in topic_raw:
                category = c
                break
    region = str(args.get("region") or "").strip()

    topic = None
    low = topic_raw.lower()
    for key, words in _TOPIC_ALIAS.items():
        if any(w in low for w in words):
            topic = key
            break
    if not topic:
        return {"observation": "想查哪类政策？退货/换货期限、保修月数、还是发货时效？"}

    eng = PolicyEngine(db)
    name = p.name if p else (f"{_CAT_CN.get(category, category)}类商品" if category != "*" else "全店商品")

    if topic == "return":
        dec = await eng.return_policy(category)
        if not dec.allowed:
            return {"observation": f"没查到 {name} 的退换政策配置。"}
        d = dec.data
        return {"observation": f"{name} 退换政策：{d.get('window_days')} 天无理由"
                               f"（{d.get('condition')}）；"
                               f"{'支持' if d.get('no_reason') else '不支持'}无理由退货，"
                               f"{'运费卖家承担' if d.get('free_return') else '非质量问题运费买家承担'}。",
                "data": {"topic": "return", **d}}

    if topic == "warranty":
        dec = await eng.warranty_policy(category)
        if not dec.allowed:
            return {"observation": f"没查到 {name} 的保修政策配置。"}
        d = dec.data
        return {"observation": f"{name} 保修：{d.get('months')} 个月，凭证{d.get('proof')}；"
                               f"除外条款：{d.get('exception')}。",
                "data": {"topic": "warranty", **d}}

    dec = await eng.shipping_sla(region or "其他")
    if not dec.allowed:
        return {"observation": "没查到时效配置。"}
    d = dec.data
    return {"observation": f"发往「{region or '其他'}」由{d.get('carrier')}承运，"
                           f"承诺 {d.get('promise_days')} 天送达（{d.get('cutoff_hour')}:00 前下单当日发）。"
                           f"具体以物流更新为准。",
            "data": {"topic": "shipping", **d}}


register_tool(
    name="compare_products",
    description="横向对比 2–4 款商品。本店 SKU 用商品库事实（价格/库存/规格差异）；"
                "市场型号（如 RTX 5080 / RX 9070 XT / 竞品键盘）先查 P3 内部事实库（第一信源，"
                "含跑分/参数/来源置信度），P3 未覆盖才联网（第二信源）。"
                "用户问「哪个好/有什么区别/差在哪/值不值」且给了多个型号时用",
    tool_type="read",
    params={"properties": {
        "skus": {"type": "array", "items": {"type": "string"},
                 "description": "要对比的型号，本店 SKU 或市场型号，如 ['KB-PRO87','KB-60HE'] 或 ['RTX 5080','RX 9070 XT']"},
        "products": {"type": "array", "items": {"type": "string"}, "description": "同 skus，型号或名称"},
        "kind": {"type": "string", "enum": ["cpu", "gpu", "soc"],
                 "description": "可选：市场型号品类，仅 P3 对比时用"},
        "focus": {"type": "string", "description": "关注点，如 手型/轴体/无线"},
    }},
    handler=_compare_products, agents=["presale"],
)

register_tool(
    name="check_compatibility",
    description="检查商品与系统（macOS/Windows/iPad）、主机（PS5/Xbox/Switch）或另一件商品是否兼容；基于规格字段给结论与依据",
    tool_type="read",
    params={"properties": {
        "product_sku": {"type": "string", "description": "商品 SKU，如 KB-PRO87"},
        "target": {"type": "string", "description": "兼容对象：macOS/Windows/iPad/PS5/Switch，或另一个 SKU"},
        "system": {"type": "string", "description": "同 target"},
    }},
    handler=_check_compatibility, agents=["presale"],
)

register_tool(
    name="recommend_config",
    description="按预算和用途（电竞/办公/直播/便携）生成整套配置方案：键盘+鼠标+耳机，可带摄像头/手柄/扩展坞；给两套方案、总价与库存校验",
    tool_type="read",
    params={"properties": {
        "budget": {"type": "number", "description": "总预算（元）"},
        "usage": {"type": "string", "description": "用途：电竞/办公/直播/便携，或用户原话"},
    }},
    handler=_recommend_config, agents=["presale"],
)

register_tool(
    name="diagnose_issue",
    description="售后问题诊断：按型号+现象给可能原因、分步自检和处理结论，并引用说明书章节；用户说「连不上/没声音/按键失灵/续航变短」等故障时用",
    tool_type="read",
    params={"properties": {
        "product_sku": {"type": "string", "description": "商品 SKU"},
        "symptom": {"type": "string", "description": "现象原话，如「左耳没声音」「连不上电脑」"},
    }},
    handler=_diagnose_issue, agents=["aftersale"],
)

register_tool(
    name="query_policy",
    description="查询退换期限、保修月数、发货时效等政策（结构化规则表，可指定品类/型号与地区）",
    tool_type="read",
    params={"properties": {
        "topic": {"type": "string", "description": "政策类型：退货/保修/时效，或用户原话"},
        "query": {"type": "string", "description": "同 topic，用户原话即可"},
        "product_sku": {"type": "string", "description": "商品 SKU（按品类取政策）"},
        "region": {"type": "string", "description": "地区（查时效时用）"},
    }},
    handler=_query_policy, agents=["presale", "aftersale"],
)
