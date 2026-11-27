"""
记忆系统服务：把「同一个用户」在网站与客服之间串起来。

分层（与项目原有的短期会话状态互补，不替代）：
  1. 长期记忆  UserProfile  —— 习惯/偏好/统计，跨会话、跨设备、跨渠道
  2. 行为流    UserEvent    —— 只追加，浏览/加购/下单/抢购/客服，可解释推荐依据
  3. 任务断点  AgentTask    —— 一个诉求一个任务；确认卡挂起 = checkpoint，可续点
  4. 对话记忆  Message      —— 复用原有表，按 buyer_id（而非 session_id）取最近对话

所有函数都 **不 commit**：由调用方在一次短事务里统一提交，避免写事务跨越
LLM 调用造成 SQLite 锁等待（项目里已被真实并发验证过的约束）。
"""
from __future__ import annotations

import datetime as dt
import json
import re
import uuid

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from sqlalchemy import select

from app.db.models import AgentTask, Message, Order, Session, UserEvent, UserProfile

_STAGE_CN = {
    "presale": "售前", "insale": "售中", "aftersale": "售后",
    "chitchat": "闲聊", "handoff": "转人工",
}
_TASK_TTL_HOURS = 24

# 偏好抽取用确定性规则，不额外调 LLM：便宜、可审计、离线可复现
_PREF_RULES: list[tuple[str, str, list[str]]] = [
    ("手型", "大手", ["大手", "手大", "手掌大", "手掌偏大"]),
    ("手型", "中手", ["中手"]),
    ("手型", "小手", ["小手", "手小", "手掌小"]),
    ("连接", "无线", ["无线", "2.4g", "蓝牙"]),
    ("连接", "有线", ["有线"]),
    ("重量", "轻量化", ["轻量", "轻一点", "轻的", "克数低"]),
    ("按键", "静音", ["静音", "安静", "无声"]),
    ("降噪", "需要 ANC", ["anc", "主动降噪", "降噪"]),
    ("轴体", "磁轴", ["磁轴"]),
    ("轴体", "机械轴", ["机械轴", "线性轴", "段落轴"]),
    ("配列", "75% / 87 键", ["75%", "87键", "87 键"]),
    ("配列", "全尺寸", ["全尺寸", "104键", "104 键"]),
]


def _now() -> dt.datetime:
    return dt.datetime.utcnow()


def _loads(raw: str | None, default):
    try:
        return json.loads(raw or "") if raw else default
    except (ValueError, TypeError):
        return default


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value else None


# ---------------------------------------------------------------------------
# 长期记忆：画像、偏好、统计
# ---------------------------------------------------------------------------
async def ensure_profile(db, buyer_id: str) -> UserProfile:
    p = (await db.execute(
        select(UserProfile).where(UserProfile.buyer_id == buyer_id)
    )).scalar_one_or_none()
    if not p:
        p = UserProfile(
            buyer_id=buyer_id, display_name=buyer_id,
            prefs_json="{}", stats_json="{}",
        )
        db.add(p)
        await db.flush()
    return p


def extract_prefs(text: str) -> dict:
    """从一句自然语言里抽取稳定偏好（用户习惯）。"""
    low = (text or "").lower()
    found: dict[str, str] = {}
    for key, value, words in _PREF_RULES:
        if any(w in low for w in words):
            found[key] = value
    m = re.search(r"预算\s*[¥￥]?\s*(\d{2,6})", text or "")
    if m:
        found["预算"] = f"≤¥{m.group(1)}"
    return found


async def apply_prefs(db, buyer_id: str, text: str) -> dict:
    """把本轮用户话里表达的习惯并入长期画像（最近表达优先）。"""
    found = extract_prefs(text)
    if not found:
        return {}
    p = await ensure_profile(db, buyer_id)
    merged = {**_loads(p.prefs_json, {}), **found}
    p.prefs_json = _dumps(merged)
    p.updated_at = _now()
    return found


