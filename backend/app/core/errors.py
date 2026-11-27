"""
系统级错误码（错误代码控制）。

设计目标（对应需求「给整个系统加入错误代码控制」）：
- 任何失败都能用统一错误码表达，而不是静默崩溃或返回空内容。
- 后端通过 AppError 携带 code / message / detail；SSE 以 error 事件下发，
  普通路由由 main.py 的全局异常处理器统一成 JSON。
- 前端据此渲染带错误码的可恢复提示（而不是卡在空气泡）。

错误码分段：
  SYS   系统级（SYS000 未归类 / SYS001 超时）
  LLM   大模型层（限流 / 超时 / 鉴权 / 网络 / 解析 / 未归类）
  TOOL  工具层（未知工具 / 执行失败）
  SVC   业务/会话层（会话不存在 / 确认卡不存在 / 写频超限）
  RAG   检索层（重排配置/鉴权 / 网络超时限流 / 返回解析 / 参数非法）
  WEB   联网搜索层（百炼 EnhancedSearch MCP 鉴权 / 网络超时 / 返回解析 / 参数非法）
"""
from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    # 系统级
    SYS_UNKNOWN = "SYS000"
    SYS_TIMEOUT = "SYS001"

    # 大模型层
    LLM_RATE_LIMIT = "LLM001"   # 限流（免费 token 高频触发）
    LLM_TIMEOUT = "LLM002"      # 模型响应超时
    LLM_AUTH = "LLM003"         # 密钥无效/失效
    LLM_NETWORK = "LLM004"      # 网络不可达
    LLM_PARSE = "LLM005"        # 返回无法解析
    LLM_UNKNOWN = "LLM006"      # 未归类模型异常
    LLM_QUOTA = "LLM007"        # 周期额度/墨点耗尽（429 quota_exceeded，禁止自动重试）

    # 工具层
    TOOL_UNKNOWN = "TOOL001"     # 未知工具
    TOOL_FAIL = "TOOL002"        # 工具执行失败

    # 会话/业务层
    SVC_SESSION_NOT_FOUND = "SVC001"
    SVC_CONFIRM_NOT_FOUND = "SVC002"
    SVC_RATE_LIMIT = "SVC003"    # 写频率超限
    SVC_IDENTITY = "SVC004"      # 身份与会话不一致（疑似越权）
    SVC_TOKEN = "SVC005"         # 身份签名无效/过期

    # 检索 / 重排层（SiliconFlow Reranker）
    RAG_AUTH = "RAG001"          # 未配置 key / 鉴权失败
    RAG_UNAVAILABLE = "RAG002"   # 网络 / 超时 / 限流
    RAG_PARSE = "RAG003"         # 返回结构无法解析
    RAG_BAD_INPUT = "RAG004"     # query / documents 参数非法

    # 联网搜索层（百炼 EnhancedSearch MCP）
    WEB_AUTH = "WEB001"          # 未配置 key / 鉴权失败
    WEB_UNAVAILABLE = "WEB002"   # 网络 / 超时 / 限流 / 5xx
    WEB_PARSE = "WEB003"         # MCP 返回结构无法解析
    WEB_BAD_INPUT = "WEB004"      # query 为空或非法


# 给用户看的话术（与 code 一一对应，前端可直接展示）
_DEFAULT_MESSAGES: dict[str, str] = {
    ErrorCode.SYS_UNKNOWN: "服务出现未预期异常，已记录，请稍后重试。",
    ErrorCode.SYS_TIMEOUT: "请求处理超时，请稍后重试。",
    ErrorCode.LLM_RATE_LIMIT: "模型服务触发限流，请稍后重试（免费额度 RPM/TPM 较低）。",
    ErrorCode.LLM_TIMEOUT: "模型响应超时，请稍后重试。",
    ErrorCode.LLM_AUTH: "模型密钥无效或已失效，请检查 .env 配置或切换 mock 模式。",
    ErrorCode.LLM_NETWORK: "无法连接模型服务，请检查网络或 base_url。",
    ErrorCode.LLM_PARSE: "模型返回内容无法解析，请重试。",
    ErrorCode.LLM_UNKNOWN: "模型服务返回异常，请稍后重试或切换 mock 模式。",
    ErrorCode.LLM_QUOTA: "模型调用额度已用尽（5 小时 / 7 天限额或墨点不足），系统不会自动重试。请更换 API Key、等待额度重置，或在设置页切到本地模型 / Mock。",
    ErrorCode.TOOL_UNKNOWN: "请求的工具不存在，已转人工处理。",
    ErrorCode.TOOL_FAIL: "工具执行失败，已记录并转人工处理。",
    ErrorCode.SVC_SESSION_NOT_FOUND: "会话不存在或已过期，请重新发起对话。",
    ErrorCode.SVC_CONFIRM_NOT_FOUND: "确认请求不存在或已过期，请重新操作。",
    ErrorCode.SVC_RATE_LIMIT: "操作过于频繁，请稍后再试。",
    ErrorCode.SVC_IDENTITY: "当前会话与所选身份不一致，请重新发起会话或切换回原身份。",
    ErrorCode.SVC_TOKEN: "身份凭证无效或已过期，请重新获取。",
    ErrorCode.RAG_AUTH: "重排服务密钥未配置或无效，已降级为关键词检索。",
    ErrorCode.RAG_UNAVAILABLE: "重排服务暂时不可用，已降级为关键词检索。",
    ErrorCode.RAG_PARSE: "重排服务返回内容无法解析，已降级为关键词检索。",
    ErrorCode.RAG_BAD_INPUT: "检索参数不合法：query 与 documents 均不能为空。",
    ErrorCode.WEB_AUTH: "联网搜索密钥未配置或无效，已跳过联网检索。",
    ErrorCode.WEB_UNAVAILABLE: "联网搜索暂时不可用，已跳过联网检索。",
    ErrorCode.WEB_PARSE: "联网搜索返回内容无法解析，已跳过联网检索。",
    ErrorCode.WEB_BAD_INPUT: "搜索问题不能为空。",
}


