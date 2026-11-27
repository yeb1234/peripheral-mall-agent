"""补丁：重算说明书/资源校验值 + 把说明书自动同步进知识库。

为什么需要：说明书下载文件从「几行占位元信息」升级为「结构化章节全文」后，
文件字节变了，库里旧的 sha256 不再匹配；同时知识库切片需要按新正文重建。

- 可重复执行：sha256 一致就跳过，知识库靠内容指纹幂等，不产生重复切片。
- 正常部署其实无需手动跑：main.py 的 lifespan 启动时会自动 sync_knowledge()。
  本脚本用于存量数据重算 sha256，以及演示「同步统计」。

运行：python -m tools.sync_knowledge
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.db.models import Manual, Product, Resource
from app.db.session import AsyncSessionLocal, init_db
from app.services import knowledge_service as kb
from app.services import resource_service as rsvc


async def main() -> None:
    await init_db()
    async with AsyncSessionLocal() as db:
        fixed = 0
        checked = 0
        for model, kind in ((Manual, "manual"), (Resource, "resource")):
            rows = (
                await db.execute(
                    select(model, Product).join(Product, model.product_id == Product.id)
                )
            ).all()
            for m, p in rows:
                item = rsvc._serialize(m, p, kind)
                want = rsvc.item_sha256(kind, item)
                checked += 1
                if m.sha256 != want:
                    m.sha256 = want
                    fixed += 1
        await db.commit()
        print(f"校验值：检查 {checked} 份文档，重算 {fixed} 个 SHA256（与下载文件正文一致）。")

    # 独立 session 同步知识库，避免上面 commit 后的事务快照影响
    async with AsyncSessionLocal() as db2:
        out = await kb.sync_knowledge(db2)
        print(f"知识库：{out['detail']}")
        st = await kb.stats(db2)
        print(f"知识库现状：共 {st['total_docs']} 个章节切片，覆盖 {st['sku_count']} 个型号；"
              f"商品参数文档 {st['product_docs']} 个（独立索引域）。")


if __name__ == "__main__":
    asyncio.run(main())
