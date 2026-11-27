"""Supervisor 节点：多智能体分配（唯一职责，不做回答、不调工具）。

新链路：understand 产出 intent_queue（主意图 + 子意图），Supervisor 每轮 pop 一个，
按 intent 条件路由到对应 Agent；collect 判断要不要回来再派发下一个。

「提示词只是倾向，硬约束在代码」在这里有两层：
1. 每个派发段都用 rule_based_intent 对模型结论做确定性护栏；
2. 强业务关键词命中的直白句子在 understand 里就短路掉了 LLM，省一次模型调用。
模型分类不可靠时（本地 7B 小模型尤其明显），这条护栏是可用性的分界线。
"""
from __future__ import annotations

from app.agents.llm import get_llm, rule_based_intent
from app.agents.runtime import get_runtime
from app.agents.state import AgentState
from app.core.audit import audit
from app.core.errors import AppError

_VALID = {"presale", "insale", "aftersale", "chitchat", "handoff"}


async def supervisor(state: AgentState) -> AgentState:
    rt = get_runtime()

    # ---- 主路径：消费第一层理解产出的意图队列 ----
    queue = list(state.get("intent_queue") or [])
    if queue:
        item = queue[0]
        state["intent_queue"] = queue[1:]
        intent = str(item.get("intent") or "chitchat")
        if intent not in _VALID:
            intent = "chitchat"
        raw_goal = str(item.get("goal") or "")
        goal = str(item.get("rewritten") or raw_goal or state.get("user_input", ""))

        # 逐段护栏：子意图同样是模型输出，也要过确定性规则。
        # 改写句可能把「没有声音」改掉原词，所以原句与改写句都判一次，任一命中就覆盖。
        # 但「注入检测 → 强制转人工」是安全收敛，不能被业务关键词覆盖回去。
        guard = rule_based_intent(goal) or rule_based_intent(raw_goal)
        if guard and guard != intent and not item.get("locked"):
            intent = guard

        # 跨 Agent 交接：上一段的 Agent 与本段不同，记一条可观测的 transfer。
        # 同域连续子意图（售前→售前）不算交接，避免审计噪声。
        prev = state.get("active_agent")
        business = {"presale", "insale", "aftersale"}
        if prev and prev != intent and (prev in business or intent in business):
            trail = list(state.get("agent_trail") or [])
            trail.append({"seq": len(trail) + 1, "from": prev, "to": intent, "goal": goal[:80]})
            state["agent_trail"] = trail
            try:
                await audit(
                    rt.ctx.db, session_id=rt.ctx.session_id, event_type="agent_transfer",
                    input_data={"from": prev, "to": intent, "goal": goal[:200],
                                "deferred": len(state.get("intent_queue") or [])},
                    decision=f"上下文与状态移交：{prev} → {intent}",
                )
            except Exception:
                pass  # 审计失败不影响路由

        state["intent"] = intent
        state["active_agent"] = intent
        state["current_goal"] = goal
        state["intent_source"] = "nlu_" + (state.get("nlu_source") or "llm")
        return state

    # ---- 兼容路径：图被单独调用 / NLU 未产出队列时，退回规则 + 模型分类 ----
    rt = get_runtime()
    llm = get_llm()
    raw = state.get("user_input", "")
    rule = rule_based_intent(raw)

    if rule:
        state["intent"] = rule
        state["active_agent"] = rule
        state["intent_source"] = "rule"
        return state

    try:
        intent = await llm.classify_intent(raw)
    except AppError as e:
        state["error_code"] = e.code.value
        intent = rule or "chitchat"
        state["intent"] = intent
        state["active_agent"] = intent
        state["intent_source"] = "fallback"
        return state

    if intent not in _VALID:
        intent = "chitchat"
    if rule and rule != intent:
        try:
            await audit(
                rt.ctx.db, session_id=rt.ctx.session_id, event_type="intent_guard",
                input_data={"input": raw, "model_intent": intent},
                decision=f"确定性意图护栏覆盖为 {rule}",
            )
        except Exception:
            pass  # 审计失败不能影响路由
        intent = rule

    state["intent"] = intent
    state["active_agent"] = intent
    state["intent_source"] = "llm"
    return state