class AppError(Exception):
    """统一业务异常：携带错误码、用户话术、可选 detail 与建议 HTTP 状态。"""

    def __init__(
        self,
        code: ErrorCode,
        message: str | None = None,
        *,
        http_status: int = 200,
        detail: str | None = None,
    ) -> None:
        self.code = code
        self.message = message or _DEFAULT_MESSAGES.get(code, "系统异常")
        self.http_status = http_status
        self.detail = detail
        super().__init__(self.message)

    def to_dict(self) -> dict:
        return {"code": self.code.value, "message": self.message, "detail": self.detail}

    def __repr__(self) -> str:  # pragma: no cover - 便于日志排查
        return f"AppError[{self.code.value}] {self.message}"


def rate_limit_kind(exc: Exception) -> str:
    """区分 429 的两种语义（TokenPlan 限速说明）。

    - rate_limit_exceeded / rate_limit_error：RPM/TPM 触顶 → 指数退避可重试
    - quota_exceeded：5 小时 / 7 天墨点额度耗尽 → **禁止自动重试**，重试只会
      继续撞 429 并污染全局限流器的自适应间隔。

    OpenAI SDK 的 body 是 dict；也兼容 response.json() 与纯文本异常消息。
    """
    candidates: list[dict] = []
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        candidates.append(body)
    resp = getattr(exc, "response", None)
    try:
        parsed = resp.json() if resp is not None else None
        if isinstance(parsed, dict):
            candidates.append(parsed)
    except Exception:
        pass
    for c in candidates:
        err = c.get("error") if isinstance(c, dict) else None
        if isinstance(err, dict):
            code = str(err.get("code") or "").lower()
            typ = str(err.get("type") or "").lower()
            if "quota_exceeded" in (code, typ):
                return "quota_exceeded"
            if code == "rate_limit_exceeded" or typ in ("rate_limit_exceeded", "rate_limit_error"):
                return "rate_limit_exceeded"
    if "quota_exceeded" in str(exc).lower():
        return "quota_exceeded"
    return "rate_limit_exceeded"  # 未知 429 按可退避的限流处理


def map_llm_error(exc: Exception) -> AppError:
    """把原始 OpenAI 异常映射到系统错误码（兜底为 LLM_UNKNOWN）。"""
    try:
        import openai
    except Exception:  # openai 未安装时（mock 模式不会走到这）
        return AppError(ErrorCode.LLM_UNKNOWN, detail=f"{type(exc).__name__}: {exc}")

    if isinstance(exc, openai.RateLimitError):
        if rate_limit_kind(exc) == "quota_exceeded":
            return AppError(ErrorCode.LLM_QUOTA, detail=str(exc))
        return AppError(ErrorCode.LLM_RATE_LIMIT, detail=str(exc))
    if isinstance(exc, openai.APITimeoutError):
        return AppError(ErrorCode.LLM_TIMEOUT, detail=str(exc))
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return AppError(ErrorCode.LLM_AUTH, detail=str(exc))
    if isinstance(exc, openai.APIConnectionError):
        return AppError(ErrorCode.LLM_NETWORK, detail=str(exc))
    if isinstance(exc, openai.APIError):
        return AppError(ErrorCode.LLM_UNKNOWN, detail=str(exc))
    return AppError(ErrorCode.LLM_UNKNOWN, detail=f"{type(exc).__name__}: {exc}")


def fallback_response(err: AppError) -> str:
    """生成给用户看的兜底话术（含错误码，便于排障）。"""
    if err.code == ErrorCode.LLM_QUOTA:
        # 额度耗尽不能暗示「重试」，直接给可执行的处置建议
        return f"[{err.code.value}] {err.message}"
    return f"[{err.code.value}] {err.message}（如持续异常，可在 .env 把 LLM_BACKEND 改为 mock 重试。）"
