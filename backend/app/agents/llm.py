"""
大模型客户端（可插拔 + 运行时切换）。

- MockLLM：内置「确定性决策」，整个多 Agent 编排、工具调用、六道关都能无密钥跑通，
  是给你慢慢学的最佳起点：你能清楚看到「输入 -> 意图 -> 工具 -> 闸 -> 输出」每一步。
- OpenAIClient：兼容任何 OpenAI 协议端点 —— 外部 LLM（DeepSeek/SenseNova）与
  本地 LM Studio / vLLM 都走它，区别只在 base_url / model / key。

提供方由 app/core/llm_registry.py 在运行时决定，设置页切换后无需重启。
两种实现都满足同一个接口，Agent 编排代码（graph / nodes）零改动。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Protocol

from app.agents.prompts import NLU
from app.agents.tools.base import resolve_tool_name
from app.config import settings
from app.core.errors import AppError, ErrorCode, map_llm_error, rate_limit_kind
from app.core.llm_registry import get_registry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 不同端点的返回格式兼容
# ---------------------------------------------------------------------------

def _message_text(resp, provider_id: str | None = None) -> str:
    """统一取回复正文。

    外部端点：message.content。
    LM Studio：thinking 模型（qwen3 等）可能 content 为空、正文在 reasoning_content；
    外部 thinking 模型（deepseek-v4.1-flash-1017 等）content 为空只说明这一轮
    只输出了思考，不能把英文思考过程当成客服答案，交给上层兜底。
    有些模型还会把 <think>...</think> 或 ```json 围栏混进 content。
    """
    try:
        msg = resp.choices[0].message
    except (AttributeError, IndexError, TypeError):
        return ""
    content = getattr(msg, "content", None) or ""
    if not str(content).strip():
        reasoning = getattr(msg, "reasoning_content", None) or ""
        content = reasoning if (provider_id is None or provider_id == "local") else ""
    text = str(content)
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.I).strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_json_loose(text: str) -> dict | None:
    """本地小模型不一定严格输出 JSON：容错剥围栏、截取第一个 { 到最后一个 }。"""
    if not text:
        return None
    t = text.strip()
    try:
        d = json.loads(t)
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    s, e = t.find("{"), t.rfind("}")
    if s >= 0 and e > s:
        try:
            d = json.loads(t[s : e + 1])
            return d if isinstance(d, dict) else None
        except Exception:
            return None
    return None


def _tool_catalog(tools: list[dict]) -> tuple[str, list[str]]:
    """把工具注册表渲染成「模型能看见」的清单，同时返回合法名字白名单。

    之前 select_tool 收到了 tools 却没写进 prompt，真模型不知道有哪些工具，
    于是臆造 search_knowledge_base / logistics_query 之类不存在的名字。
    """
    names = [t["name"] for t in tools]
    if not tools:
        return "（当前 Agent 没有可调用的工具，只能 respond）", names
    lines = []
    for t in tools:
        props = (t.get("params") or {}).get("properties", {}) or {}
        arg_desc = ", ".join(
            f"{k}:{v.get('description', v.get('type', ''))}" if isinstance(v, dict) else str(k)
            for k, v in props.items()
        )
        lines.append(f"- {t['name']}｜{t.get('description', '')}｜参数: {arg_desc or '无'}")
    return "\n".join(lines), names


def _sanitize_selection(data: dict, allowed: list[str]) -> dict:
    """模型输出不可信：工具名做白名单硬校验（提示词只是倾向，这里才是硬约束）。

    非法名先走 resolve_tool_name（别名/近形解析）；仍不合法就强制降级 respond，
    绝不把「未知工具：xxx」这种内部错误透给用户。
    """
    if not isinstance(data, dict):
        return {"action": "respond", "reply": "抱歉，我刚才没能理解，您能再说一遍吗？"}
    action = data.get("action")
    if action == "respond":
        reply = (data.get("reply") or "").strip()
        return {"action": "respond", "reply": reply or "您好，请问有什么可以帮您？"}
    if action != "tool":
        return {"action": "respond", "reply": "您好，请问有什么可以帮您？"}

    raw_name = str(data.get("tool") or "").strip()
    resolved = resolve_tool_name(raw_name, allowed)
    if resolved:
        return {"action": "tool", "tool": resolved, "args": data.get("args") or {}, "reply": ""}

    reply = (data.get("reply") or "").strip()
    return {
        "action": "respond",
        "reply": reply or "抱歉，这个问题我暂时没能查到资料，能换个说法或提供具体商品型号再问我一次吗？",
        "_invalid_tool": raw_name,  # 仅内部记录，返回前 pop 掉
    }


def _tool_example(agent: str) -> str:
    """给本地小模型一个 one-shot 示例，显著提高工具名与参数格式的命中率。"""
    if agent == "presale":
        # 用「自然语言检索」做示例，避免本地小模型把一切都硬套成 query_spec（需要 SKU 才可用）
        return '{"action":"tool","tool":"search_products","args":{"query":"适合大手的无线轻量鼠标"}}'
    if agent == "insale":
        return '{"action":"tool","tool":"query_order","args":{}}'
    if agent == "aftersale":
        return '{"action":"tool","tool":"aftersale_accept","args":{"aftersale_type":"return","reason":"七天无理由"}}'
    return '{"action":"respond","reply":"您好，请问有什么可以帮您？"}'


class LLMClient(Protocol):
    async def analyze(self, text: str, history: list[dict], context: str = "") -> dict: ...
    async def classify_intent(self, text: str) -> str: ...
    async def select_tool(self, agent: str, text: str, tools: list[dict], history: list[dict], context: str = "") -> dict: ...
    async def generate_reply(self, system: str, observations: list[str], user_text: str) -> str: ...


# ---------------------------------------------------------------------------
# Mock：用关键词模拟「意图分类 + 工具选择 + 话术生成」
# ---------------------------------------------------------------------------

_INTENT_RULES = [
    ("handoff", ["人工", "转人工", "投诉", "举报", "找客服", "真人"]),
    ("insale", ["订单", "查单", "物流", "发货", "改地址", "改收货", "取消订单", "催发货", "运单", "地址", "改", "换地址"]),
    ("aftersale", ["退", "换货", "保修", "维修", "寄修", "坏了", "质量问题", "换新", "售后", "七天"]),
    ("presale", ["规格", "参数", "兼容", "支持", "系统", "接口", "macOS", "windows", "linux",
                 "安卓", "ios", "多少钱", "优惠", "券", "库存", "有货", "什么时候到", "几天",
                 "推荐", "区别", "哪个好", "适配", "驱动", "驱动程序", "固件",
                 "说明书", "手册", "用户手册", "资源", "快速入门", "指南",
                 "怎么", "如何", "设置", "配对", "连接", "重置", "快捷键",
                 "排查", "故障", "没反应", "不亮", "失灵", "教程", "使用说明"]),
]


def _match(text: str, words: list[str]) -> bool:
    return any(w in text for w in words)


# 确定性意图护栏：本地小模型分类很不稳定（实测把「查订单物流」判成售后、
# 「KB-PRO87 支持 macOS 吗」判成闲聊）。强业务关键词命中时由代码覆盖模型结果，
# 体现「提示词只是倾向，硬约束在代码」这一设计原则。
_RULE_INTENTS: list[tuple[str, list[str]]] = [
    ("handoff", ["转人工", "人工客服", "找客服", "投诉", "真人", "举报", "12315"]),
    ("aftersale", ["退货", "退款", "退掉", "换货", "换新", "保修", "维修",
                   "寄修", "质量问题", "售后", "七天", "少发", "漏发", "错发",
                   "退货运费", "补偿", "补差价",
                   # 口语化退货表达：「我买的耳机能退吗」不带「退货」二字，靠这些组合词兜住
                   "能退", "可退", "退吗", "想退", "退换", "包退", "无理由", "不想要了",
                   # 故障症状：用户描述现象时就是售后场景，不能因为带「怎么」被售前抢走
                   "没声音", "没有声音", "没声", "无声", "听不到", "不出声",
                   "杂音", "底噪", "电流声", "续航", "耗电", "掉电",
                   "失灵", "双击", "连击", "不亮", "断连", "掉线", "识别不了",
                   "不识别", "不跟手", "卡顿", "连不上", "连不上电脑"]),
    ("insale", ["订单", "查单", "物流", "运单", "发货", "改地址", "改收货",
               "收货地址", "取消订单", "待支付", "快递", "催发货", "物流停滞",
               "发票抬头", "改发票"]),
    ("presale", ["规格", "参数", "兼容", "支持", "适配", "接口", "尺寸", "重量",
                "系统", "macos", "windows", "linux", "多少钱", "价格", "优惠",
                "券", "满减", "分期", "免息", "专票", "发票", "库存", "有货", "现货",
                "推荐", "区别", "哪个好", "什么时候到", "时效", "驱动", "驱动程序",
                "跑分", "geekbench", "3dmark", "time spy", "timespy", "steel nomad",
                "单核", "多核", "安兔兔", "天梯", "帧数", "显卡", "处理器", "对比",
                "固件", "说明书", "手册", "用户手册", "资源", "快速入门", "指南",
                "怎么", "如何", "设置", "配对", "连接", "连不上", "重置",
                "快捷键", "排查", "故障", "没反应", "不亮", "失灵", "教程",
                "使用说明", "说明书里", "手册里",
                # 配置方案类诉求（预算 + 用途 + 一整套），不命中就会被判成闲聊
                "预算", "配一套", "配一整套", "套装", "搭配", "装备", "凑一套", "配置方案",
                # 主机兼容问法（「能接 PS5 吗」不带「兼容」二字）
                "ps5", "xbox", "switch", "主机", "能接", "能用吗", "兼容吗"]),
]


def rule_based_intent(text: str) -> str | None:
    """命中强业务信号 → 返回确定意图；否则 None，交给模型判断。"""
    low = (text or "").lower()
    for intent, words in _RULE_INTENTS:
        if any(w in low for w in words):
            return intent
    return None


class MockLLM:
    async def analyze(self, text: str, history: list[dict], context: str = "") -> dict:
        """离线也能演示「改写 / 指代消解 / 多意图拆解」，零模型调用、结果确定。

        函数内延迟导入避免与 nlu_service 形成模块循环。
        """
        import re as _re

        from app.services import nlu_service as nlu

        out = nlu.rule_nlu(text, context, "rule")
        parts = nlu._split_by_connector(text)
        if len(parts) > 1:
            # 主段只留第一个诉求，子诉求单独派发 —— 与真实 LLM 的输出约定一致
            out["rewritten"] = parts[0]
            for s in out.get("sub_intents", []):
                s["rewritten"] = s.get("rewritten") or s.get("goal")
            out["confidence"] = 0.8
            out["reason"] = "已按连接词把一句话拆成多个可独立派发的诉求"

        if any(w in text for w in ("它", "这款", "那款", "刚才", "上次", "这个", "那个", "这货")):
            sku = nlu._context_sku(context)
            if sku:
                out["slots"]["product_sku"] = sku
                # 真正把指代词「替换掉」，而不是把原句拼在后面，
                # 否则下游检索/工具仍然看到「它」，改写等于没做。
                cleaned = _re.sub(r"它|这款|那款|这个|那个|刚才|上次|这货", "", out["rewritten"])
                cleaned = cleaned.strip("，, 啊呢吗？?。 、")
                out["rewritten"] = f"查询商品 {sku}：{cleaned}" if cleaned else f"查询商品 {sku} 的详细信息"
                out["reason"] = (out.get("reason") or "") + "；指代消解：依据最近对话补全具体型号"
            else:
                out["needs_clarify"] = True
                out["confidence"] = 0.4
                out["reason"] = "出现指代词但记忆里没有可补全的商品，建议澄清"
        return out

    async def classify_intent(self, text: str) -> str:
        for intent, words in _INTENT_RULES:
            if _match(text, words):
                return intent
        return "chitchat"

    async def select_tool(self, agent: str, text: str, tools: list[dict], history: list[dict], context: str = "") -> dict:
        # 确定性护栏优先：① 使用/排障类 → 说明书知识库；② 下载类 → 资源下载。
        # 与真实 LLM 路径共用同一组护栏，保证 Mock 与在线行为一致。
        qa = rule_select_manual_qa(text, tools)
        if qa is not None:
            return qa
        drv = rule_select_driver(text, tools)
        if drv is not None:
            return drv
        names = {t["name"] for t in tools}
        sku = _extract_sku(text)
        # 售前
        if agent == "presale":
            # 没给具体 SKU 的「推荐 / 找一款」类诉求 → 走 RAG 商品知识库（mock 模式纯本地召回）
            if "search_products" in names and not sku and _match(
                text, ["推荐", "哪款", "哪个好", "适合", "找", "区别", "对比", "选购",
                       "挑", "想要", "有没有", "有吗", "选哪"]
            ):
                return {"action": "tool", "tool": "search_products", "args": {"query": text}}
            if "query_spec" in names and _match(text, ["规格", "参数", "兼容", "接口", "支持", "适配", "尺寸", "重量", "系统"]):
                return {"action": "tool", "tool": "query_spec", "args": _sku_args(sku)}
            if "query_stock" in names and _match(text, ["库存", "有货", "还有吗", "现货"]):
                return {"action": "tool", "tool": "query_stock", "args": _sku_args(sku)}
            if "query_price" in names and _match(text, ["多少钱", "优惠", "券", "便宜", "保价", "价格"]):
                return {"action": "tool", "tool": "query_price", "args": _sku_args(sku)}
            if "query_shipping" in names and _match(text, ["什么时候到", "几天", "时效", "发货时间", "多久", "到货"]):
                return {"action": "tool", "tool": "query_shipping", "args": {}}
        # 售中
        if agent == "insale":
            if "cancel_order" in names and _match(text, ["取消", "不要了", "退掉订单"]):
                return {"action": "tool", "tool": "cancel_order", "args": {"reason": "用户主动取消"}}
            if "query_logistics" in names and _match(text, ["物流", "运单", "快递", "到哪", "发货", "轨迹"]):
                return {"action": "tool", "tool": "query_logistics", "args": {}}
            if "change_address" in names and _match(text, ["地址", "改", "换"]):
                return {"action": "tool", "tool": "change_address", "args": _parse_address(text)}
            if "query_order" in names and _match(text, ["订单", "查单", "我的单", "状态", "买的东西"]):
                return {"action": "tool", "tool": "query_order", "args": {}}
        # 售后
        if agent == "aftersale":
            if "aftersale_accept" in names:
                if _match(text, ["退", "退款", "退货"]):
                    return {"action": "tool", "tool": "aftersale_accept",
                            "args": {"aftersale_type": "return", "reason": "七天无理由"}}
                if _match(text, ["换", "换新"]):
                    return {"action": "tool", "tool": "aftersale_accept",
                            "args": {"aftersale_type": "exchange", "reason": "故障换新"}}
                if _match(text, ["修", "保修", "寄修", "维修"]):
                    return {"action": "tool", "tool": "aftersale_accept",
                            "args": {"aftersale_type": "repair", "reason": "在保维修"}}
            if "compensation" in names and _match(text, ["补偿", "赔", "优惠券", "运费"]):
                return {"action": "tool", "tool": "compensation",
                        "args": {"comp_type": "coupon", "reason": "体验补偿"}}
        # 默认：用自然语言应答
        return {"action": "respond", "reply": _fallback_reply(agent, text)}

    async def generate_reply(self, system: str, observations: list[str], user_text: str) -> str:
        if observations:
            return "根据系统查证结果：\n" + "\n".join(f"- {o}" for o in observations)
        if re.fullmatch(r"(你好|您好|hi|hello|在吗|嗨|hey)[!！~。\s]*", (user_text or "").strip(), re.I):
            return "你好！我是 P4 智能客服，可以帮您查规格参数、订单物流，也能处理改地址、取消和售后。想先看点什么？"
        return _fallback_reply("generic", user_text)


def _extract_sku(text: str) -> str | None:
    # 前缀允许 2–6 位（DOCK-TB4 不能被截成 CK-TB4），后缀允许第二段连字符（MS-LARGE-W）
    m = re.search(r"[A-Z][A-Z0-9]{1,5}-[A-Z0-9]{1,8}(?:-[A-Z0-9]{1,8})*", (text or "").upper())
    return m.group(0) if m else None


def _extract_os(text: str) -> str:
    """从自然语言里抽取操作系统（归一为库的 Windows/macOS/Linux）。"""
    low = (text or "").lower()
    if "win" in low:
        return "Windows"
    if "mac" in low or "os x" in low:
        return "macOS"
    if "linux" in low:
        return "Linux"
    return ""


# 资源下载类关键词：驱动 / 说明书 / 资源统一走 search_driver（内部按 kind 分流）。
# NLU 规则与工具选择护栏共用，保证「意图 → 工具」一致。
_RESOURCE_KW = [
    "驱动", "驱动程序", "固件", "firmware", "driver",
    "说明书", "手册", "用户手册", "使用说明", "manual",
    "快速入门", "入门", "指南", "faq", "资源",
]


def _infer_resource_kind(text: str) -> str:
    """从文本判断用户要的是驱动 / 说明书 / 资源（快速入门 / FAQ / 保修卡等）。"""
    if _match(text, ["说明书", "手册", "用户手册", "使用说明", "manual"]):
        return "manual"
    if _match(text, ["快速入门", "入门", "指南", "faq", "资源"]):
        return "resource"
    return "driver"


# 使用类问题 → 说明书知识库问答（search_manual）。
# 与「下载说明书」必须分开：前者要内容，后者要文件。
_USAGE_KW = [
    "怎么", "如何", "怎样", "设置", "配对", "连接", "连不上", "重置", "恢复出厂",
    "调节", "切换", "快捷键", "组合键", "灯光", "dpi", "回报率", "校准", "调试",
    "没反应", "不亮", "失灵", "故障", "排查", "无法识别", "识别不到", "断连",
    "漂移", "没声音", "用不了", "不能用", "教程", "操作步骤", "使用说明",
    "说明书里", "手册里", "文档里", "怎么装", "怎么用",
]

# 明确索取文件 / 强业务域诉求：让位给 search_driver 或原有订单/售后链路
_USAGE_EXCLUDE_KW = [
    "下载", "发我", "给我", "链接", "在哪下", "我要下",
    "退货", "退款", "换货", "换新", "维修", "寄修", "保修", "售后", "投诉", "转人工",
    "订单", "物流", "运单", "发票", "改地址", "取消", "快递",
    "下单", "购买", "付款", "支付", "结账", "购物车",
    "多少钱", "价格", "优惠", "券", "库存", "有货", "推荐", "哪款", "哪个好",
]


def rule_select_manual_qa(text: str, tools: list[dict], judge_text: str | None = None) -> dict | None:
    """确定性护栏：使用/排障类问题走说明书知识库检索，而不是商品推荐或凭记忆作答。

    优先级高于 rule_select_driver（调用方按顺序检查），但遇到「下载说明书」
    或订单/售后/价格等强业务诉求时主动让位，避免把下载、退货、下单误判成使用问答。

    judge_text 传用户原话（判定用），text 传 NLU 改写后的文本（检索用）：
    改写可能抹掉「怎么」这类疑问词，用原话判定更稳，指代消解后的改写更利于召回。
    """
    if "search_manual" not in {t["name"] for t in tools}:
        return None
    low = ((judge_text if judge_text is not None else text) or "").lower()
    if not any(w in low for w in _USAGE_KW):
        return None
    if any(w in low for w in _USAGE_EXCLUDE_KW):
        return None
    args: dict = {"query": text}
    sku = _extract_sku(text)
    if sku:
        args["product_sku"] = sku
    return {"action": "tool", "tool": "search_manual", "args": args}


def rule_select_driver(text: str, tools: list[dict]) -> dict | None:
    """确定性工具护栏：驱动/说明书/资源类诉求无论用 Mock 还是真实 LLM，都走 search_driver。

    返回 select_tool 风格的决策 dict；None 表示不拦截，交给模型选工具。
    这是「提示词只是倾向，硬约束在代码」在工具层的体现，保证演示稳定可用。
    """
    if "search_driver" not in {t["name"] for t in tools}:
        return None
    if not _match(text, _RESOURCE_KW):
        return None
    sku = _extract_sku(text)
    os_name = _extract_os(text)
    kind = _infer_resource_kind(text)
    # 既没给型号也没给系统 → 不硬查整张表，引导补全，避免返回几十行噪声
    if not sku and not os_name and kind == "driver":
        return {"action": "respond",
                "reply": "好的，请告诉我具体型号（如 KB-PRO87）或系统（Windows/macOS/Linux），"
                         "我帮您定位对应的官方驱动和下载地址。"}
    if not sku and kind != "driver":
        return {"action": "respond",
                "reply": "好的，请告诉我具体型号（如 KB-PRO87），我帮您找到对应的说明书或资源下载。"}
    # 型号与系统可同时生效：如「KB-PRO87 的 Windows 驱动」应精确返回那一条
    args: dict = {"kind": kind}
    if sku:
        args["product_sku"] = sku
    if os_name:
        args["os"] = os_name
    return {"action": "tool", "tool": "search_driver", "args": args}


_LOGISTICS_KW = ("物流", "快递", "运单", "到哪了", "到哪儿", "送到哪", "派送", "签收", "顺丰")
_LOGISTICS_EXCLUDE = ("时效", "几天", "多久", "运费", "包邮", "发货时间")


def rule_select_logistics(text: str, tools: list[dict]) -> dict | None:
    """查物流这类诉求直接走 query_logistics。

    真实模型在多轮历史里容易「凭上下文回答」而不调工具（实测说成「AA42 调不到」），
    物流是演示必讲环节，这里用代码兜住，模型只负责组织话术。
    """
    if "query_logistics" not in {t["name"] for t in tools}:
        return None
    if not _match(text or "", _LOGISTICS_KW):
        return None
    if _match(text or "", _LOGISTICS_EXCLUDE):
        return None
    return {"action": "tool", "tool": "query_logistics", "args": {}}


_CANCEL_KW = ("取消订单", "取消我的", "取消待支付", "取消这单", "把单取消", "把订单取消",
              "帮我取消", "取消吧", "不想要了", "不要这单")


def rule_select_cancel(text: str, tools: list[dict]) -> dict | None:
    """取消订单直接走 cancel_order（目标订单由会话绑定推导）。

    模型遇到「取消我的待支付订单」会先去查单、再回一句「没有待支付单」，
    实际 E819 就在绑定里；这类无参数的写操作交给代码更稳。
    """
    if "cancel_order" not in {t["name"] for t in tools}:
        return None
    if not _match(text or "", _CANCEL_KW):
        return None
    return {"action": "tool", "tool": "cancel_order", "args": {}}


_ADDRESS_KW = ("改地址", "修改地址", "换个地址", "换地址", "地址改成", "地址改到",
               "改收货地址", "修改收货地址", "改一下地址")

# 省/市/区三级 + 详细地址。允许省市同名（北京）、允许没有「市」（自治区）。
_ADDR_RE = re.compile(
    r"(?P<prov>[\u4e00-\u9fff]{2,10}?(?:省|自治区|市))"
    r"(?P<city>[\u4e00-\u9fff]{2,8}?(?:市|自治州|地区))?"
    r"(?P<dist>[\u4e00-\u9fff]{2,8}?(?:区|县|旗))"
    r"(?P<detail>[^，。；！？,.;!?]{2,60})"
)


def rule_select_address(text: str, tools: list[dict]) -> dict | None:
    """改地址同样走确定性规则：模型容易回一句「回复确认」而不出确认卡。

    地址解析失败（没说全）时返回 None，交回模型追问缺项；
    能解析出省/区/详细地址就直接构造 change_address，由 executor 出确认卡。
    """
    if "change_address" not in {t["name"] for t in tools}:
        return None
    if not _match(text or "", _ADDRESS_KW):
        return None
    m = _ADDR_RE.search(text or "")
    if not m:
        return None
    prov = (m.group("prov") or "").strip()
    city = (m.group("city") or "").strip() or prov
    dist = (m.group("dist") or "").strip()
    detail = (m.group("detail") or "").strip()
    if not (prov and dist and detail):
        return None
    return {
        "action": "tool", "tool": "change_address",
        "args": {"province": prov, "city": city, "district": dist, "detail": detail},
    }


_COMPARE_KW = ("对比", "比较", "区别", "差在哪", "哪个好", "选哪个", "怎么选")
_CONFIG_KW = ("预算", "配置", "装机", "一套", "搭配", "套装", "配齐", "凑一套")
_DIAG_KW = ("连不上", "断连", "掉线", "没声音", "无声", "杂音", "底噪", "续航",
            "耗电", "按键失灵", "双击", "连击", "灯不亮", "驱动装不上", "识别不了",
            "延迟高", "卡顿", "不跟手")
_POLICY_KW = ("保修多久", "保修期", "保修政策", "质保多久", "能退吗", "可以退吗",
              "退换政策", "几天到", "多久到", "包邮吗", "运费多少")


def _extract_all_skus(text: str) -> list[str]:
    found = re.findall(r"[A-Z][A-Z0-9]{1,5}-[A-Z0-9]{1,8}(?:-[A-Z0-9]{1,8})*", (text or "").upper())
    out: list[str] = []
    for s in found:
        s = s.strip("-")
        if len(s) >= 5 and s not in out:
            out.append(s)
    return out


def rule_select_compare(text: str, tools: list[dict]) -> dict | None:
    """两个以上型号 + 对比意图 → compare_products。"""
    if "compare_products" not in {t["name"] for t in tools}:
        return None
    if not _match(text or "", _COMPARE_KW):
        return None
    skus = _extract_all_skus(text)
    if len(skus) < 2:
        return None
    return {"action": "tool", "tool": "compare_products", "args": {"skus": skus[:4]}}


def rule_select_config(text: str, tools: list[dict]) -> dict | None:
    """「预算多少、配一套」→ recommend_config。没给预算也能出最低可行组合。"""
    if "recommend_config" not in {t["name"] for t in tools}:
        return None
    if not _match(text or "", _CONFIG_KW):
        return None
    m = re.search(r"(\d{3,5})\s*(?:元|块|以内|以下|预算)?", text or "")
    budget = float(m.group(1)) if m else 0.0
    return {"action": "tool", "tool": "recommend_config", "args": {"budget": budget, "usage": text}}


_AFTERSALE_ACTION_KW = ("申请换货", "申请退货", "申请寄修", "我要换货", "我要退货",
                        "帮我换货", "帮我退货", "帮我换", "帮我退", "办退货", "办理退货",
                        "直接换货", "走换货", "走退货")


def rule_select_aftersale_action(text: str, tools: list[dict]) -> dict | None:
    """用户已经把决定下了（「申请换货」「我要退货」）→ 直接进受理，别再只给诊断。"""
    if "aftersale_accept" not in {t["name"] for t in tools}:
        return None
    if not _match(text or "", _AFTERSALE_ACTION_KW):
        return None
    kind = "exchange" if "换" in (text or "") else ("repair" if "修" in (text or "") else "return")
    return {"action": "tool", "tool": "aftersale_accept",
            "args": {"aftersale_type": kind, "reason": text}}


def rule_select_diagnose(text: str, tools: list[dict]) -> dict | None:
    """售后故障描述 + 具体型号 → diagnose_issue（没型号就交给模型追问）。"""
    if "diagnose_issue" not in {t["name"] for t in tools}:
        return None
    if not _match(text or "", _DIAG_KW):
        return None
    # 已经明确要办理（申请换货）就别拦在诊断这一步
    if _match(text or "", _AFTERSALE_ACTION_KW):
        return None
    sku = _extract_sku(text or "")
    if not sku:
        return None
    return {"action": "tool", "tool": "diagnose_issue", "args": {"product_sku": sku, "symptom": text}}


def rule_select_policy(text: str, tools: list[dict]) -> dict | None:
    """政策咨询（保修多久/能退吗/几天到）→ query_policy；办业务（我要退货）不走这条。"""
    if "query_policy" not in {t["name"] for t in tools}:
        return None
    if not _match(text or "", _POLICY_KW):
        return None
    if any(w in (text or "") for w in ("我要退", "申请退", "帮我退", "要换", "申请换", "帮我换")):
        return None
    return {"action": "tool", "tool": "query_policy",
            "args": {"topic": text, "product_sku": _extract_sku(text or "") or ""}}


def _sku_args(sku: str | None) -> dict:
    return {"product_sku": sku} if sku else {}


def _parse_address(text: str) -> dict:
    """极简地址解析（演示用），真实场景由 LLM 抽取结构化字段。"""
    return {
        "province": "广东省", "city": "广州市", "district": "天河区",
        "detail": "演示新地址 1 号 接收大厦 601",
    }


def _fallback_reply(agent: str, text: str) -> str:
    return ("我是 P4 智能客服，可以帮您查规格、查订单、处理改地址/取消/售后等。"
            "为了核实身份，请先告知收货手机后四位，或登录后自动绑定订单。")


# ---------------------------------------------------------------------------
# 真实 LLM：OpenAI 协议（外部端点 + 本地 LM Studio / vLLM 共用）
# ---------------------------------------------------------------------------

class OpenAIClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        provider_id: str = "external",
    ) -> None:
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(
            api_key=api_key or settings.OPENAI_API_KEY or "EMPTY",
            base_url=base_url or settings.OPENAI_BASE_URL,
        )
        self.model = model or settings.OPENAI_MODEL
        self.provider_id = provider_id
        # None=未探测 / True=支持 json_object / False=用提示词约束 + 容错解析
        # LM Studio 只认 json_schema/text，传 json_object 会 400，本地直接跳过试探
        self._json_mode_supported: bool | None = (
            False if provider_id == "local" else None
        )

    async def _create(self, **kwargs):
        """经「限流控制系统」排队后调用模型，并在 429 时放宽重试。

        一个 slot 覆盖本次调用及其所有重试（重试期间持锁，避免别的请求插队撞限流）。
        失败一律抛 AppError（带错误码），由上层（节点 / chat_turn / SSE）统一兜底。
        """
        import openai

        from app.core.llm_rate_limit import get_llm_limiter

        limiter = get_llm_limiter()
        max_retries = int(settings.LLM_MAX_RETRIES)
        attempt = 0

        async with limiter.slot():
            while True:
                t0 = time.perf_counter()
                try:
                    r = await self.client.chat.completions.create(**kwargs)
                    limiter.on_success(time.perf_counter() - t0, had_retries=(attempt > 0))
                    return r
                except openai.RateLimitError as e:
                    # TokenPlan 的 429 有两种语义：RPM/TPM 触顶可退避重试；
                    # quota_exceeded 是 5 小时 / 7 天墨点额度耗尽，重试毫无意义，
                    # 必须立刻失败并告诉用户换 key / 等重置 / 切本地或 Mock。
                    if rate_limit_kind(e) == "quota_exceeded":
                        limiter.on_quota_exceeded()
                        logger.warning("[LLM007] TokenPlan 周期额度耗尽（quota_exceeded），不自动重试")
                        raise AppError(
                            ErrorCode.LLM_QUOTA,
                            detail="quota_exceeded: 5h/7d 额度或墨点不足，未执行重试",
                        )
                    attempt += 1
                    limiter.on_rate_limited()
                    if attempt >= max_retries:
                        raise AppError(
                            ErrorCode.LLM_RATE_LIMIT,
                            detail=f"重试 {attempt} 次仍被限流",
                        )
                    # 1.5s / 3s / 6s / 8s 指数退避 + 抖动，尊重 Retry-After；
                    # 且全局串行锁保证同一时刻只有 1 个请求在飞（限制客户端并发）。
                    await limiter.backoff(attempt, e)
                except openai.APITimeoutError:
                    raise AppError(ErrorCode.LLM_TIMEOUT)
                except (openai.AuthenticationError, openai.PermissionDeniedError):
                    raise AppError(ErrorCode.LLM_AUTH)
                except openai.APIConnectionError:
                    raise AppError(ErrorCode.LLM_NETWORK)
                except openai.APIError as e:
                    # 本地端点（LM Studio）冷启动 / 模型切换时会偶发 400/500，
                    # 这类抖动重试一两次就能自愈；仍失败才抛 LLM006 交给上层兜底。
                    # 注意：400 也可能代表参数不支持，_create_json 会据 detail 降级 json_object。
                    attempt += 1
                    if attempt <= 2:
                        logger.warning(
                            "[LLM] %s 返回 %s，重试 %d/2（模型端点抖动自愈）",
                            self.provider_id, type(e).__name__, attempt,
                        )
                        await asyncio.sleep(0.8 * attempt)
                        continue
                    raise AppError(ErrorCode.LLM_UNKNOWN, detail=str(e))

    async def _create_json(self, **kwargs):
        """JSON 模式自动降级。

        SenseNova / OpenAI 支持 response_format=json_object；
        LM Studio 只认 json_schema 或 text，传 json_object 会 400。
        第一次撞到就记住并去掉该参数，改靠提示词 + _parse_json_loose 容错解析，
        之后不再重复试探（每个端点只交一次学费）。
        """
        rf = kwargs.pop("response_format", {"type": "json_object"})
        if self._json_mode_supported is False:
            return await self._create(**kwargs)
        try:
            r = await self._create(response_format=rf, **kwargs)
            if self._json_mode_supported is None:
                self._json_mode_supported = True
            return r
        except AppError as e:
            detail = (
                f"{getattr(e, 'detail', '') or ''} {getattr(e, 'message', '') or ''}"
            ).lower()
            # openai SDK 的 400 常只给 "Error code: 400"、不含响应体，
            # 所以除了匹配文本，也把「首次探测时的任意模型端 400」当作不支持该参数。
            unsupported = "response_format" in detail or (
                e.code == ErrorCode.LLM_UNKNOWN and "400" in detail
            )
            if self._json_mode_supported is None and unsupported:
                logger.warning(
                    "[LLM] 端点 %s 不支持 json_object，已自动降级为提示词约束", self.provider_id
                )
                self._json_mode_supported = False
                return await self._create(**kwargs)
            raise

    async def analyze(self, text: str, history: list[dict], context: str = "") -> dict:
        """第一层语义理解：一次 JSON 调用产出改写 + 意图 + 槽位 + 多意图拆解。

        与 classify_intent 的区别：后者只给一个标签，这里给的是下游编排器能
        直接消费的结构化任务描述。两处共用同一个限流器与 429 语义处理。
        """
        messages = [{"role": "system", "content": NLU}]
        if context:
            messages.append({
                "role": "system",
                "content": "【用户记忆与网站行为（系统记录，可用于指代消解；不得据此编造未记录的事实）】\n"
                           + context[:1400],
            })
        for h in history[-4:]:
            messages.append({"role": h.get("role", "user"), "content": str(h.get("content", ""))[:300]})
        messages.append({"role": "user", "content": text})

        r = await self._create_json(
            model=self.model, messages=messages,
            response_format={"type": "json_object"}, temperature=0,
        )
        out = _message_text(r, self.provider_id)
        data = _parse_json_loose(out)
        if data is None:
            # 外部 thinking 模型偶发把结构化结果吐在 reasoning_content 而 content 为空。
            # 只在「能解析出 JSON」时救回，绝不把英文思考过程当客服正文。
            try:
                reasoning = getattr(r.choices[0].message, "reasoning_content", "") or ""
            except (AttributeError, IndexError, TypeError):
                reasoning = ""
            data = _parse_json_loose(reasoning)
        if data is None:
            logger.warning("[LLM005] NLU 未返回合法 JSON (provider=%s): %r",
                           self.provider_id, out[:160])
            return {}
        return data

    async def classify_intent(self, text: str) -> str:
        sys = (
            "你是电商客服意图分类器。只输出一个英文单词："
            "presale（售前·规格/兼容/价格/库存）/ insale（售中·订单/物流/改地址/取消）/ "
            "aftersale（售后·退换修/保修）/ handoff（转人工）/ chitchat（闲聊）。\n"
            "示例：\n"
            "用户：KB-PRO87 支持 macOS 吗 → presale\n"
            "用户：查一下我的订单物流 → insale\n"
            "用户：我要退货 → aftersale\n"
            "用户：转人工 → handoff\n"
            "用户：你好 → chitchat\n"
            "只输出标签本身，不要输出解释或标点。"
        )
        r = await self._create(
            model=self.model, messages=[{"role": "system", "content": sys},
                                        {"role": "user", "content": text}],
            temperature=0,
        )
        out = _message_text(r, self.provider_id)
        # 本地小模型爱加解释（"应该是 presale，因为…"），只取合法英文标签
        m = re.search(r"\b(presale|insale|aftersale|handoff|chitchat)\b", out.lower())
        return m.group(1) if m else "chitchat"

    async def select_tool(self, agent: str, text: str, tools: list[dict], history: list[dict], context: str = "") -> dict:
        catalog, allowed = _tool_catalog(tools)
        sys = (
            f"你是「{agent}」客服 Agent。根据用户诉求，从【可用工具清单】里选一个调用，或选 respond 直接回答。\n\n"
            "【可用工具清单】（tool 字段只能填这里列出的 name）\n"
            f"{catalog}\n\n"
            "硬性规则：\n"
            "1. action=\"tool\" 时，tool 必须与清单里的 name 逐字一致。"
            "严禁发明、翻译、改写或合并工具名；清单里没有的能力不能调用。"
            "按需求找/推荐/对比商品时用清单里的检索工具，已知 SKU 查具体参数才用 query_spec。\n"
            "2. 清单里没有匹配能力、或拿不准用哪个时，必须 action=\"respond\"，用 reply 自然简短地回答用户。\n"
            "3. 工具入参中不要包含订单号/手机号/运单号（由系统从会话注入）。\n"
            "4. 只输出 JSON 对象，不要输出任何解释文字或代码块："
            "{\"action\":\"tool\"|\"respond\",\"tool\":\"<name>\",\"args\":{...},\"reply\":\"<text>\"}\n"
            f"示例（照这个格式输出）：{_tool_example(agent)}"
        )
        if context:
            # 长期记忆 + 网站行为 + 进行中任务注入：客服不再是「每轮失忆的问答机」
            sys += (
                "\n\n【用户记忆与网站联动上下文（系统记录，不是用户本轮输入）】\n"
                + context[:1600]
                + "\n可据此个性化回答；价格/库存/订单状态仍须以工具查证为准，不得编造未记录的事实。"
            )
        messages = [{"role": "system", "content": sys}]
        for h in history[-6:]:
            messages.append({"role": h["role"], "content": h["content"]})
        messages.append({"role": "user", "content": text})
        r = await self._create_json(
            model=self.model, messages=messages,
            response_format={"type": "json_object"}, temperature=0.2,
        )
        data = _parse_json_loose(_message_text(r, self.provider_id))
        if data is None:
            logger.warning(
                "[LLM005] 工具选择未返回合法 JSON (provider=%s): %r",
                self.provider_id, _message_text(r, self.provider_id)[:160],
            )
            return {"action": "respond", "reply": "抱歉，我刚才没能理解，您能再说一遍吗？"}
        result = _sanitize_selection(data, allowed)
        invalid = result.pop("_invalid_tool", None)
        if invalid:
            logger.warning(
                "[TOOL001] 模型臆造工具名 %r (agent=%s, allowed=%s)，已降级 respond",
                invalid, agent, allowed,
            )
        return result

    async def generate_reply(self, system: str, observations: list[str], user_text: str) -> str:
        ctx = "系统查证结果：\n" + "\n".join(observations) if observations else "（无工具查证）"
        r = await self._create(
            model=self.model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": f"{user_text}\n\n{ctx}"}],
            temperature=0.4,
        )
        return _message_text(r, self.provider_id) or "抱歉，本轮未能生成回复，请再试一次。"


# ---------------------------------------------------------------------------
# 运行时提供方选择
# ---------------------------------------------------------------------------

_llm_cache: tuple[str, str, LLMClient] | None = None


def _apply_limiter(provider_id: str) -> None:
    """本地 / 离线没有 RPM 额度限制，把全局串行间隔降到 0，避免无谓等待。"""
    from app.core.llm_rate_limit import get_llm_limiter
    base = float(settings.LLM_MIN_INTERVAL) if provider_id == "external" else 0.0
    get_llm_limiter().set_min_interval(base)


def get_llm() -> LLMClient:
    """按注册表当前生效的提供方构造客户端；配置变化自动重建，无需重启。"""
    global _llm_cache
    reg = get_registry()
    pid, cfg = reg.active_config()
    fp = reg.fingerprint(pid)
    if _llm_cache and _llm_cache[0] == pid and _llm_cache[1] == fp:
        return _llm_cache[2]
    _apply_limiter(pid)
    if pid == "mock":
        client: LLMClient = MockLLM()
    else:
        client = OpenAIClient(
            api_key=cfg.get("api_key") or "EMPTY",
            base_url=cfg.get("base_url") or settings.OPENAI_BASE_URL,
            model=cfg.get("model") or settings.OPENAI_MODEL,
            provider_id=pid,
        )
    _llm_cache = (pid, fp, client)
    logger.info("[LLM] 生效提供方 = %s, model = %s", pid, cfg.get("model"))
    return client


def reset_llm() -> None:
    """设置页切换后清空缓存，下一次调用立即用新配置。"""
    global _llm_cache
    _llm_cache = None
