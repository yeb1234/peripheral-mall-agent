"""
联网搜索工具：把百炼 EnhancedSearch MCP 暴露成客服可读工具。

分工边界（与本地 RAG 工具互补，不互相替代）：
- search_products / search_manual：本店商品库与说明书，参数 / 价格 / 库存 / 售后以此为准；
- web_search：公开网络的通用概念、行业名词、新品行情、时效资讯；结果须注明来自公开网络，
  不得用联网结果承诺本店价格、库存、优惠或售后。

可靠性：客户端内部已做降级（无 key / 超时 / 鉴权失败 / 解析失败不抛异常），
这里只把结构化结果转成工具层统一的 observation / data 口径。
"""
from __future__ import annotations

from app.agents.tools.base import ToolContext, register_tool
from app.core.llm_registry import get_registry
from app.core.websearch_client import get_websearch


async def _web_search(db, ctx: ToolContext, args: dict, order=None) -> dict:
    query = (args.get("query") or "").strip()
    if not query:
        return {"observation": "请描述一下您想了解的外部信息，例如「磁轴键盘是什么原理」。"}

    # Mock / 离线确定性模式不发起任何外部 HTTP：与 RAG 工具的 offline 口径一致，
    # 保证断网、压测、额度耗尽时整条 Agent 链路仍可跑通。
    if get_registry().active == "mock":
        return {
            "observation": "当前为离线演示模式，未启用联网检索；本店商品信息我可以直接为您查询。",
            "data": {"degraded": True, "code": "WEB_OFFLINE", "results": []},
        }

    category = (args.get("category") or "").strip() or None
    time_range = (args.get("time_range") or args.get("timeRange") or "").strip() or None
    source = (args.get("source") or "").strip().lower() or None
    if source not in (None, "auto", "bailian", "tavily", "both"):
        source = None
    try:
        top_k = int(args.get("top_k") or 0) or None
    except (TypeError, ValueError):
        top_k = None

    res = await get_websearch().search(
        query, category=category, time_range=time_range, top_k=top_k, source=source,
    )
    data = {
        "query": res.get("query"),
        "degraded": res.get("degraded"),
        "code": res.get("code"),
        "detail": res.get("detail"),
        "cached": res.get("cached"),
        "latency_ms": res.get("latency_ms"),
        "mode": res.get("mode"),
        "providers": res.get("providers"),
        "results": res.get("results", []),
    }
    return {"observation": res.get("observation") or "暂未检索到相关公开信息。", "data": data}


register_tool(
    name="web_search",
    description="联网检索公开网络（第二信源）。严格对比/参数核实时，必须先查 P3 内部事实库"
                "（compare_benchmarks / compare_products）与商品库/说明书；只有内部库没有覆盖、"
                "或问题属于外部时效资讯时才用本工具。"
                "本店商品规格/价格/库存/售后仍须优先用商品与说明书工具，不得用联网结果替代；"
                "回复须注明信息来自公开网络，商品参数与价格以本店页面为准",
    tool_type="read",
    params={"properties": {
        "query": {"type": "string", "description": "要联网检索的问题，如 磁轴键盘是什么原理"},
        "category": {"type": "string", "description": "可选：finance/law/medical/internet/tax/news_province/news_center，多个英文逗号分隔；通用场景留空"},
        "time_range": {"type": "string", "description": "可选：OneDay/OneWeek/OneMonth/OneYear/NoLimit，默认 NoLimit"},
        "top_k": {"type": "integer", "description": "可选：返回条目数，默认 5，最大 10"},
        "source": {"type": "string", "description": "可选：auto（默认，中文走百炼、英文走 Tavily）/ bailian / tavily / both；拿不准就不要传"},
    }},
    handler=_web_search, agents=["presale"],
)
