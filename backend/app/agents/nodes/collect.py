"""
多意图派发的收口：collect（收集分段结果 / 判断是否继续派发）→ compose（合并回复）。

一句「查下订单到哪了，顺便看看 KB-PRO87 有没有货」会被 understand 拆成两个意图，
由 Supervisor 依次派给售中 Agent、售前 Agent；每段结果进 response_segments，
最后在这里合并成一条回复。这正是「能分析用户语句做出很多细致操作」的编排形态。

安全边界（很重要，面试常问「多意图会不会绕过二次确认」）：
一旦出现确认卡 / 身份核验 / 错误 / 转人工，立即停止继续派发，
剩余意图写进 deferred_intents（再由 chat_service 存进 AgentTask.checkpoint）。
也就是说：写操作永远停在确认卡上，绝不会因为「同一句里还有别的诉求」被连着执行。
"""
from __future__ import annotations

from app.agents.state import AgentState
from app.config import settings


def stop_reason(state: AgentState) -> str | None:
    if state.get("confirmation"):
        return "pending_confirmation"
    if state.get("need_bind"):
        return "need_identity_binding"
    if state.get("error_code"):
        return "error"
    if state.get("handoff") or state.get("active_agent") == "handoff":
        return "handoff"
    return None


async def collect(state: AgentState) -> AgentState:
    seg = (state.get("final_response") or "").strip()
    segs = list(state.get("response_segments") or [])
    if seg:
        segs.append(seg)
    state["response_segments"] = segs
    state["final_response"] = ""       # 交给 compose 统一合并，避免分段互相覆盖

    queue = list(state.get("intent_queue") or [])
    if stop_reason(state) and queue:
        state["deferred_intents"] = queue
        state["intent_queue"] = []
    return state


def route_after_collect(state: AgentState) -> str:
    """条件边：还有待办意图且当前没有阻塞 → 回到 Supervisor 再派发一次。

    队列长度由 NLU_MAX_SUB_INTENTS 限死，循环最多 1+N 次，不可能无限转圈。
    """
    if stop_reason(state):
        return "done"
    queue = state.get("intent_queue") or []
    limit = int(settings.NLU_MAX_SUB_INTENTS) + 1
    if queue and len(state.get("response_segments") or []) < limit:
        return "loop"
    return "done"


async def compose(state: AgentState) -> AgentState:
    segs = [s.strip() for s in (state.get("response_segments") or []) if s and s.strip()]
    if not segs:
        state["final_response"] = "抱歉，本轮未能生成回复，请再试一次。"
        return state

    if len(segs) == 1:
        out = segs[0]
    else:
        labels = "①②③④⑤"
        head = f"我识别到 {len(segs)} 项诉求，按顺序分别处理：\n\n"
        out = head + "\n\n".join(
            f"{labels[i] if i < len(labels) else str(i + 1) + '.'} {s}" for i, s in enumerate(segs)
        )

    deferred = state.get("deferred_intents") or []
    if deferred:
        goals = "、".join(str(d.get("goal", ""))[:24] for d in deferred[:3])
        out += (
            f"\n\n（另有 {len(deferred)} 项诉求已写入任务断点：{goals}。"
            "完成当前确认后说「继续」即可接着办。）"
        )
    state["final_response"] = out
    return state