async def set_profile(db, buyer_id: str, *, display_name: str | None = None,
                      prefs: dict | None = None) -> UserProfile:
    p = await ensure_profile(db, buyer_id)
    if display_name is not None:
        p.display_name = display_name[:64]
    if prefs is not None:
        p.prefs_json = _dumps({**_loads(p.prefs_json, {}), **prefs})
    p.updated_at = _now()
    return p


# ---------------------------------------------------------------------------
# 行为流：只追加事件，同时维护轻量统计
# ---------------------------------------------------------------------------
async def record_event(db, buyer_id: str, kind: str, *, sku: str = "",
                       summary: str = "", payload: dict | None = None,
                       session_id: str = "", task_id: str = "") -> UserEvent:
    ev = UserEvent(
        id=uuid.uuid4().hex, buyer_id=buyer_id, kind=kind,
        ref_sku=sku or "", summary=(summary or "")[:240],
        payload_json=_dumps(payload or {}),
        session_id=session_id or "", task_id=task_id or "",
    )
    db.add(ev)
    p = await ensure_profile(db, buyer_id)
    stats = _loads(p.stats_json, {})
    stats["events"] = int(stats.get("events", 0)) + 1
    stats[kind] = int(stats.get(kind, 0)) + 1
    if sku:
        stats["last_sku"] = sku
        if kind == "view_product":
            recent = [x for x in stats.get("recent_skus", []) if x != sku]
            stats["recent_skus"] = ([sku] + recent)[:8]
    stats["last_seen_at"] = _iso(_now())
    p.stats_json = _dumps(stats)
    p.updated_at = _now()
    return ev


async def recent_events(db, buyer_id: str, limit: int = 12) -> list[dict]:
    rows = (await db.execute(
        select(UserEvent).where(UserEvent.buyer_id == buyer_id)
        .order_by(UserEvent.created_at.desc()).limit(limit)
    )).scalars().all()
    return [
        {
            "id": e.id, "kind": e.kind, "sku": e.ref_sku, "summary": e.summary,
            "payload": _loads(e.payload_json, {}), "task_id": e.task_id,
            "created_at": _iso(e.created_at),
        }
        for e in rows
    ]


# ---------------------------------------------------------------------------
# 购买记录 / 对话记忆
# ---------------------------------------------------------------------------
async def purchase_records(db, buyer_id: str, limit: int = 5) -> list[dict]:
    rows = (await db.execute(
        select(Order).where(Order.buyer_id == buyer_id)
        .order_by(Order.created_at.desc()).limit(limit)
    )).scalars().all()
    out = []
    for o in rows:
        d = o.to_public_dict()
        d["order_id"] = o.id
        out.append(d)
    return out


async def purchase_stats(db, buyer_id: str) -> dict:
    rows = (await db.execute(
        select(Order).where(Order.buyer_id == buyer_id)
    )).scalars().all()
    paid = [o for o in rows if o.status.value not in ("cancelled", "refunded")]
    return {
        "orders": len(rows),
        "valid_orders": len(paid),
        "total_spent": round(sum(o.total for o in paid), 2),
        "recent_skus": [i.sku for o in rows[:3] for i in o.items][:6],
    }


async def recent_dialog(db, buyer_id: str, limit: int = 6, session_id: str | None = None) -> list[dict]:
    """取最近对话。传 session_id 时只取本会话：避免新话题被别的会话原文带偏。"""
    stmt = (
        select(Message).join(Session, Message.session_id == Session.id)
        .where(Session.buyer_id == buyer_id)
    )
    if session_id:
        stmt = stmt.where(Message.session_id == session_id)
    rows = (await db.execute(stmt.order_by(Message.created_at.desc()).limit(limit))).scalars().all()
    return [
        {"role": m.role, "content": (m.content or "")[:300],
         "meta": _loads(m.meta, {}), "created_at": _iso(m.created_at)}
        for m in reversed(rows)
    ]


