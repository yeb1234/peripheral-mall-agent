"""
写类工具：这里只放「通过六道关之后的真实落库逻辑」。
身份闸（订单推导）、策略闸、频率闸、幂等、二次确认、审计全部在 executor 里，
工具本身【做不到】越权——这正是设计文档的核心思想：权限在代码里，不在提示词里。

- change_address：改收货地址（仅已支付待发货）
- cancel_order：取消订单（未发货走退款联动）
- aftersale_accept：受理退/换/修，生成工单
- compensation：补偿发放（高敏，金额必须来自策略计算，超限转人工）
"""
from __future__ import annotations

import json
import uuid

from app.agents.tools.base import ToolContext, register_tool
from app.core.policy_engine import PolicyEngine
from app.db.models import OrderStatus, Ticket


async def _change_address(db, ctx: ToolContext, args: dict, order) -> dict:
    old = f"{order.province}{order.city}{order.district}{order.detail}"
    order.province = args["province"]
    order.city = args["city"]
    order.district = args["district"]
    order.detail = args["detail"]
    db.add(order)
    new = f"{order.province}{order.city}{order.district}{order.detail}"
    await db.flush()
    return {"observation": f"收货地址已更新。原：{old}；新：{new}", "data": {"new_address": new}}


async def _cancel_order(db, ctx: ToolContext, args: dict, order) -> dict:
    order.status = OrderStatus.CANCELLED
    db.add(order)
    # 模拟退款联动（已支付才退）
    refund = order.status == OrderStatus.PAID or (order.paid_at is not None)
    await db.flush()
    note = "已发起退款" if refund else "订单未支付，直接取消"
    return {"observation": f"订单 {order.order_no[-4:]} 已取消。{note}。", "data": {"refund": refund}}


async def _aftersale_accept(db, ctx: ToolContext, args: dict, order) -> dict:
    atype = args.get("aftersale_type", "return")
    t = Ticket(session_id=ctx.session_id, order_id=order.id, ticket_type=f"aftersale_{atype}",
               status="pending", payload=json.dumps({"reason": args.get("reason", ""), "order_no_tail": order.order_no[-4:]}, ensure_ascii=False))
    db.add(t)
    await db.flush()
    label = {"return": "退货退款单", "exchange": "换货单", "repair": "寄修单"}.get(atype, "售后单")
    return {"observation": f"已为您生成{label}（工单 {t.id[:8]}），稍后发送退货地址与时效。", "data": {"ticket_id": t.id, "type": atype}}


async def _compensation(db, ctx: ToolContext, args: dict, order) -> dict:
    eng = PolicyEngine(db)
    limit = await eng.compensation_limit(order)
    # 金额一律服务端算，不采信模型给的值
    amount = round(min(float(args.get("amount", limit) or limit), limit), 2)
    if amount <= 0:
        return {"observation": "当前订单不符合自动补偿条件，已为您转人工处理。", "handoff": True}
    t = Ticket(session_id=ctx.session_id, order_id=order.id, ticket_type="compensation",
               status="pending", payload=json.dumps({"comp_type": args.get("comp_type", "coupon"), "amount": amount}, ensure_ascii=False))
    db.add(t)
    await db.flush()
    return {"observation": f"已按策略发放补偿 ¥{amount}（上限 ¥{limit}）。", "data": {"amount": amount, "limit": limit}}


register_tool(
    name="change_address", description="修改已支付待发货订单的收货地址（写·需二次确认）", tool_type="write",
    params={"properties": {"province": {"type": "string"}, "city": {"type": "string"},
                           "district": {"type": "string"}, "detail": {"type": "string"}}},
    handler=_change_address, requires_order=True, agents=["insale"],
)
register_tool(
    name="cancel_order", description="取消未发货订单（写·需二次确认·退款联动）", tool_type="write",
    params={"properties": {"reason": {"type": "string"}}},
    handler=_cancel_order, requires_order=True, agents=["insale"],
)
register_tool(
    name="aftersale_accept", description="受理退/换/修并生成工单（写·需二次确认）", tool_type="write",
    params={"properties": {"aftersale_type": {"type": "string"}, "reason": {"type": "string"}}},
    handler=_aftersale_accept, requires_order=True, agents=["aftersale"],
)
register_tool(
    name="compensation", description="补偿发放（写·高敏·金额由策略计算）", tool_type="write",
    params={"properties": {"comp_type": {"type": "string"}, "reason": {"type": "string"}}},
    handler=_compensation, requires_order=True, agents=["aftersale"], sensitive=True,
)
