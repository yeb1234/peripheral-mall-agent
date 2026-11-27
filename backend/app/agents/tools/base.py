"""
工具注册表与执行结果。

设计原则（文档「工具层」）：
- 每个工具声明 type = read | write，写类一律走六道关，读类直通。
- 工具入参用 JSON Schema 强校验，多余字段拒绝。
- requires_order=True 的工具，订单 ID 由服务端从会话绑定推导，模型【不得】指定。

ToolResult 是工具执行后的统一返回，下游（Agent 节点 / 前端）据此渲染。
"""
from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field


@dataclass
class ToolResult:
    ok: bool
    observation: str
    needs_confirm: bool = False
    confirmation: dict | None = None
    data: dict | None = None
    need_bind: bool = False  # 需要用户先核验身份


@dataclass
class ToolContext:
    db: object
    session_id: str
    buyer_id: str
    channel: str = "web"
    # 本轮用户原话：用于「绑定订单里按尾号消歧」等确定性匹配，不交给模型自由指定订单
    user_text: str = ""


# name -> 工具定义
TOOLS: dict[str, dict] = {}


def register_tool(
    *,
    name: str,
    description: str,
    tool_type: str,            # "read" | "write"
    params: dict,             # JSON Schema 风格的参数声明
    handler,                  # async fn(db, ctx, args, order=None) -> dict
    requires_order: bool = False,
    agents: list[str] | None = None,
    sensitive: bool = False,  # 高敏（如补偿）
) -> None:
    TOOLS[name] = {
        "name": name,
        "description": description,
        "type": tool_type,
        "params": params,
        "handler": handler,
        "requires_order": requires_order,
        "agents": agents or ["presale", "insale", "aftersale"],
        "sensitive": sensitive,
    }


def tools_for_agent(agent: str) -> list[dict]:
    return [
        {k: v for k, v in t.items() if k != "handler"}
        for t in TOOLS.values()
        if agent in (t["agents"] or [])
    ]


# 真实模型常把参数名写错（sku / model / product …），做受控别名归一化。
# 只有「原字段名不在该工具 schema 里」时才走别名，避免误伤写工具的 province/city 等同名字段。
_ARG_ALIASES = {
    "sku": "product_sku",
    "item_sku": "product_sku",
    "product": "product_sku",
    "product_id": "product_sku",
    "model": "product_sku",
    "model_name": "product_sku",
    # 检索类工具的自然语言参数名（只在目标工具 schema 里存在 query 时才生效）
    "q": "query",
    "keyword": "query",
    "keywords": "query",
    "question": "query",
    "user_query": "query",
    "text": "query",
    "timeRange": "time_range",
    "area": "region",
    "province": "region",
    "city": "region",
}


def _validate_args(tool: dict, args: dict) -> dict:
    """多余字段拒绝（JSON Schema 强校验的极简版）+ 受控字段别名归一化。"""
    allowed = set(tool["params"].get("properties", {}).keys())
    cleaned: dict = {}
    for k, v in (args or {}).items():
        target = k if k in allowed else _ARG_ALIASES.get(k)
        if target in allowed and target not in cleaned:
            cleaned[target] = v
    return cleaned


# 真实 LLM 常见的「臆造工具名」→ 平台真实工具名。
# 只在目标工具确实出现在当前 Agent 的可用清单里时才生效，避免跨域误映射。
_ALIASES: dict[str, str] = {
    # 知识库 / 商品资料检索类 → 真正的 RAG 工具（召回 + Reranker 精排）
    "search_knowledge_base": "search_products",
    "search_kb": "search_products",
    "knowledge_search": "search_products",
    "knowledge_base": "search_products",
    "rag_search": "search_products",
    "semantic_search": "search_products",
    "product_recommend": "search_products",
    "search_product": "search_products",
    "product_search": "search_products",
    "get_product_info": "query_spec",
    "get_spec": "query_spec",
    "query_product": "query_spec",
    "check_compatibility": "query_spec",
    # 词序倒置 / 动词替换
    "logistics_query": "query_logistics",
    "order_query": "query_order",
    "price_query": "query_price",
    "stock_query": "query_stock",
    "get_order": "query_order",
    "search_order": "query_order",
    "track_order": "query_logistics",
    "check_stock": "query_stock",
    "get_price": "query_price",
    "query_tracking": "query_logistics",
    # 联网搜索类别名 → 百炼 EnhancedSearch MCP 工具
    "online_search": "web_search",
    "internet_search": "web_search",
    "network_search": "web_search",
    "web_search_pro": "web_search",
    "enhanced_search": "web_search",
    "websearch": "web_search",
}


def resolve_tool_name(name: str, allowed) -> str | None:
    """把模型给出的工具名解析成 allowed 里的合法名字；解析不出返回 None。

    四级：精确 → 已知别名 → 规范化/双向包含 → difflib 近似。
    这是「模型输出不可信」的代码级兜底：提示词只是倾向，这里才是硬约束。
    """
    keys = list(allowed)
    if not name or not keys:
        return None
    if name in keys:
        return name

    alias = _ALIASES.get(name.lower())
    if alias and alias in keys:
        return alias

    norm = name.replace("_", "").replace("-", "").lower()
    for k in keys:
        if k.replace("_", "").replace("-", "").lower() == norm:
            return k
    for k in keys:
        kk = k.replace("_", "").replace("-", "").lower()
        if norm in kk or kk in norm:
            return k
    close = difflib.get_close_matches(name, keys, n=1, cutoff=0.6)
    return close[0] if close else None