async def build_history(db, buyer_id: str, limit: int = 6, session_id: str | None = None) -> list[BaseMessage]:
    """交给 LangGraph state.messages 的短期对话记忆（LangChain 消息对象）。

    只取本会话原文。跨会话的"最近对话"已经写在 memory snapshot 的上下文里，
    如果再把别的会话原文塞进 messages，模型会顺着上一个话题答非所问
    （实测：问退货却继续讲上一个型号的说明书）。
    """
    msgs = await recent_dialog(db, buyer_id, limit, session_id)
    out: list[BaseMessage] = []
    for m in msgs:
        if m["role"] == "user":
            out.append(HumanMessage(content=m["content"]))
        elif m["role"] == "assistant":
            out.append(AIMessage(content=m["content"][:500]))
    return out


# ---------------------------------------------------------------------------
# 多任务 / 断点续点
# ---------------------------------------------------------------------------
_SKU_RE = re.compile(r"\b([A-Z]{2,4}-[A-Z0-9]{2,}(?:-[A-Z0-9]+)*)\b")


def focus_sku(text: str) -> str:
    m = _SKU_RE.search((text or "").upper())
    return m.group(1) if m else ""


def _topic(text: str, intent: str) -> str:
    pairs = [
        ("物流", ["物流", "快递", "运单", "发货"]),
        ("订单", ["订单", "查单", "待支付"]),
        ("改地址", ["地址", "收货"]),
        ("取消订单", ["取消"]),
        ("退货退款", ["退货", "退款", "退掉"]),
        ("换货维修", ["换货", "换新", "保修", "维修", "寄修"]),
        ("规格兼容", ["规格", "参数", "兼容", "支持", "macos", "windows"]),
        ("商品推荐", ["推荐", "哪款", "适合", "选购", "对比"]),
        ("价格库存", ["多少钱", "价格", "优惠", "库存", "有货"]),
    ]
    low = (text or "").lower()
    for name, words in pairs:
        if any(w in low for w in words):
            return name
    return _STAGE_CN.get(intent, "咨询")


def task_public(t: AgentTask) -> dict:
    return {
        "id": t.id, "buyer_id": t.buyer_id, "session_id": t.session_id,
        "stage": t.stage, "stage_cn": _STAGE_CN.get(t.stage, t.stage),
        "title": t.title, "focus_sku": t.focus_sku, "status": t.status,
        "checkpoint": _loads(t.checkpoint_json, {}), "turns": t.turns,
        "last_message": t.last_message,
        "created_at": _iso(t.created_at), "updated_at": _iso(t.updated_at),
    }


async def open_tasks(db, buyer_id: str, limit: int = 8) -> list[AgentTask]:
    return list((await db.execute(
        select(AgentTask)
        .where(AgentTask.buyer_id == buyer_id, AgentTask.status.in_(["active", "paused"]))
        .order_by(AgentTask.updated_at.desc()).limit(limit)
    )).scalars().all())


async def _park_other_active(db, buyer_id: str, keep_id: str | None = None) -> None:
    """同一时刻只保留一个「进行中」任务，其余挂起，用户随时能回来续点。"""
    for t in await open_tasks(db, buyer_id, limit=20):
        if t.status == "active" and t.id != keep_id:
            t.status = "paused"
            t.updated_at = _now()


