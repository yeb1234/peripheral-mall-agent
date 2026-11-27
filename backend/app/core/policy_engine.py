"""
策略引擎（写操作第二道关 + 政策判定）。

设计铁律（文档原文）：
「能用结构化字段精确匹配的，不要交给向量；能用规则算出来的，不要交给模型。」
「凡是要动钱、动地址、动承诺的，都必须过策略引擎，模型只是发起方，不是决定方。」

这里所有判定都是代码算出来的——模型只能引用结果，不能自己决定。
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass

from sqlalchemy import select

from app.config import settings
from app.db.models import Order, OrderStatus, PolicyRule, Product


@dataclass
class PolicyDecision:
    allowed: bool
    reason: str
    data: dict | None = None


class PolicyEngine:
    def __init__(self, db) -> None:
        self.db = db

    async def _rule(self, kind: str, category: str) -> dict | None:
        # 优先品类专属，回退到 "*" 通用规则
        stmt = (
            select(PolicyRule)
            .where(PolicyRule.kind == kind, PolicyRule.category == category)
            .order_by(PolicyRule.effective_from.desc())
        )
        r = (await self.db.execute(stmt)).scalar_one_or_none()
        if r:
            return json.loads(r.payload)
        stmt = select(PolicyRule).where(PolicyRule.kind == kind, PolicyRule.category == "*")
        r = (await self.db.execute(stmt)).scalar_one_or_none()
        return json.loads(r.payload) if r else None

    # ---------- 售前/售后政策判定（读类依据） ----------

    async def return_policy(self, category: str) -> PolicyDecision:
        p = await self._rule("return", category)
        if not p:
            return PolicyDecision(False, "未配置退换政策", None)
        return PolicyDecision(True, "命中退换政策", p)

    async def warranty_policy(self, category: str) -> PolicyDecision:
        p = await self._rule("warranty", category)
        if not p:
            return PolicyDecision(False, "未配置保修政策", None)
        return PolicyDecision(True, "命中保修政策", p)

    async def shipping_sla(self, region: str) -> PolicyDecision:
        p = await self._rule("shipping", "*")
        if not p:
            return PolicyDecision(False, "未配置时效", None)
        regions = p.get("regions", {})
        days = regions.get(region, regions.get("其他", 3))
        return PolicyDecision(True, "命中时效承诺", {**p, "promise_days": days})

    # ---------- 写操作前置判定（第二道关：策略） ----------

    async def can_change_address(self, order: Order) -> PolicyDecision:
        # 「未发货」即可改（已支付 / 待支付），已发货引导拒收
        if order.status not in (OrderStatus.PAID, OrderStatus.PENDING_PAYMENT):
            return PolicyDecision(False, "已发货订单不可在线改地址，请走拒收流程或转人工", None)
        return PolicyDecision(True, "状态校验通过", None)

    async def can_cancel(self, order: Order) -> PolicyDecision:
        if order.status in (OrderStatus.SHIPPED, OrderStatus.DELIVERED,
                            OrderStatus.DONE, OrderStatus.CANCELLED,
                            OrderStatus.REFUNDED):
            return PolicyDecision(False, f"订单状态为 {order.status.value}，不可取消", None)
        return PolicyDecision(True, "状态校验通过", None)

    async def can_aftersale(self, order: Order, category: str, kind: str = "return") -> PolicyDecision:
        # 退/换走 7 天无理由窗口；寄修走保修期（两者口径不同，不能用同一个窗口卡死）
        if kind == "repair":
            if order.created_at:
                age = (dt.datetime.utcnow() - order.created_at).days
                wp = await self.warranty_policy(category)
                months = int((wp.data or {}).get("months", 12))
                if age > months * 30:
                    return PolicyDecision(False, f"已超出 {months} 个月保修期（已 {age} 天）", None)
        elif order.created_at:
            age = (dt.datetime.utcnow() - order.created_at).days
            rp = await self.return_policy(category)
            window = rp.data.get("window_days", 7) if rp.data else 7
            if age > window:
                return PolicyDecision(False, f"已超过 {window} 天无理由窗口（已 {age} 天）", None)
        if order.status in (OrderStatus.REFUNDED, OrderStatus.REFUNDING):
            return PolicyDecision(False, "订单已在退款流程中", None)
        return PolicyDecision(True, "售后资格校验通过", None)

    async def compensation_limit(self, order: Order) -> float:
        """补偿额度上限：策略算，模型不得自行决定金额。"""
        # 演示规则：补偿上限 = 客单价的 15%，且不超过 100 元
        return min(round(order.total * 0.15, 2), 100.0)

    async def category_of(self, order: Order) -> str:
        if not order.items:
            await self.db.refresh(order, attribute_names=["items"])
        first = order.items[0]
        prod = (await self.db.execute(
            select(Product).where(Product.id == first.product_id)
        )).scalar_one_or_none()
        return prod.category if prod else "*"


async def region_from_address(order: Order) -> str:
    """简单按收货省推导大区，演示用。"""
    mapping = {
        "广东省": "珠三角", "北京市": "京津冀", "上海市": "江浙沪",
        "浙江省": "江浙沪", "江苏省": "江浙沪", "天津市": "京津冀",
    }
    return mapping.get(order.province, "其他")
