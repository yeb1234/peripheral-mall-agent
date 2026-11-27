# P4 运维手册（Postgres + pgvector + 文件资产）

这份手册只写「怎么把它跑起来、怎么备份、坏了怎么查」，架构取舍见
`ARCHITECTURE` 与 `docs/HARNESS_COMPLIANCE.md`。

## 1. 拓扑与端口

| 组件 | 端口 | 说明 |
|---|---|---|
| P4 backend | 8000 | FastAPI + Agent + MCP `/mcp` |
| P4 Postgres | 5432 | `pgvector/pgvector:pg16`，库 `p4agent` |
| P4 Redis | 6379 | 限流、去重、确认单锁、物流缓存 |
| P4 frontend | 5173 | Vite dev（生产由 backend 挂 `frontend/dist`） |
| P6 API | 12706 | VidFlow，文案接口 `/api/copy/generate`、MCP `/mcp` |
| P6 Postgres / Redis / MinIO | 12506 / 12606 / 19006(19016 控制台) | P6 自有栈，不与 P4 共库 |
| P3 MCP | 12415（API 12414，PG 12504） | 参数/跑分事实库，P4 的**第一信源** |

端口按本机规则：123 前端、127 接口、125 数据库、126 缓存，尾号是项目号（P4 是 0 系，P6 是 06）。

## 2. 启动

### 2.1 全栈（compose）

```bash
docker compose up -d --build
docker compose ps          # backend 应为 healthy
```

compose 里固定了四件事：db 用 pgvector 镜像、backend 挂 `p4storage:/data/storage`、
backend healthcheck 打 `/api/health`、`P6_BASE_URL` 默认指宿主 `12706`（跨 compose 用
`host.docker.internal`，已配 `extra_hosts`）。

P3 默认 `P3_MCP_URL=http://127.0.0.1:12415/mcp`（宿主）。P4 在 compose 内且与 P3 同网络时改
`http://p3v2-mcp:8000/mcp`；不在同一网络就 `docker network connect v2_default <p4容器>`
或者走 `host.docker.internal:12415`。

镜像拉不动时用镜像站变量，不要改文件：

```bash
PGVECTOR_IMAGE=docker.m.daocloud.io/pgvector/pgvector:pg16 docker compose up -d
```

### 2.2 本机 dev（连容器里的 PG/Redis）

```powershell
$env:DATABASE_URL='postgresql+asyncpg://p4:p4pass@127.0.0.1:5432/p4agent'
$env:CACHE_BACKEND='redis'
$env:REDIS_URL='redis://127.0.0.1:6379/0'
$env:STORAGE_DIR='storage'
$env:PGVECTOR_ENABLED='true'
# 可选：不配 key 时向量层自动降级，不影响启动
$env:SILICONFLOW_API_KEY=''
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

首次启动按顺序做五件事（都在 lifespan 里，单件失败不阻塞整站）：

1. `create_all` 建 16 张业务表（+ `file_assets`）
2. 空库 seed：26 商品 / 78 驱动 / 26 说明书 / 52 资源 / 4 演示订单 / 9 政策
3. 知识库切片同步：78 份文档 → 416 个章节（sha256 幂等）
4. 文件资产落盘：156 个 md（416 切片所属文档 + 78 驱动说明包）→ `file_assets`
5. 向量索引：442 条写进 `doc_embeddings`（26 商品 + 416 章节），内容没变则跳过

## 3. 存储

### 3.1 表与索引

`doc_embeddings` 是原生 SQL 建的（向量列没有跨库 ORM 等价物）：

```sql
-- 体检
SELECT extname, extversion FROM pg_extension WHERE extname='vector';
SELECT count(*), source_kind FROM doc_embeddings GROUP BY source_kind;
\d+ doc_embeddings
```

索引：`doc_embeddings_hnsw`（`vector_cosine_ops`），距离 `<=>`，score = `1 - distance`。

### 3.2 重建向量

```bash
# 增量：只处理内容变化的切片
curl -X POST http://127.0.0.1:8000/api/vector/reindex

# 全量：换 embedding 模型 / 怀疑数据脏时
curl -X POST "http://127.0.0.1:8000/api/vector/reindex?force=true"
```

换模型必须同时改维度，顺序不能反：

1. 停写（停 backend）
2. 改 `PGVECTOR_DIM`（bge-m3 是 1024）
3. `DROP TABLE doc_embeddings;`（旧维度列不能原地 alter）
4. 起服务，自动建新表 + 全量写入

### 3.3 文件资产

- 根目录：`STORAGE_DIR`（compose 是 `/data/storage`，本机是 `backend/storage`）
- 目录：`storage/{kind}/{sku}/...`，`kind` = manual / resource / driver
- DB 只存 `file_assets`（rel_path、size、sha256、source_id），不存二进制
- 访问：`GET /api/files/manifest`、`GET /api/files/{id}`（下载）、`/api/files/{id}/meta`
- 换 MinIO/S3：只改 `file_store._write` 与 `abspath`，`rel_path` 语义换成 object_key，表与接口不动

驱动目前是「说明包」（版本、平台、SHA256、安装步骤），仓库不分发真实二进制；接对象存储后把
`file_url` 指向签名地址即可。

## 4. 备份与恢复

```bash
# 数据库
docker exec p4fagent-db-1 pg_dump -U p4 -d p4agent -Fc > p4_$(date +%F).dump

