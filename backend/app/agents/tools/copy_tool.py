"""商品文案工具：把 P6 的文案能力接进售前 Agent。

链路：售前 Agent → generate_product_copy → P6 /api/copy/generate
      → P6 反查 P4 /api/catalog/{sku}/card → 基于同一份商品事实生成文案。

为什么放在售前：文案属于「种草/导购」，不涉及订单与资金，读类直通即可；
写类工具仍走六道关，这条链路没有写权限。
"""
from __future__ import annotations

from app.agents.tools.base import ToolContext, register_tool
from app.core import p6_client


async def _generate_product_copy(db, ctx: ToolContext, args: dict, order=None) -> dict:
    sku = str(args.get("product_sku") or "").strip().upper()
    if not sku:
        return {"observation": "请提供具体型号（如 KB-PRO87），我再生成文案。"}

    channel = str(args.get("channel") or "detail")
    tone = str(args.get("tone") or "专业")
    res = await p6_client.generate_copy(sku, channel=channel, tone=tone)

    if not res.get("ok"):
        return {
            "observation": f"文案生成服务暂不可用（{res.get('reason', 'unknown')}），"
                           f"我可以先按 {sku} 的公开参数为您讲解卖点。",
            "data": {"sku": sku, "degraded": True, "reason": res.get("reason")},
        }

    copy = res.get("copy") or {}
    lines = [f"【{copy.get('title') or sku}·{channel}】"]
    for i, point in enumerate(copy.get("selling_points") or [], 1):
        lines.append(f"{i}. {point}")
    if copy.get("short"):
        lines.append(f"短文案：{copy['short']}")
    if copy.get("oral_script"):
        lines.append(f"口播：{copy['oral_script']}")
    if copy.get("hashtags"):
        lines.append("话题：" + " ".join(f"#{t}" for t in copy["hashtags"]))
    return {"observation": "\n".join(lines), "data": {"sku": sku, "copy": copy, "source": "p6"}}


register_tool(
    name="generate_product_copy",
    description=(
        "调用 P6 商品视频管线生成商品文案：标题、卖点清单、短文案、口播稿、话题标签。"
        "用户要写文案、做详情页/短视频脚本、要卖点提炼时使用。"
    ),
    tool_type="read",
    params={
        "product_sku": {"type": "string", "description": "商品 SKU，如 KB-PRO87"},
        "channel": {"type": "string", "enum": ["detail", "short_video", "social"],
                    "description": "投放渠道，默认 detail（详情页）"},
        "tone": {"type": "string", "description": "语气，如 专业 / 轻松 / 科技感"},
    },
    handler=_generate_product_copy,
    agents=["presale"],
)
