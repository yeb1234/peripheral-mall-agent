# P4 ↔ P6 商品文案链路

目标：P6（VidFlow 商品视频管线）不再自己维护一份商品库，做文案/脚本时直接读 P4 的商品事实；
P4 的售前 Agent 也能反过来调 P6 的文案能力。两条通道都通：REST 给业务代码，MCP 给 Agent。

## 1. 数据流

```
                    ┌─────────────────────────────┐
  用户/售前 Agent ──▶│ P4  copy_tool               │
                    │ generate_product_copy(sku)  │
                    └──────────┬──────────────────┘
                               │ POST /api/copy/generate
                               ▼
                    ┌─────────────────────────────┐
                    │ P6  product_copy            │
                    │ 只认 SKU，不存商品           │
                    └──────────┬──────────────────┘
                               │ GET /api/catalog/{sku}/card
                               ▼
                    ┌─────────────────────────────┐
                    │ P4  catalog（PG + p4-assets）│
                    │ 价格/库存 + 卖点/文案/图片    │
                    └─────────────────────────────┘
```

MCP 是同一能力的第二条入口：

```
P4 /mcp  ── get_product_card(sku) ─────────▶ 下游一次拿全商品卡
P6 /mcp  ── generate_product_copy(sku) ────▶ 内部同样反查 P4 catalog
```

## 2. 为什么商品库只留一份

- 价格、库存、规格在 P4 一直变；P6 复制一份必然漂移，文案里的价格和商品页对不上就是事故。
- P4 的商品卡本身有两层来源：运营口径走 PG（价格/库存/文件索引），营销口径走 `p4-assets`
  （卖点、短/长文案、场景、关键词）。这两层已经是单一事实源，P6 只消费。
- P6 侧只做表达：渠道（详情页/短视频/社媒）、语气、合规提示，不碰事实。

## 3. 契约

### 3.1 P4 → 下游：商品卡

`GET /api/catalog/{sku}/card`

| 字段 | 说明 |
|---|---|
| `sku` / `name` / `brand` / `category` | 基础身份 |
| `price` / `currency` / `stock` | 运营口径，实时 |
| `specs_flat` | 压平后的参数（`分组·属性: 值`） |
| `selling_points` | 5–6 条营销卖点，来自 p4-assets（带利益点） |
| `copy` | `tagline` / `short` / `long` / `keywords` |
| `images` | 商品图 + 资产包图片 URL |
| `files` | 说明书/资源/驱动的 FileAsset（含下载 URL、sha256） |
| `manuals` / `resources` / `drivers` | 按类型分开的原始资产元数据 |
| `source` | `{product: p4-postgres, copy: p4-assets}`，便于排查 |

`GET /api/catalog/search?q=&limit=` 返回 SKU 摘要，给 P6 选品用。

### 3.2 P6：文案

`POST /api/copy/generate`

```json
{"sku": "KB-PRO87", "channel": "detail|short_video|social", "tone": "专业|轻松|科技感"}
```

返回：

```json
{
  "ok": true,
  "sku": "KB-PRO87",
  "source": {"product": "p4:/api/catalog", "copy": "p6:template"},
  "copy": {
    "title": "P4Gear P4 机械键盘 Pro 87",
    "selling_points": ["75% / 87 键｜桌面留出更多鼠标空间，视线更聚焦", "..."],
    "short": "P4 机械键盘 Pro 87｜把配列、轴体与触发都交回给你自己。...",
    "long": "...",
    "oral_script": "...到手价 499 元...",
    "hashtags": ["P4Gear", "机械键盘", "好物推荐"],
    "channel": "detail", "tone": "专业",
    "specs_summary": {"核心参数·配列": "75% / 87 键"},
    "compliance_notes": ["不承诺最低价/绝对化用语", "参数与价格以商品页为准"]
  },
  "product": {"name": "...", "brand": "...", "price": 499.0, "files": 6}
}
```

失败语义：P4 不可达时返回 `{"ok": false, "reason": "p4_catalog_unavailable"}`，
P6 不编造商品事实；P4 的 Agent 工具收到失败会退回「用公开参数讲解」，不阻塞对话。

### 3.3 MCP

| 侧 | 地址 | 工具 | 说明 |
|---|---|---|---|
| P4 | `http://127.0.0.1:8000/mcp` | `get_product_card(sku)` | 12 个工具之一，商品卡组合包 |
| P6 | `http://127.0.0.1:12706/mcp` | `generate_product_copy(sku, channel, tone)` | 唯一工具，内部反查 P4 |

两边都是 Streamable HTTP + `json_response=true` + stateless，直接用官方 MCP client 接。

## 4. 运行前置

| 变量 | 位置 | 值 |
|---|---|---|
| `P6_BASE_URL` | P4 backend | `http://127.0.0.1:12706`（compose 内是 `http://host.docker.internal:12706`） |
| `P4_BASE_URL` | P6 api | `http://host.docker.internal:8000`（容器访问宿主） |
| `P6_COPY_ENABLED` | P4 backend | `true`，关掉则工具直接返回降级 |

启动顺序：P4 先起（P6 要读 catalog）；P6 起后 P4 无需重启（每次请求现连）。

## 5. 实测（2026-11-24）

`python scripts/probe_bridge.py` → **13/13 PASS**，报告在 `docs/bridge_test_report.json`。

| 用例 | 结果 |
|---|---|
| P4 health / vector status | total=442（26 商品 + 416 章节） |
| P4 vector search「热插拔 机械键盘」 | top1 = KB-PRO87 |
| P4 files manifest | 156 个文件 |
| P4 catalog card | 5 卖点 / 6 文件 / 3 驱动 |
| P6 health | db=true，p4_base_url 已配 |
| P6 copy REST | title/5 卖点，来源 `p4:/api/catalog` |
| P4→P6 client | ok，source 同上 |
| P4 MCP tools/list + get_product_card | 12 工具，返回商品卡 |
| P6 MCP tools/list + generate_product_copy | 1 工具，返回文案 |
| P4 Agent 工具 `generate_product_copy` | 已注册，handler 返回完整 copy |

## 6. 边界与下一步

- P6 的文案目前是模板（事实驱动、无 LLM），稳定可测；要更自然可以接 P6 的 `llm_orchestrator`，
  但事实仍必须来自 P4。
- 多销售域（A2A）扩展时，新增域只注册自己的商品卡工具，不复制 catalog；用能力注册中心 +
  统一信封，避免 N² 直连。
- 图片/视频二进制后续进 MinIO（P6 已有），P4 的 `FileAsset.rel_path` 换成 object_key 即可。
