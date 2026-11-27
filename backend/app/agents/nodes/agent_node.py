"""通用子 Agent 节点：调用 LLM 选工具 -> 走 executor（六道关）-> 产出回复/确认卡。"""
from __future__ import annotations

from app.agents.llm import (
    get_llm, rule_select_address, rule_select_aftersale_action, rule_select_cancel,
    rule_select_compare, rule_select_config, rule_select_diagnose, rule_select_driver,
    rule_select_logistics, rule_select_manual_qa, rule_select_policy,
)
from app.agents.prompts import AFTERSALE, INSALE, PRESALE
from app.agents.runtime import get_runtime
from app.agents.state import AgentState, effective_text
from app.agents.tools.product_bench_tool import rule_select_benchmark
from app.agents.tools.base import TOOLS, tools_for_agent
from app.agents.tools.executor import run_tool
from app.core.audit import audit
from app.core.errors import AppError, ErrorCode, fallback_response

_SYSTEM = {"presale": PRESALE, "insale": INSALE, "aftersale": AFTERSALE}


_ROLE = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}


def _history(state: AgentState) -> list[dict]:
    """LangChain 消息 / 普通 dict 都兼容；type 必须映射成 OpenAI 的 role。"""
    out: list[dict] = []
    for m in state.get("messages", []):
        if isinstance(m, dict):
            out.append({"role": m.get("role", "user"), "content": m.get("content", "") or ""})
            continue
        t = getattr(m, "type", "human")
        out.append({"role": _ROLE.get(t, "user"), "content": getattr(m, "content", "") or ""})
    return out


def _fill_slots(tool_name: str, args: dict, state: AgentState) -> dict:
    """把第一层抽取到的【可信】槽位补进工具入参。

    这些值已在 nlu_service.sanitize_nlu 里做过白名单 + 事实性校验（SKU 必须
    真实出现在原话或记忆中），所以这里补参不会引入模型臆造的寻址信息。
    订单号/手机号/运单号从不出现在这里 —— 那由会话绑定推导。
    """
    tool = TOOLS.get(tool_name)
    out = dict(args or {})
    if not tool:
        return out
    props = (tool.get("params") or {}).get("properties", {}) or {}
    slots = (state.get("nlu") or {}).get("slots") or {}
    if "product_sku" in props and not out.get("product_sku") and slots.get("product_sku"):
        out["product_sku"] = slots["product_sku"]
    # 改写后的查询直接喂给 RAG 检索工具，指代消解在这里真正产生效果
    if tool_name == "search_products" and not out.get("query"):
        out["query"] = effective_text(state)
    if tool_name == "web_search" and not out.get("query"):
        out["query"] = effective_text(state)
    return out


def make_agent_node(agent_name: str):
    async def node(state: AgentState) -> AgentState:
        rt = get_runtime()
        ctx = rt.ctx
        llm = get_llm()
        tools = tools_for_agent(agent_name)

        # 选工具这一步也可能触发模型限流/网络异常 —— 必须守卫，否则整条链路崩、前端空气泡
        try:
            # 确定性护栏（按顺序）：使用/排障 → search_manual；下载资料 → search_driver。
            # 两者都放过才交给模型，保证 Mock 与真实 LLM 的意图→工具映射一致。
            decision = rule_select_compare(effective_text(state), tools)
            if decision is None:
                decision = rule_select_address(effective_text(state), tools)
            if decision is None:
                decision = rule_select_aftersale_action(effective_text(state), tools)
            if decision is None:
                decision = rule_select_diagnose(effective_text(state), tools)
            if decision is None:
                decision = rule_select_manual_qa(
                    effective_text(state), tools,
                    judge_text=state.get("user_input") or "",
                )
            if decision is None:
                decision = rule_select_driver(effective_text(state), tools)
            if decision is None:
                decision = rule_select_logistics(effective_text(state), tools)
            if decision is None:
                decision = rule_select_cancel(effective_text(state), tools)
            if decision is None:
                decision = rule_select_config(effective_text(state), tools)
            if decision is None:
                decision = rule_select_policy(effective_text(state), tools)
            if decision is None:
                decision = rule_select_benchmark(effective_text(state), tools)
            if decision is None:
                decision = await llm.select_tool(
                    agent_name, effective_text(state), tools, _history(state),
                    state.get("memory_context", ""),
                )
        except AppError as e:
            await audit(
                ctx.db, session_id=ctx.session_id, event_type="tool_error",
                input_data={"agent": agent_name, "input": state.get("user_input", "")},
                decision=f"模型选工具失败：{e.code.value}",
            )
            state["error"] = f"{e.code.value}: {e.message}"
            state["error_code"] = e.code.value
            state["final_response"] = fallback_response(e)
            state["handoff"] = True
            return state

        if decision.get("action") == "respond":
            state["final_response"] = decision.get("reply", "您好，有什么可以帮您？")
            return state

        tool_name = decision.get("tool")
        args = _fill_slots(str(tool_name or ""), decision.get("args", {}) or {}, state)
        if not tool_name:
            state["final_response"] = "抱歉，我没能完全理解您的具体诉求，能再描述一下吗？"
            return state

        try:
            result = await run_tool(tool_name, args, ctx)
        except Exception as e:  # noqa: BLE001  — 工具执行异常统一兜底，绝不向上冒泡 500
            await audit(
                ctx.db, session_id=ctx.session_id, event_type="tool_error",
                input_data={"tool": tool_name, "args": args},
                decision=f"异常兜底：{type(e).__name__}: {e}",
            )
            state["error"] = f"{type(e).__name__}: {e}"
            state["final_response"] = (
                "抱歉，处理该操作时出现系统异常，已记录并转交人工处理，请稍候。"
            )
            state["handoff"] = True
            return state

        # 最后防线：内部错误（如「未知工具：xxx」）绝不能当成客服话术透给用户。
        # 正常路径已由 llm.select_tool 的工具名白名单硬校验拦住，这里只兜极端情况。
        if not result.ok and "未知工具" in result.observation:
            await audit(
                ctx.db, session_id=ctx.session_id, event_type="tool_error",
                input_data={"tool": tool_name, "args": args},
                decision=f"{ErrorCode.TOOL_UNKNOWN.value} 工具名解析失败，已降级为引导话术",
            )
            state["error_code"] = ErrorCode.TOOL_UNKNOWN.value
            state["final_response"] = (
                "抱歉，这个问题我暂时没能查到资料，能换个说法或告诉我具体商品型号再问一次吗？"
            )
            return state

        if result.need_bind:
            state["need_bind"] = True
            state["final_response"] = result.observation
            return state
        if result.needs_confirm:
            state["confirmation"] = result.confirmation
            state["final_response"] = result.observation
            return state

        state["final_response"] = result.observation
        # 跑分结果是一段事实清单，直接丢给用户太干；再让模型按售前口径转述一次。
        # 转述失败就保留原清单，数据不会丢。
        if tool_name in ("compare_benchmarks", "get_bench_facts") and result.ok:
            try:
                reply = await llm.generate_reply(
                    _SYSTEM[agent_name], [result.observation], state.get("user_input", "")
                )
                if reply:
                    state["final_response"] = reply
            except Exception:  # noqa: BLE001 — 润色失败不影响已拿到的事实
                pass
        if result.data:
            state.setdefault("observations", []).append(result.observation)
        return state

    return node