# 文件资产（和库分开备份，恢复时先库后文件）
docker run --rm -v p4fagent_p4storage:/data -v $PWD:/out alpine \
  tar czf /out/p4storage_$(date +%F).tgz -C /data .

# 恢复
docker exec -i p4fagent-db-1 pg_restore -U p4 -d p4agent --clean < p4_2026-11-24.dump
```

顺序：先恢复 PG（元数据），再恢复 storage（正文）。sha256 对不上时 `/api/files/manifest`
会暴露，重新 `ensure_seed` 会补写。

## 5. 观测

| 看什么 | 入口 | 正常值 |
|---|---|---|
| 存活 | `GET /api/health` | `status=ok`，cache=redis |
| 向量 | `GET /api/vector/status` | total>0，by_kind 有 product/knowledge |
| 文件 | `GET /api/files/manifest` | count=156（当前数据量） |
| 审计 | `audit` 表 | 写操作 4 类事件齐全 |
| 压测基线 | `python scripts/loadtest.py --target products -c 10 -n 100` | 本机 292 rps / P95 75ms / 0 错 |
| 信源链路 | `python scripts/probe_sources.py` | 7/7：P3 工具、P3 对比、路由器覆盖判定、三层 source_chain |

压测三档：`products`（纯 DB，不花钱）、`vector`（会调 embedding）、`chat`（走完整 LLM，
最贵）。报告落 `docs/loadtest_<target>.json`。

## 6. 扩容与回滚

- 连接池：`session.py` PG 侧 `pool_size=20, max_overflow=10`；先加 worker 再加池，别反向
- 进程：`uvicorn --workers N`（本机 dev 单进程够用；compose 生产按 CPU 核数）
- 向量：数据量上百万再调 HNSW 的 `m` / `ef_construction`；当前 442 条不需要
- 回滚：`git tag` 语义化版本 + 回退 compose 镜像；数据库变更先备份再回滚，pgvector 表可直接重建

## 7. 故障排查

| 症状 | 先查 | 处置 |
|---|---|---|
| 启动即退出 | `.env`、`DATABASE_URL`、asyncpg 是否装 | 本机 `pip install asyncpg`；compose 看 `docker logs` |
| `/api/vector/status` total=0 | 是否有 `SILICONFLOW_API_KEY`、`RAG_ENABLED` | 配 key 后 `POST /api/vector/reindex` |
| `CREATE EXTENSION vector` 失败 | db 镜像是否是 pgvector、用户是否 superuser | 换 `pgvector/pgvector:pg16`；compose 的 `p4` 是 superuser |
| HNSW 索引建不出来 | pgvector 版本 | 低于 0.5 会跳过索引，功能仍可用（顺序扫描） |
| P6 文案 502/超时 | `P6_BASE_URL`、P6 容器是否起、`/health` | P6 容器内访问 P4 要 `host.docker.internal`，别写 127.0.0.1 |
| 跑分/参数答成「连不上事实库」 | `P3_MCP_URL`、P3 容器、`from mcp import ClientSession` | 先 `python scripts/probe_sources.py`；mcp 2.x 导入路径写错会被降级吞掉 |
| 外设对比总是落到联网 | P3 是否注册了 peripherals domain | 属预期：P3 目前只有 cpu/gpu/soc/auto，字段设计见 `docs/SOURCE-PRIORITY.md` |
| 429 偏多 | Redis 限流键、买家维度 | 读写分离买家（售前一次性 buyer_id），别共用 |
| 文件下载 404 | `rel_path` 是否存在、卷是否挂对 | 重跑 `ensure_seed`；compose 检查 `p4storage` 卷 |

## 8. 安全与发布

- `.env` 永不进 git（`check_structure.py` 会查）
- 买家身份：token 优先，`REQUIRE_BUYER_TOKEN=true` 时强制；写操作仍走二次确认
- 发布：`main` 必须能 `docker compose up`；分支 `feat/*`、`fix/*`、`eval/*`，commit 前缀
  `feat/fix/eval/docs/refactor/chore`
- CI：`.github/workflows/ci.yml` —— 静态扫描、结构自检、pgvector 起服务、健康/文件/向量探活、
  安全 quick、压测基线、前端构建
