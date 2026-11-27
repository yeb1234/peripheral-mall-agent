"""
全局配置。

设计要点（与《电商客服 Agent · 中小企业加固版设计》对齐）：
- 数据库默认 SQLite（aiosqlite），方便你一台机器直接跑通；生产用 Postgres（docker-compose 已配）。
- 缓存默认 memory，演示「异步 + 高性能」时切到 redis（docker-compose 已配）。
- 大模型默认 mock：整个 Agent 多轮编排、工具调用、六道关都能无密钥跑通；
  想接真模型时把 LLM_BACKEND 改成 openai（兼容 vLLM / 任何 OpenAI 协议端点）。
"""
from __future__ import annotations

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "P4Gear 智能电商中台"
    ENV: str = "dev"

    # ---- 数据库 ----
    # 本地快速体验：sqlite+aiosqlite；生产/演示：postgresql+asyncpg
    DATABASE_URL: str = "sqlite+aiosqlite:///./p4agent.db"

    # ---- 缓存 / 锁 ----
    # memory（单机演示，零依赖）| redis（真正的异步高并发演示）
    CACHE_BACKEND: str = "memory"
    REDIS_URL: str = "redis://localhost:6379/0"

    # ---- 大模型 ----
    # mock（内置确定性决策，无需密钥）| openai（OpenAI 协议，含本地 vLLM）
    # 所有密钥统一从 .env 读取，运行配置（base_url / model）才落 llm_config.json。
    LLM_BACKEND: str = "mock"
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-4o-mini"
    LOCAL_LLM_API_KEY: str = "lm-studio"   # 本地端点占位密钥，不是真凭据

    # ---- 第一层语义理解（NLU：改写 + 意图识别 + 槽位 + 多意图拆解）----
    # auto：直白业务句走确定性规则短路（省一次模型调用），只有指代消解、
    #       多意图、模糊表述才真正调 LLM；always：每轮都调（演示语义理解用）；
    #       off：纯规则（压测 / 断网 / 额度耗尽时仍可跑完整条 Agent 链路）。
    NLU_MODE: str = "auto"
    NLU_MAX_SUB_INTENTS: int = 3     # 单轮最多派发几个子意图，防止一句长文本拖垮一轮对话

    # ---- LLM 限流控制系统 ----
    # 免费/低额度 token 的 RPM/TPM 很紧，靠「全局串行 + 最小间隔 + 自适应退避」把
    # 请求抹平，而不是让多个会话各自撞 429。切到付费/自建端点可把间隔调 0、重试调小。
    LLM_MIN_INTERVAL: float = 1.5      # 两次 LLM 调用之间的最小间隔（秒）
    # 低额度外部端点：重试要有，但不能让用户等 4 分钟才看到 LLM001。
    # 意图分类已由确定性护栏短路（每轮少一次调用），这里把最坏等待压到 ~30s/次。
    # 想在压测时多试几次，可在 .env 单独调大 LLM_MAX_RETRIES / LLM_RETRY_MAX。
    LLM_MAX_RETRIES: int = 5           # 单次调用遇到限流的最大重试次数
    LLM_RETRY_BASE: float = 1.5        # 指数退避基数（秒）
    LLM_RETRY_MAX: float = 8.0         # 单次退避上限（秒）
    LLM_QUEUE_TIMEOUT: float = 30.0    # 排队等待上限（秒），超时返回 LLM001
    LLM_ADAPTIVE: bool = True          # 429 自动拉长间隔、连续成功缓慢回落

    # ---- 存储：Postgres + pgvector ----
    # 向量检索只在 DATABASE_URL 指向 postgres 时启用；SQLite 下整层跳过，服务照常起。
    PGVECTOR_ENABLED: bool = True
    PGVECTOR_DIM: int = 1024            # bge-m3 维度，换模型必须同步改表结构
    STORAGE_DIR: str = "./storage"      # 文档/驱动等文件资产根目录（DB 只存元数据）

    # ---- P6（VidFlow 商品视频管线）联动 ----
    # P4 提供商品卡，P6 产出商品文案；两边都留 REST + MCP 两条入口。
    P6_BASE_URL: str = "http://127.0.0.1:12706"
    P6_TIMEOUT: float = 8.0
    P6_COPY_ENABLED: bool = True

    # ---- RAG / SiliconFlow（硅基流动）----
    # 检索链路：DB 商品文档 → 关键词/BM25 召回 → /v1/rerank 精排 → Top-K 进 Agent observations。
    # 与对话 LLM 完全解耦：key 为空、超时、429 都会降级成召回顺序，绝不阻塞客服。
    SILICONFLOW_API_KEY: str = ""
    SILICONFLOW_BASE_URL: str = "https://api.siliconflow.cn/v1"
    SILICONFLOW_RERANK_MODEL: str = "BAAI/bge-reranker-v2-m3"
    SILICONFLOW_EMBED_MODEL: str = "BAAI/bge-m3"
    RAG_ENABLED: bool = True            # 关掉则纯关键词检索，零外部调用
    RAG_RECALL_K: int = 8              # 一阶段召回候选数（送给 reranker）
    RAG_TOP_K: int = 3                 # 二阶段精排后注入上下文的文档数
    RAG_CACHE_TTL: int = 600           # 同一 query+候选的重排结果缓存（秒）
    RAG_TIMEOUT: float = 12.0          # rerank 单次 HTTP 超时（秒）
    RAG_MAX_RETRIES: int = 2           # 429 / 5xx 额外重试次数

    # ---- 联网搜索（阿里云百炼 EnhancedSearch MCP）----
    # 商品库 / 说明书覆盖不到的外部通用概念、行业名词、新品行情与时效资讯，
    # 经 MCP 联网检索补上下文。与对话 LLM、RAG 解耦：key 为空 / 超时 / 鉴权失败 /
    # 解析失败一律降级跳过，绝不阻塞客服链路。
    WEBSEARCH_ENABLED: bool = True
    WEBSEARCH_API_KEY: str = ""
    WEBSEARCH_MCP_URL: str = "https://dashscope.aliyuncs.com/api/v1/mcps/EnhancedSearch/mcp"
    WEBSEARCH_TOOL: str = "search_pro"
    WEBSEARCH_TIMEOUT: float = 15.0
    WEBSEARCH_MAX_RESULTS: int = 5
    WEBSEARCH_CACHE_TTL: int = 600

    # ---- 联网搜索：多源路由 ----
    # auto：按问题语种选主源（中文→百炼，英文/外文→Tavily），主源结果不足时自动补次源；
    # both：两源并发后合并去重；bailian / tavily：只用单一源（排障、设置页试搜）。
    # 两源都不可用才整体降级，客服对话不会中断。
    WEBSEARCH_SOURCE: str = "auto"        # auto | bailian | tavily | both
    WEBSEARCH_FALLBACK: bool = True       # 主源失败/结果不足时是否补次源
    WEBSEARCH_MIN_RESULTS: int = 3        # 低于该条数视为"结果不足"，触发补源
    WEBSEARCH_RERANK: bool = True         # 合并去重后是否复用 SiliconFlow reranker 精排

    # ---- Tavily：英文 / 海外源 ----
    TAVILY_API_KEY: str = ""
    TAVILY_BASE_URL: str = "https://api.tavily.com"
    TAVILY_SEARCH_DEPTH: str = "advanced"  # basic | advanced
    TAVILY_TOPIC: str = "general"          # general | news
    TAVILY_TIMEOUT: float = 15.0
    TAVILY_MAX_RETRIES: int = 2

    # ---- P3 跑分对比（远程 MCP，Streamable HTTP）----
    # P4 作为 MCP 客户端去调 P3 的 compare_products / get_product_facts。
    # 不可达时工具返回降级提示，不阻塞客服对话。
    # 默认走宿主（P3 MCP 端口 12415）；P4 在 compose 内且同网络时覆盖为
    # P3_MCP_URL=http://p3v2-mcp:8000/mcp，跨 compose 用宿主网关。
    P3_MCP_ENABLED: bool = True
    P3_MCP_URL: str = "http://127.0.0.1:12415/mcp"
    P3_MCP_TIMEOUT: float = 30.0
    # P3 的 run_traces 主键是秒级时间戳+payload 哈希，同秒重复请求会撞键；
    # 同参数 60s 内复用结果（0 = 关闭）。见 docs/V2_REVIEW.md「P3 连续调用」。
    P3_CACHE_TTL: float = 60.0

    # ---- 会话 / 安全 ----
    SESSION_TTL_SECONDS: int = 1800          # 会话超时（同时解绑订单）
    WRITE_RATE_LIMIT: int = 2                # 单会话写操作频率上限
    WRITE_RATE_WINDOW: int = 600             # 窗口（秒）
    IDEMPOTENCY_TTL: int = 86400             # 幂等键存活时间
    MAX_BOUND_ORDERS: int = 5                # 单会话最多绑定订单数（演示买家 5 单，少一单会查不到已发货的 AA42）
    ORDER_BIND_WINDOW_DAYS: int = 90         # 仅绑定近 90 天订单
    CONFIRM_TTL_SECONDS: int = 300           # 二次确认卡有效期

    # ---- 身份签名（用户 ID 不能裸传）----
    # 生产部署必须显式配置 BUYER_TOKEN_SECRET；为空时用「应用名+环境」派生，仅够演示。
    BUYER_TOKEN_SECRET: str = ""
    BUYER_TOKEN_TTL: int = 3600
    REQUIRE_BUYER_TOKEN: bool = False      # True 时 chat 不带签名 token 直接拒绝
    DEMO_BUYERS: str = "buyer_demo"        # dev 模式允许直接签发的演示身份（逗号分隔）

    # ---- 对话入口限流 / 去重 ----
    CHAT_RATE_LIMIT: int = 20              # 单身份每窗口对话次数
    CHAT_RATE_WINDOW: int = 60
    IP_RATE_LIMIT: int = 60                # 单 IP 每窗口对话次数（防换身份绕过）
    DEDUP_WINDOW: int = 120                # 重复/近义问句复用回复的窗口（秒）
    DEDUP_SIM: float = 0.72                 # 3-gram Jaccard 阈值

    # ---- Web ----
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"
    LOG_LEVEL: str = "info"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