async def upsert_task(db, buyer_id: str, session_id: str, intent: str,
                      text: str) -> tuple[AgentTask, bool]:
    """一个诉求 = 一个任务。相同主题续用；换主题则把旧任务挂起留作续点。"""
    sku = focus_sku(text)
    topic = _topic(text, intent)
    title = f"{_STAGE_CN.get(intent, '咨询')}·{topic}" + (f"·{sku}" if sku else "")
    cutoff = _now() - dt.timedelta(hours=_TASK_TTL_HOURS)

    same = (await db.execute(
        select(AgentTask)
        .where(
            AgentTask.buyer_id == buyer_id, AgentTask.stage == intent,
            AgentTask.focus_sku == sku, AgentTask.status.in_(["active", "paused"]),
            AgentTask.updated_at >= cutoff,
        )
        .order_by(AgentTask.updated_at.desc()).limit(1)
    )).scalar_one_or_none()

    if same:
        await _park_other_active(db, buyer_id, keep_id=same.id)
        resumed = same.status == "paused"
        same.status = "active"
        same.turns += 1
        same.last_message = text[:240]
        same.session_id = session_id
        same.title = title
        same.updated_at = _now()
        # 续点时把上一轮挂起的确认卡留在 last_checkpoint，避免重复要求确认
        cp = _loads(same.checkpoint_json, {})
        if cp.get("pending_confirm"):
            cp["last_checkpoint"] = cp.pop("pending_confirm")
            same.checkpoint_json = _dumps(cp)
        return same, resumed

    # 主题切换：旧任务不删除，改成 paused，用户随时可以回来续点
    await _park_other_active(db, buyer_id)
    task = AgentTask(
        id=uuid.uuid4().hex, buyer_id=buyer_id, session_id=session_id,
        stage=intent, title=title, focus_sku=sku, status="active",
        checkpoint_json=_dumps({}), turns=1, last_message=text[:240],
    )
    db.add(task)
    await db.flush()
    return task, False


async def pause_task(db, task_id: str, checkpoint: dict) -> None:
    t = (await db.execute(select(AgentTask).where(AgentTask.id == task_id))).scalar_one_or_none()
    if not t:
        return
    cp = _loads(t.checkpoint_json, {})
    cp["pending_confirm"] = checkpoint
    t.checkpoint_json = _dumps(cp)
    t.status = "paused"
    t.updated_at = _now()


async def finish_task(db, task_id: str, outcome: str) -> None:
    t = (await db.execute(select(AgentTask).where(AgentTask.id == task_id))).scalar_one_or_none()
    if not t:
        return
    cp = _loads(t.checkpoint_json, {})
    cp["pending_confirm"] = {**(cp.pop("pending_confirm", {}) or {}), "outcome": outcome}
    cp["last_checkpoint"] = cp.pop("pending_confirm")
    t.checkpoint_json = _dumps(cp)
    t.status = "done"
    t.updated_at = _now()


async def find_task_by_confirm(db, buyer_id: str, confirm_id: str) -> AgentTask | None:
    for t in await open_tasks(db, buyer_id, limit=30):
        cp = _loads(t.checkpoint_json, {})
        if (cp.get("pending_confirm") or {}).get("confirm_id") == confirm_id:
            return t
    return None


async def resume_task(db, task_id: str) -> dict:
    t = (await db.execute(select(AgentTask).where(AgentTask.id == task_id))).scalar_one_or_none()
    if not t:
        return {"ok": False, "reason": "任务不存在"}
    cp = _loads(t.checkpoint_json, {})
    pending = cp.get("pending_confirm") or {}
    # 第一层理解识别出的「被确认卡挡住的剩余诉求」也随断点一起恢复，
    # 续点话术带上它们，客服就能把上次没办完的几件事接着派发。
    deferred = pending.get("deferred_intents") or []
    if cp.get("pending_confirm"):
        cp["last_checkpoint"] = cp.pop("pending_confirm")
    t.checkpoint_json = _dumps(cp)
    t.status = "active"
    t.updated_at = _now()
    # 续点话术保留原诉求关键词，保证 Supervisor 能路由回正确的 Agent
    extra = ""
    if deferred:
        goals = "、".join(str(d.get("goal", ""))[:26] for d in deferred[:2])
        extra = f"；上次还有没办完的：{goals}"
    return {
        "ok": True, "task": task_public(t),
        "suggested_message": f"继续上次的任务：{t.last_message or t.title}{extra}",
        "deferred_intents": deferred,
    }


