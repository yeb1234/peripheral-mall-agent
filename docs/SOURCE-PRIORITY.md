# 信源优先级：P3 第一，公开网络第二

P4 做「严格产品对比 / 推荐」时的取值顺序是硬规则，不是提示词建议：

| 层 | 信源 | 管什么 | 代码 |
|---|---|---|---|
| 1 | **P3 内部事实库** | 跨品牌参数、跑分、评测结论；每条事实带来源、置信度、冲突标记 | `app/core/p3_client.py` |
| 2 | **P4 商品库** | 自营 SKU 的价格、库存、规格、文件资产（经营事实） | `app/services/catalog` + `routers/catalog.py` |
| 3 | **公开网络** | 内部库没覆盖的型号/品类，只补事实，不产出结论 | `web_search_tool` |

判定与编排在 `app/services/source_router.py`：P3 覆盖就出 P3 结论；部分覆盖只出已覆盖部分，
缺口单独列出，不拿网文凑表；P3 明确解析不了或没数据，才给 `fallback_query` 走第二信源。

## 1. 工具映射

| 用户问题 | 路由 | 实际信源 |
|---|---|---|
| 「KB-PRO87 和 KB-60HE 差在哪」 | `compare_products` → 本地 SKU 命中 | `p4_catalog` |
| 「RTX 5080 和 RX 9070 XT 哪个强」 | `compare_products` → 本地无 SKU → `source_router` | `p3`（Time Spy 综合分） |
| 「9800X3D 和 285K 跑分」 | `compare_benchmarks`（确定性护栏） | `p3` |
| 「Keychron Q1 和 KB-PRO87 比一下」 | `compare_products` → P3 无外设 domain | `p3`（判定未覆盖）→ `websearch` |
| 「磁轴键盘是什么原理」 | `web_search` | `websearch`（纯外部知识） |

`compare_products` 的返回里带 `data.source_chain`：`["p4_catalog"]` / `["p3"]` / `["p3","websearch"]`，
出问题时能直接看出这句话是从哪层来的。

## 2. P3 现在有什么

- 已入库 5 个产品：Apple M4（SoC）、RTX 5080 / RX 9070 XT（GPU）、9800X3D / 285K（CPU）。
- 4 个 MCP 工具：`compare_products`、`get_product_facts`、`search_products`、`benchmark_ranking`。
- 品类注册表：`electronics`（cpu/gpu/soc）+ `auto`（字段已定义、数据未接）。
- **没有外设 domain**：键盘/鼠标/耳机传进去会报「无法判断品类」，P4 就按设计走第二信源。

## 3. 外设评测怎么进 P3（下一步，不改现有骨架）

P3 的 EAV + `DomainProfile` 天生支持加品类，外设只需要新增一个 profile，不新建表：

```python
# agent/domains/peripherals.py（示意）
FieldSpec("periph.switch_type", "轴体", direction=None)      # 线性/段落/磁轴
FieldSpec("periph.weight_g", "重量", unit="g", direction="lower")
FieldSpec("periph.battery_h", "续航", unit="h", direction="higher")
FieldSpec("periph.polling_hz", "回报率", unit="Hz", direction="higher")
FieldSpec("periph.keycap", "键帽", direction=None)           # PBT/ABS/双色
FieldSpec("review.score", "评测分", unit="分", direction="higher")  # 自营评测机构口径
```

两条来源层级要分开存：厂商参数（`source_tier=vendor`）、自营评测（`tier=inhouse`）、
媒体评测（`tier=media`）。P4 回答时按 `tier` 决定口吻——自营评测可以说「我们实测」，
媒体数据只能说「公开评测显示」。

接入后 P4 不用改代码：`source_router.resolve_models(["Keychron Q1","KB-PRO87"])` 会从
`source=none` 变成 `source=p3`，`compare_products` 自然切到第一信源。

## 4. 为什么不直接让 P4 搜网

- **可复核**：P3 的事实带 `source_url` / `confidence` / `conflicts`，客服说「32642 分」能追到来源；
  网文摘要做不到，且同一型号不同媒体口径经常打架。
- **一致性**：P4 的商品页价格/库存与 P3 的参数分开管，但品牌/型号解析共用一套别名，
  不会出现「P4 说 87 键、网文说 75%」的自我矛盾。
- **成本与延迟**：P3 查库是毫秒级；联网一次 5–15 秒且受额度限制。第一信源命中时不该付这个代价。

## 5. 实测（2026-11-24）

`python backend/scripts/probe_sources.py` → **7/7 PASS**，报告 `docs/source_test_report.json`。

| 用例 | 结果 |
|---|---|
| P3 MCP 工具清单 | 4 个工具在线 |
| P3 实时对比 RTX 5080 vs RX 9070 XT | Time Spy 综合分，2 款产品 |
| 路由器 P3 覆盖 | `source=p3`，coverage=1.0 |
| 路由器外设型号 | `source=none`，理由「无法判断 Keychron Q1 是 CPU/GPU/SoC/车型」 |
| 本店 SKU 对比 | `source_chain=["p4_catalog"]` |
| 市场型号对比 | `source_chain=["p3"]`，输出 Time Spy 分数 |
| 外设竞品对比 | `source_chain=["p3","websearch"]`，P3 判未覆盖后走百炼 |

## 6. 运维注意

- 默认地址 `P3_MCP_URL=http://127.0.0.1:12415/mcp`（宿主）；P4 在 compose 内且与 P3 同网络时
  改 `http://p3v2-mcp:8000/mcp`，跨 compose 用 `host.docker.internal`。
- mcp 2.x 的客户端导入是 `from mcp import ClientSession`；写成 `mcp.client` 会 ImportError，
  且会被工具层的降级吞掉，表现为「跑分库连不上」——排障时先看这一行。
- P3 的 `comparisons` 是结果缓存，抽取规则变更后要清表，否则旧结论会一直返回。
