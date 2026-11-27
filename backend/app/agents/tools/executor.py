"""
工具执行器：把「六道关」套在每一个工具调用上。

读类：直通（仅订单类受身份闸约束）。
写类：依次过 ——
  一·身份   ：订单由会话绑定推导，模型只给意图不给寻址（args 已做字段白名单）
  二·策略   ：状态/金额/次数/时间窗口由 PolicyEngine 代码判定
  三·二次确认：写操作先回显「将要改什么」，不执行；用户确认后才落库
  四·幂等   ：业务键 = 会话+动作+目标对象，重复请求只生效一次
  五·事务边界：本地工单先落库、再调外部（模拟）；失败可补偿
  六·审计   ：入参/出参/判定/执行者全留痕，可查可复盘

另外：会话级串行写锁（with_lock）保证同一会话写操作互斥——这是正确性问题，不是性能问题。
"""
from __future__ import annotations

import datetime as dt
import json
import uuid

from sqlalchemy import select

from app.agents.tools.base import TOOLS, ToolContext, ToolResult, _validate_args, resolve_tool_name
from app.core.audit import audit
from app.services import agent_cache
from app.core.idempotency import business_key as _bk, idempotent
from app.core.policy_engine import PolicyDecision, PolicyEngine
from app.core.rate_limit import check_write_rate
from app.core.security import mask_order_no, sanitize_untrusted
from app.core.session_manager import resolve_order
from app.config import settings
from app.db.models import Order, OrderStatus

# 各写工具期望的目标订单状态（用于从绑定订单中推导正确那一单）
_HINT = {
    "change_address": OrderStatus.PAID,
    "cancel_order": OrderStatus.PENDING_PAYMENT,
    "aftersale_accept": OrderStatus.DELIVERED,
    "query_order": None,
    "query_logistics": None,
}

# 返回「外部内容」的工具：知识库原文、联网搜索、远程 P3 跑分 MCP。
# 这些输出要过间接注入隔离；本店 DB 查询（商品/订单/库存）是可信数据，不额外改写。
_EXTERNAL_TOOLS = {
    "search_manual", "search_driver", "web_search",
    "compare_benchmarks", "get_bench_facts", "search_bench_products",
}


def _preview(tool_name: str, args: dict, order: Order) -> str:
    tail = mask_order_no(order.order_no)
    if tool_name == "change_address":
        a = args
        return f"将订单 {tail} 收货地址改为：{a.get('province','')}{a.get('city','')}{a.get('district','')}{a.get('detail','')}"
    if tool_name == "cancel_order":
        return f"将取消订单 {tail}（原因：{args.get('reason','用户主动取消')}），已支付将联动退款"
    if tool_name == "aftersale_accept":
        t = args.get("aftersale_type", "return")
        label = {"return": "退货退款", "exchange": "换货", "repair": "寄修"}.get(t, "售后")
        return f"将为订单 {tail} 发起{label}（原因：{args.get('reason','')}）并生成工单"
    if tool_name == "compensation":
        return f"将为订单 {tail} 发放补偿（金额由策略引擎计算，模型不可指定）"
    return f"将对订单 {tail} 执行 {tool_name}"


async def _policy_check(tool_name: str, db, order: Order, args: dict) -> PolicyDecision:
    eng = PolicyEngine(db)
    if tool_name == "change_address":
        return await eng.can_change_address(order)
    if tool_name == "cancel_order":
        return await eng.can_cancel(order)
    if tool_name == "aftersale_accept":
        cat = await eng.category_of(order)
        return await eng.can_aftersale(order, cat, kind=str(args.get("aftersale_type") or "return"))
    if tool_name == "compensation":
        return PolicyDecision(True, "策略将计算金额")
    return PolicyDecision(True, "ok")


def _resolve_tool(name: str) -> str | None:
    """执行器最后一道兜底：共享 base.resolve_tool_name（别名 + 模糊解析）。"""
    return resolve_tool_name(name, TOOLS.keys())


