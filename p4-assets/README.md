# P4 产品资源库 · P4 Asset Library

版本 `1.0.0` · 26 个 SKU · 自包含静态资产目录。
**这个文件夹可以直接拷给别的项目 / 传 CDN / 进 git，不依赖 P4 商城的数据库或 Python 环境。**

## 里面有什么

| 目录 / 文件 | 内容 |
|---|---|
| `manifest.json` | 索引：版本、生成时间、品类、每个 SKU 的文件路径与摘要、MCP 入口 |
| `schema.json` | 资产包 JSON Schema（draft 2020-12），别的项目照此解析 |
| `products/{SKU}.json` | 单个产品的完整资产包 |
| `images/products/{SKU}.svg` | 产品卡图（矢量，600×400） |
| `images/visuals/{category}-16x9.svg` | 品类 16:9 主视觉（1600×900，视频封面 / 背景板） |
| `images/brand/hero.png` | 品牌主视觉（片尾品牌帧） |

## 资产包字段（`products/{SKU}.json`）

- `identity`：SKU、名称、品牌、品类、价格、是否主推
- `specs` / `specs_flat`：**参数**（DB 原始分组规格 + 压平版，字幕直接取 `specs_flat`）
- `copy`：**文案**
  - `tagline` 主标语、`short_copy` 电商短文案、`long_copy` 详情页长文案
  - `selling_points[]` 卖点（`fact` 是真实参数值，`benefit` 是利益点，`copy` 可直接上字幕）
  - `scenes[]` 使用场景、`keywords[]` 检索标签
  - `video_script` **短视频分镜**：30 秒 / 16:9 / 5 镜头，每镜头含 `timecode`、`visual`、`overlay_text`、`voiceover`、`asset_ref`
- `images`：每类图片的 `path`（相对本目录）、`url`（HTTP）、`mime_type`、尺寸、用途
- `downloads`：该型号的驱动 / 说明书 / 资源引用（含 SHA256）
- `provenance`：来源与生成信息

> 事实约束：文案与分镜字幕里的参数全部来自商品库真实 `specs`，模板不会编造未标注性能。
> 生成脚本 `backend/tools/build_assets.py` 确定性可重跑，改模板后可整库重建。

## 做宣传视频怎么用

1. 读 `manifest.json` 挑 SKU（可按 `category` / `featured` 过滤）
2. 读 `products/{SKU}.json` 的 `copy.video_script.shots[]`，按 `timecode` 排期
3. 每个镜头的 `asset_ref` 对应 `images` 里的键（`product_card` / `category_visual` / `brand_hero`），取 `path` 或 `url` 拿素材
4. `overlay_text` / `voiceover` 直接做字幕与配音稿；`bgm_hint` / `style` 给剪辑风格参考
5. 需要更多参数时查 `specs_flat`，需要合规说明看 `copy.video_script.compliance_note`

## 通过 MCP 读取（推荐）

P4 商城把这份资源库以 MCP 协议供出去，别的项目不用解析文件也能读：

- **Streamable HTTP**：`http://127.0.0.1:8000/mcp`（服务在线即可连）
- **独立 HTTP 端口**：`python -m tools.mcp_http`（默认 `http://127.0.0.1:8001/mcp`）
- **stdio**（配到客户端 `mcp.json`）：`python -m tools.mcp_server`

提供的工具：`get_manifest` / `list_products` / `search_products` / `get_product` / `get_marketing_copy` / `get_specs` / `get_images` / `get_video_storyboard` / `get_downloads` / `get_brand_kit` / `health`。
资源：`p4assets://manifest`、`p4assets://products/{sku}`、`p4assets://products/{sku}/copy` 等。
Prompt：`product_promo_video(sku)` 直接产出一段视频脚本创作提示词。

`ASSET_BASE_URL`（默认 `http://127.0.0.1:8000/p4-assets`）可在 `build_assets.py` 里改成实际部署域名，
改完重跑即可批量刷新所有图片 URL。
