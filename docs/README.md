# 文档索引

[← 返回 README](../README.md) · [English README](../README.en.md)

仓库里的文档按用途分四类：先读架构，再按你要做的事选集成、运维或质量报告。

## 入门

| 文档 | 内容 | 适合 |
| --- | --- | --- |
| [README](../README.md) | 项目简介、功能、快速开始、接口与测试现状 | 第一次接触项目 |
| [ARCHITECTURE](ARCHITECTURE.md) | 分层结构、请求主链路、多 Agent 编排、六道关、数据与信源 | 想理解设计而非只看功能 |
| [README.en.md](../README.en.md) | 英文版项目说明 | 英文读者 |

## 集成

| 文档 | 内容 |
| --- | --- |
| [P4-P6-BRIDGE](P4-P6-BRIDGE.md) | 与商品视频系统 P6 的 REST / MCP 契约、字段、错误码与联调步骤 |
| [P3-MCP-INTEGRATION](P3-MCP-INTEGRATION.md) | 参数事实库 P3 的 MCP 工具、缓存与并发单飞策略 |
| [SOURCE-PRIORITY](SOURCE-PRIORITY.md) | 三级信源取值顺序、来源标记与冲突处理 |

## 运维

| 文档 | 内容 |
| --- | --- |
| [OPS_RUNBOOK](OPS_RUNBOOK.md) | 启动、备份恢复、扩容、常见故障与排障路径 |

## 质量报告

`reports/` 下为脚本直接产出的机器可读报告，随代码一起更新。

| 文件 | 来源 |
| --- | --- |
| [test_report.md](reports/test_report.md) / [test_report.json](reports/test_report.json) | `backend/scripts/probe_full.py` 对话全量 |
| [bridge_test_report.json](reports/bridge_test_report.json) | `backend/scripts/probe_bridge.py` 跨系统桥接 |
| [source_test_report.json](reports/source_test_report.json) | `backend/scripts/probe_sources.py` 信源路由 |
| [loadtest_products.json](reports/loadtest_products.json) | `backend/scripts/loadtest.py --target products` |
| [loadtest_vector.json](reports/loadtest_vector.json) | `backend/scripts/loadtest.py --target vector` |

## 图片资产

`images/` 下为 README 与架构说明使用的图：

- `architecture.png`：系统架构图（1200×780，由 SVG 源渲染）
- `home.png` / `products.png` / `workbench.png` / `nlu.png`：真实界面截图（1440×900）

## 维护约定

- `docs/` 只放对使用者、集成方与运维有用的文档；面试与评测材料不进仓库，统一放在本地 `interview/` 并由 `.gitignore` 排除。
- 测试报告由脚本生成，不手工编辑；改脚本输出路径时同步更新本页。
- 新增文档后在本页对应分类登记，避免出现孤儿文档。