# ---------------------------------------------------------------------------
# 上下文拼装：给 LLM 看的事实摘要 + 给前端看的完整快照
# ---------------------------------------------------------------------------
async def build_context(db, buyer_id: str) -> str:
    p = await ensure_profile(db, buyer_id)
    prefs = _loads(p.prefs_json, {})
    stats = _loads(p.stats_json, {})
    buys = await purchase_stats(db, buyer_id)
    orders = await purchase_records(db, buyer_id, limit=3)
    events = await recent_events(db, buyer_id, limit=6)
    dialog = await recent_dialog(db, buyer_id, limit=4)
    tasks = await open_tasks(db, buyer_id, limit=4)

    lines = [f"【用户记忆 · 系统已记录的事实】用户：{buyer_id}"]
    if prefs:
        lines.append("习惯/偏好：" + "、".join(f"{k}={v}" for k, v in prefs.items()))
    else:
        lines.append("习惯/偏好：暂无记录（可主动询问，不要臆测）")
    lines.append(
        f"购买记录：{buys['orders']} 单，累计 ¥{buys['total_spent']:.0f}"
        + (f"；最近一单 {orders[0]['order_no_tail']}（{orders[0]['status']}）" if orders else "")
    )
    if stats.get("recent_skus"):
        lines.append("常看商品：" + "、".join(stats["recent_skus"][:4]))
    if events:
        desc = " → ".join(f"{e['kind']}:{e['sku'] or e['summary'][:12]}" for e in reversed(events[:4]))
        lines.append(f"近期网站行为：{desc}")
    if dialog:
        last = [f"{'用户' if m['role'] == 'user' else '客服'}：{m['content'][:70]}" for m in dialog[-3:]]
        lines.append("最近对话：" + " / ".join(last))
    if tasks:
        lines.append("进行中的任务：" + "；".join(
            f"{t.title}[{'等待确认' if t.status == 'paused' else '进行中'}]" for t in tasks[:3]
        ))
        # 第一层理解识别出的多意图，若被二次确认卡挡住会留在 checkpoint 里；
        # 把它拼进上下文，用户隔天回来问「上次那几件事」时客服才能接得上。
        deferred: list[str] = []
        for t in tasks[:3]:
            cp = _loads(t.checkpoint_json, {})
            for d in ((cp.get("pending_confirm") or {}).get("deferred_intents") or [])[:2]:
                goal = str(d.get("goal", "")).strip()
                if goal:
                    deferred.append(goal[:30])
        if deferred:
            lines.append("上次未办完的诉求：" + "、".join(deferred[:3]))
    lines.append("使用规则：以上均为系统记录，可据其个性化回答；价格/库存/订单状态仍须以工具查证为准，不得据此编造未记录的事实。")
    return "\n".join(lines)


async def snapshot(db, buyer_id: str, *, session_id: str | None = None) -> dict:
    p = await ensure_profile(db, buyer_id)
    tasks = await open_tasks(db, buyer_id, limit=8)
    task_dicts = [task_public(t) for t in tasks]
    paused = [t for t in task_dicts if t["status"] == "paused"]
    return {
        "buyer_id": buyer_id,
        "session_id": session_id,
        "profile": {
            "display_name": p.display_name,
            "prefs": _loads(p.prefs_json, {}),
            "stats": _loads(p.stats_json, {}),
            "created_at": _iso(p.created_at), "updated_at": _iso(p.updated_at),
        },
        "purchase": await purchase_stats(db, buyer_id),
        "orders": await purchase_records(db, buyer_id, limit=5),
        "events": await recent_events(db, buyer_id, limit=12),
        "dialog": await recent_dialog(db, buyer_id, limit=8),
        "tasks": task_dicts,
        "suggested_resume": paused[0] if paused else None,
        "context": await build_context(db, buyer_id),
    }