async def run_tool(tool_name: str, args: dict, ctx: ToolContext) -> ToolResult:
    """决策阶段调用：读类执行，写类生成二次确认（不执行）。"""
    tool = TOOLS.get(tool_name)
    if not tool:
        # 真实 LLM 偶尔会改写工具名，模糊兜底一次
        resolved = _resolve_tool(tool_name)
        if resolved:
            tool_name = resolved
            tool = TOOLS[tool_name]
    if not tool:
        return ToolResult(False, f"未知工具：{tool_name}")
    args = _validate_args(tool, args)

    order = None
    if tool["requires_order"]:
        order = await resolve_order(ctx.db, ctx.session_id, _HINT.get(tool_name),
                                    hint_text=getattr(ctx, "user_text", "") or "")
        if order is None:
            await audit(ctx.db, session_id=ctx.session_id, event_type="tool_call",
                        input_data={"tool": tool_name}, decision="需要身份核验（无绑定订单）")
            return ToolResult(False, "未查询到您绑定的订单。请先告知收货手机后四位完成核验，或登录后自动绑定。", need_bind=True)

    if tool["type"] == "read":
        data = await tool["handler"](ctx.db, ctx, args, order)
        if data.get("need_bind"):
            return ToolResult(False, data["observation"], need_bind=True)
        obs = data["observation"]
        # 间接注入隔离：知识库 / 网页 / 远程 MCP 返回的内容是「资料」，不是「指令」，
        # 命中指令模式时替换片段并保留业务事实（本店 DB 查询不走这一步）。
        if tool["name"] in _EXTERNAL_TOOLS:
            obs, _flagged = sanitize_untrusted(obs, "外部资料")
        return ToolResult(True, obs, data=data.get("data"))

    # ---------------- 写类六道关 ----------------
    # 二·频率闸
    ok, left = await check_write_rate(ctx.session_id)
    if not ok:
        await audit(ctx.db, session_id=ctx.session_id, event_type="tool_call", order_id=order.id,
                    input_data={"tool": tool_name}, decision="频率超限拒绝")
        return ToolResult(False, f"写操作过于频繁（窗口内已达上限），剩余可用 {left} 次，请稍后再试或转人工。")

    # 三·策略闸
    pdec = await _policy_check(tool_name, ctx.db, order, args)
    if not pdec.allowed:
        await audit(ctx.db, session_id=ctx.session_id, event_type="tool_call", order_id=order.id,
                    input_data={"tool": tool_name, "args": args}, decision=f"策略拒绝：{pdec.reason}")
        return ToolResult(False, "抱歉，该操作暂时无法执行：" + pdec.reason)

    # 四·二次确认（先不执行）
    preview = _preview(tool_name, args, order)
    cid = uuid.uuid4().hex
    pending = {
        "confirm_id": cid, "tool": tool_name, "args": args,
        "order_id": order.id, "preview": preview,
        "sensitive": tool["sensitive"],
    }
    await agent_cache.set_raw(f"confirm:{cid}", json.dumps(pending, ensure_ascii=False),
                              ex=settings.CONFIRM_TTL_SECONDS)
    await audit(ctx.db, session_id=ctx.session_id, event_type="tool_call", order_id=order.id,
                input_data={"tool": tool_name, "args": args}, decision="生成二次确认卡")
    return ToolResult(
        False,
        f"⚠️ 这是一笔写操作，需要您二次确认：\n{preview}\n请点击下方确认卡确认或取消。",
        needs_confirm=True,
        confirmation={"confirm_id": cid, "preview": preview, "sensitive": tool["sensitive"]},
    )


async def execute_confirmed(confirm_id: str, approve: bool, ctx: ToolContext) -> dict:
    """确认阶段调用：用户点击确认/取消后真正执行（带会话串行锁 + 幂等）。"""
    pending = await agent_cache.get_json_value(f"confirm:{confirm_id}")
    if not pending:
        return {"ok": False, "observation": "确认卡已过期，请重新发起操作。"}
    await agent_cache.delete(f"confirm:{confirm_id}")

    tool_name = pending["tool"]
    args = pending["args"]
    order = await ctx.db.get(Order, pending["order_id"])
    if not order:
        return {"ok": False, "observation": "订单不存在或已失效。"}

    if not approve:
        await audit(ctx.db, session_id=ctx.session_id, event_type="write_exec", order_id=order.id,
                    input_data={"tool": tool_name}, decision="用户取消")
        return {"ok": True, "observation": "已取消该操作，订单未做任何改动。"}

    tool = TOOLS[tool_name]

    async def _execute_once():
        # 五·事务边界：本地落库（工单/状态）由 handler 完成；外部接口在 handler 内以「模拟」体现
        data = await tool["handler"](ctx.db, ctx, args, order)
        await audit(ctx.db, session_id=ctx.session_id, event_type="write_exec", order_id=order.id,
                    actor="ai", input_data={"tool": tool_name, "args": args},
                    output_data=data, decision="执行成功")
        await ctx.db.commit()
        return data

    # 四·幂等：业务键保证连点只生效一次；会话级串行锁保证互斥
    bk = _bk(ctx.session_id, tool_name, order.id)
    result = await agent_cache.lock(
        ctx.session_id,
        lambda: idempotent(bk, _execute_once),
    )
    obs = result.get("observation", "操作完成。")
    if result.get("handoff"):
        return {"ok": True, "observation": obs, "handoff": True}
    reused = result.get("idempotent_reused")
    if reused:
        obs += "（幂等：本次为重复提交，已返回首次结果，未重复执行）"
    return {"ok": True, "observation": obs, "data": result.get("data")}
