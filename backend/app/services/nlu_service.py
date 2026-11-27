"""
第一层语义理解（NLU / Query Understanding）：改写 + 意图识别 + 槽位抽取 + 多意图拆解。

这是「实用级多 Agent」和「关键词机器人」的分界线：
  用户原句 → 规范化改写 → 结构化意图/槽位/子意图队列 → 再交给 Supervisor 做多智能体分配。

设计原则（面试里值得讲清楚的几条）：
1. 一次模型调用产出全部结构化结果（rewrite + intent + slots + sub_intents），
   而不是「改写一次、分类一次、抽槽一次」，低额度端点下这是成本问题不是洁癖。
2. 模型输出一律不可信：意图白名单、SKU 必须真实出现在原句或记忆上下文里、
   订单号/手机号/运单号等敏感槽位直接丢弃，改写文本二次过注入过滤。
   硬约束在代码，提示词只是倾向。
3. 失败闭环：LLM 限流/超时/解析失败 → 降级为「原句 + 确定性规则意图」，
   链路继续跑，绝不因为理解层挂掉而给用户空气泡。
4. NLU_MODE=auto 时按需调用：强业务关键词且没有指代/多意图/模糊表达的直白句子
   直接走规则短路，省一次模型调用；只有真正需要「理解」的句子才花钱。
5. 理解层是 LangGraph 里的一个节点，不是图外中间件 —— 可观测、可审计、可降级、
   结果能随 state 传给下游每个 Agent。

本模块被两处复用：agents/nodes/understand.py（图内）与 routers/nlu.py（调试实验台）。
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from app.agents.llm import get_llm, rule_based_intent
from app.config import settings
from app.core.errors import AppError, ErrorCode
from app.core.security import strip_injection
from app.services import memory_service as mem

logger = logging.getLogger(__name__)

VALID_INTENTS = ("presale", "insale", "aftersale", "chitchat", "handoff")
AGENT_LABEL = {
    "presale": "售前 Agent", "insale": "售中 Agent", "aftersale": "售后 Agent",
    "chitchat": "闲聊 Agent", "handoff": "人工坐席",
}

_SKU_RE = re.compile(r"[A-Z]{2,5}-[A-Z0-9-]+")
_SLOT_KEYS = ("product_sku", "os", "topic", "aftersale_type", "region", "query")
# 敏感字段：即使模型返回了也一律丢弃，订单寻址只由服务端会话绑定推导。
_FORBIDDEN_SLOTS = ("order_no", "order_id", "phone", "mobile", "tracking_no", "amount", "price", "address")

# 需要「理解」而不是「匹配」的信号
_REFERRING = ("它", "这款", "那款", "这台", "那台", "刚才", "上次", "上一轮",
             "前面说", "同一个", "再查", "也查", "那家", "这个", "那个")
_MULTI = ("顺便", "另外", "还有", "以及", "同时", "再帮我", "然后再", "接着", "，再", ";", "；")
_FUZZY = ("咋", "啥", "有没有", "能不能", "行不行", "好不好", "弄", "搞", "咋样",
          "适合", "想要", "挑", "选", "推荐", "对比", "区别")


def _clean_text(value: Any, fallback: str, limit: int = 220) -> str:
    """模型回传文本统一清洗：去思考块/代码围栏/控制字符，截断，空则回落原句。"""
    t = str(value or "").strip()
    t = re.sub(r"<think>[\s\S]*?</think>", "", t, flags=re.I)
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    t = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", t)
    t = " ".join(t.split())
    if not t:
        return fallback[:limit]
    return t[:limit]


def _needs_llm(text: str, mode: str, force: bool) -> bool:
    """auto 模式：只有真正需要语义理解的句子才付出一次模型调用。

    off 是运维级硬开关（断网 / 额度耗尽 / 压测），优先级高于 force。
    """
    if mode == "off":
        return False
    if force:
        return True
    if mode == "always":
        return True
    # auto
    if any(w in text for w in _REFERRING) or any(w in text for w in _MULTI):
        return True
    rule = rule_based_intent(text)
    if rule:                # 直白业务句：规则护栏已经够准，省一次调用
        return False
    if len(text) >= 8 and any(w in text for w in _FUZZY):
        return True         # 模糊业务表述（如「想找个适合大手的无线鼠」）
    return False            # 「你好」「在吗」这类短闲聊直接 chitchat


def _context_sku(context: str) -> str | None:
    """指代消解的规则降级，按「离当前对话最近」的优先级取 SKU。

    最近对话 > 近期网站行为 > 常看商品。不能只取「常看商品」第一个，
    否则用户上一轮明明在聊 KB-PRO87，一句「它呢」会被错误绑定到更早浏览过的鼠标。
    """
    for label in ("最近对话", "近期网站行为", "常看商品"):
        m = re.search(re.escape(label) + r"[：:]([^\n]*)", context or "")
        if not m:
            continue
        skus = _SKU_RE.findall(m.group(1))
        if not skus:
            continue
        # 行为流是「旧 → 新」拼接的，取最后一个即最近一次；对话取第一个即本轮上文
        return skus[-1] if label == "近期网站行为" else skus[0]
    return None


def _split_by_connector(text: str) -> list[str]:
    """规则版多意图切分（Mock / 降级用），按连接词断开。"""
    parts = re.split(r"顺便|另外|还有就是|还有|以及|同时|然后再|接着|；|;", text)
    return [p.strip("，, 。") for p in parts if p and len(p.strip("，, 。")) >= 2]


def sanitize_nlu(data: Any, raw_text: str, context: str = "", max_sub: int = 3) -> dict | None:
    """模型输出 → 可信结构。返回 None 表示完全不可用，交给规则降级。

    这是理解层的「硬约束层」：白名单 + 事实性校验 + 敏感字段剔除 + 注入过滤。
    """
    if not isinstance(data, dict):
        return None

    intent = str(data.get("intent") or "").strip().lower()
    if intent not in VALID_INTENTS:
        return None

    rewritten = _clean_text(data.get("rewritten"), raw_text)
    rewritten, injected = strip_injection(rewritten)
    if injected or not rewritten.strip():
        rewritten = raw_text

    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence") or 0.0)))
    except (TypeError, ValueError):
        confidence = 0.0

    raw_slots = data.get("slots") if isinstance(data.get("slots"), dict) else {}
    slots: dict[str, str] = {}
    for k in _SLOT_KEYS:
        v = raw_slots.get(k)
        if v is None:
            continue
        v = str(v).strip()[:60]
        if not v:
            continue
        if k == "product_sku":
            v = v.upper()
            if not _SKU_RE.fullmatch(v):
                continue
            # 关键：不得凭空生成 SKU。必须能在用户原话或系统记录的记忆中找到。
            if v not in raw_text.upper() and v not in (context or "").upper():
                logger.warning("[NLU] 丢弃模型臆造的 SKU %r", v)
                continue
        slots[k] = v
    # 显式剔除敏感字段（哪怕混在 slots 里带进来）
    for k in _FORBIDDEN_SLOTS:
        slots.pop(k, None)

    subs: list[dict] = []
    raw_subs = data.get("sub_intents") if isinstance(data.get("sub_intents"), list) else []
    for item in raw_subs[:max_sub]:
        if not isinstance(item, dict):
            continue
        si = str(item.get("intent") or "").strip().lower()
        if si not in VALID_INTENTS:
            continue
        goal = _clean_text(item.get("goal"), "", 100)
        if not goal:
            continue
        sub: dict[str, str] = {"intent": si, "goal": goal}
        sr = _clean_text(item.get("rewritten"), "", 160)
        if sr:
            sub["rewritten"] = sr
        subs.append(sub)

    return {
        "rewritten": rewritten,
        "intent": intent,
        "confidence": round(confidence, 2),
        "slots": slots,
        "sub_intents": subs,
        "needs_clarify": bool(data.get("needs_clarify")),
        "reason": _clean_text(data.get("reason"), "", 120),
    }


def rule_nlu(text: str, context: str = "", source: str = "rule") -> dict:
    """确定性降级：不花模型调用，也要给出结构一致的理解结果。"""
    intent = rule_based_intent(text) or "chitchat"
    sku_match = _SKU_RE.search(text)
    sku = sku_match.group(0) if sku_match else ""
    if not sku and any(w in text for w in _REFERRING):
        sku = _context_sku(context) or ""
    slots: dict[str, str] = {}
    if sku:
        slots["product_sku"] = sku

    parts = _split_by_connector(text)
    subs: list[dict] = []
    if len(parts) > 1:
        for p in parts[1:][: max(0, int(settings.NLU_MAX_SUB_INTENTS))]:
            pi = rule_based_intent(p) or intent
            subs.append({"intent": pi, "goal": p, "rewritten": p})

    return {
        "rewritten": text,
        "intent": rule_based_intent(parts[0]) or intent if parts else intent,
        "confidence": 0.62 if source == "rule" else 0.35,
        "slots": slots,
        "sub_intents": subs,
        "needs_clarify": False,
        "reason": "确定性规则护栏" if source == "rule" else "模型不可用，已降级为规则理解",
    }


def apply_rule_guard(nlu: dict, text: str) -> dict:
    """把线上 analyze_input 使用的确定性意图护栏抽成共用函数。

    - 单意图句：规则命中且与模型结论冲突时，代码覆盖模型并留审计线索。
    - 多意图句：主意图先由 NLU 判定，Supervisor 在派发每个子意图时再逐段护栏；
      因此这里用 _MULTI 判断，避免把「顺便/另外」句的主诉求被单个关键词粗暴覆盖。
    """
    guard = rule_based_intent(text)
    if guard and guard != nlu.get("intent") and not any(w in text for w in _MULTI):
        nlu["reason"] = (nlu.get("reason") or "") + f"；确定性护栏覆盖 {nlu.get('intent')}→{guard}"
        nlu["intent"] = guard
        nlu["confidence"] = max(float(nlu.get("confidence") or 0), 0.8)
    return nlu


def _to_dicts(history: Any) -> list[dict]:
    """LangChain 消息对象 / dict 都转成 OpenAI role 格式。"""
    out: list[dict] = []
    role = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}
    for m in history or []:
        if isinstance(m, dict):
            out.append({"role": m.get("role", "user"), "content": str(m.get("content", "") or "")[:300]})
        else:
            out.append({"role": role.get(getattr(m, "type", "human"), "user"),
                        "content": str(getattr(m, "content", "") or "")[:300]})
    return out[-4:]


async def analyze_input(
    db,
    text: str,
    *,
    buyer_id: str | None = None,
    session_id: str | None = None,
    context: str | None = None,
    history: Any = None,
    force: bool = False,
    injection_detected: bool | None = None,
) -> dict:
    """理解层唯一入口。图内节点与 /api/nlu/analyze 共用，保证「调试所见即线上所跑」。

    返回：{ok, input, nlu, source, mode, elapsed_ms, context_chars, error_code, injected}
    source: llm（真实模型）| rule（规则短路）| fallback（模型失败降级）
    """
    t0 = time.perf_counter()
    clean, stripped = strip_injection(text or "")
    # chat 链路在入模前已经剥离过一次注入，通过显式参数把标记带进来，
    # 保证「实验台看到的结论」与「客服链路实际执行的结论」完全一致。
    injected = bool(stripped) or bool(injection_detected)
    if not clean.strip():
        clean = "你好"

    if context is None:
        if buyer_id:
            await mem.ensure_profile(db, buyer_id)
            await db.commit()          # 短事务：画像创建立即提交，不让写事务跨 LLM 调用
            context = await mem.build_context(db, buyer_id)
        else:
            context = ""
    if history is None and buyer_id:
        history = await mem.build_history(db, buyer_id, limit=4)

    mode = str(settings.NLU_MODE or "auto").lower()
    if mode not in ("auto", "always", "off"):
        mode = "auto"
    use_llm = _needs_llm(clean, mode, force)
    if injected:
        use_llm = False   # 越权指令不值得花一次模型调用，直接收敛转人工

    nlu: dict
    source = "rule"
    error_code: str | None = None
    if use_llm:
        try:
            raw = await get_llm().analyze(clean, _to_dicts(history), context)
            parsed = sanitize_nlu(raw, clean, context, int(settings.NLU_MAX_SUB_INTENTS))
            if parsed is None:
                raise AppError(ErrorCode.LLM_PARSE, detail="NLU 返回结构不合法")
            nlu, source = parsed, "llm"
        except AppError as e:
            error_code = e.code.value
            logger.warning("[NLU] 模型理解失败 %s，降级为规则", error_code)
            nlu = rule_nlu(clean, context, "fallback")
            source = "fallback"
        except Exception as e:  # noqa: BLE001 — 理解层任何异常都不能冲出到客服链路
            logger.warning("[NLU] 未预期异常，降级为规则：%s", e)
            nlu = rule_nlu(clean, context, "fallback")
            source = "fallback"
    else:
        nlu = rule_nlu(clean, context, "rule")

    # 最后一道护栏：命中强业务关键词时由代码覆盖模型（与线上 Supervisor 共用同一规则）。
    nlu = apply_rule_guard(nlu, clean)

    # 安全收敛放在【共用层】：越权/注入指令一律转人工，队列锁住，
    # 不允许后续的业务护栏把它覆盖回售后，也不允许继续派发任何工具。
    applied_guard: str | None = None
    if injected:
        applied_guard = "injection"
        nlu.update({
            "intent": "handoff", "confidence": 1.0, "slots": {},
            "sub_intents": [], "needs_clarify": False,
            "reason": "检测到越权/注入指令，已剥离并强制转人工，不派发任何工具",
        })
        queue: list[dict] = [{"intent": "handoff", "goal": clean, "rewritten": clean,
                              "primary": True, "locked": True}]
    else:
        # 主意图永远排在多意图队列第一位，Supervisor 只需 pop。
        queue = [{"intent": nlu["intent"], "goal": nlu["rewritten"],
                  "rewritten": nlu["rewritten"], "primary": True}]
        for sub in nlu.get("sub_intents", [])[: int(settings.NLU_MAX_SUB_INTENTS)]:
            if sub["intent"] == nlu["intent"] and sub.get("goal", "") == nlu["rewritten"]:
                continue
            queue.append({"intent": sub["intent"], "goal": sub["goal"],
                          "rewritten": sub.get("rewritten") or sub["goal"], "primary": False})

    return {
        "ok": True,
        "input": clean,
        "nlu": nlu,
        "source": source,
        "mode": mode,
        "llm_called": use_llm,
        "error_code": error_code,
        "injected": injected,
        "guard": applied_guard,
        "context_chars": len(context or ""),
        "history_turns": len(_to_dicts(history)),
        "queue": queue,
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
    }
