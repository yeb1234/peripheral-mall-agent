# P3 跑分 / 参数 MCP 接入说明

P3（`C:\FinalProjectYeb\P3June\v2`）是 P4 售前 Agent 的**第一信源**：用户问「两张卡差多少 / 哪个值」时，
P4 先通过 MCP 拿 P3 的事实，不靠模型记忆，也不先搜网。公开网络只在 P3 未覆盖时补位（第二信源）。

## 代码位置

| 文件 | 作用 |
|---|---|
| `backend/app/core/p3_client.py` | MCP 协议客户端（call / list_tools / compare / facts / search / ranking），错误归一为 `{"error": ...}` |
| `backend/app/agents/tools/product_bench_tool.py` | 三个跑分业务工具 + 确定性护栏 `rule_select_benchmark()` |
| `backend/app/services/source_router.py` | 信源路由：P3 覆盖判定、缺口列表、`fallback_query` |
| `backend/app/agents/tools/service_tools.py` | `compare_products` 扩展：本店 SKU → 商品库；市场型号 → P3 → 联网 |
| `backend/app/config.py` | `P3_MCP_ENABLED` / `P3_MCP_URL` / `P3_MCP_TIMEOUT` |

Agent 工具名：`compare_benchmarks` / `get_bench_facts` / `search_bench_products`（只注册给 presale）。

## 地址

| 场景 | URL |
|---|---|
| 本机/宿主调试（默认） | `http://127.0.0.1:12415/mcp` |
| 容器同网络（P4 接入 `v2_default`） | `http://p3v2-mcp:8000/mcp` |
| 跨 compose 网络 | 用 `host.docker.internal:12415`，或在 P4 容器里 `docker network connect v2_default` |

## 起一份联调容器

Docker Hub 直连拉不动基础镜像时，复用已有 `p4-agent:local` 镜像并挂载代码：

```powershell
docker run -d --name p4-main-app --network p4-agent_default -p 12417:8000 `
  -v "C:\FinalProjectYeb\P4F Agent电商\backend\app:/app/app" `
  --env-file "C:\FinalProjectYeb\P4F Agent电商\backend\.env" `
  -e DATABASE_URL=postgresql+asyncpg://p4:p4pass@p4-agent-db-1:5432/p4agent `
  -e CACHE_BACKEND=redis -e REDIS_URL=redis://p4-agent-redis-1:6379/0 `
  -e P3_MCP_URL=http://p3v2-mcp:8000/mcp p4-agent:local
docker network connect v2_default p4-main-app
```

宿主 8000 被 p1-slimtrack 占用时，这里用 12417。

## P3 暴露的工具（4 个）

| 工具 | 用途 |
|---|---|
| `compare_products(products, kind?, scene?, metric?)` | 2–8 个 CPU/GPU/SoC 对比，GPU 锁同一 3DMark 场景 |
| `get_product_facts(product_id)` | 单产品事实 + 来源 + 置信度 |
| `search_products(query, kind?, limit?)` | 型号片段 → 稳定 ID，未入库标 `exists=false` |
| `benchmark_ranking(kind, field, limit?)` | 同字段排行，不跨场景混排 |

## 已知坑

1. **mcp 2.x 客户端导入**：`from mcp import ClientSession`。写成 `from mcp.client import ClientSession`
   会 ImportError；工具层降级后只显示「跑分库连不上」，很难定位。
2. 镜像里自带的 `/app/llm_config.json` 会覆盖环境变量：容器启动时先
   `rm -f /app/llm_config.json`，否则 `.env` 里的模型配置不生效；`app` 用户对 `/app` 无写权限，
   需要 `--user root` 才能删。
3. 外部密钥被限流（429）时，前端会看到 `LLM003 密钥无效`；联调可先把运行配置切到
   SiliconFlow（`POST /api/llm/config`，`model=Qwen/Qwen3-8B`）。
4. P3 的 `comparisons` 是结果缓存，抽取规则变了要清表，否则旧结论会一直返回。
5. 小模型选工具不稳，跑分请求必须走 `rule_select_benchmark()`，否则会答成「您好，请问有什么可以帮您」。
6. 外设（键盘/鼠标/耳机）目前没有 P3 domain，传进去会报「无法判断品类」；P4 会按设计
   走第二信源。外设评测入库的字段设计见 `docs/SOURCE-PRIORITY.md` §3。

## 实测

`python backend/scripts/probe_sources.py` → 7/7 PASS（2026-11-24），覆盖 P3 工具清单、
RTX 5080 vs RX 9070 XT 实时对比、P3 覆盖/未覆盖判定、三个工具层的 source_chain。
