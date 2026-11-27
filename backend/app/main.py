"""
FastAPI 入口。

- 异步全栈：路由/DB/Redis/LLM 全部 await，不阻塞事件循环。
- 启动即建表并填充演示数据（无密钥也能跑通）。
- CORS 放开前端开发端口；生产由反向代理统一管理。
"""
from __future__ import annotations

import os
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.core.errors import AppError, ErrorCode
from app.core.llm_registry import get_registry
from app.core.rerank_client import get_reranker
from app.core.websearch_client import get_websearch
from app.db.session import AsyncSessionLocal, dispose_engine, init_db
from app.routers import (auth, chat, drivers, knowledge, orders, products, resources,
                         workbench, flash_sale, llm, rag, memory, nlu, websearch,
                         files, catalog, vector)

# MCP 资源库子应用：lifespan 与 mount 必须共用同一个实例。
try:
    from tools.mcp_server import create_http_app
    _mcp_app = create_http_app(path="/")
except Exception as e:
    _mcp_app = None
    print(f"[main] MCP endpoint disabled: {type(e).__name__}: {e}", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # FastAPI 的 mount 不会自动运行子应用 lifespan，MCP session manager 必须手动进入，
    # 否则 /mcp 会报 "Task group is not initialized. Make sure to use run()"。
    async with AsyncExitStack() as stack:
        if _mcp_app is not None:
            await stack.enter_async_context(_mcp_app.router.lifespan_context(_mcp_app))
        await init_db()
        # 空库自动填充演示数据（商品/驱动/订单/政策）
        async with AsyncSessionLocal() as db:
            from sqlalchemy import func, select
            from app.db.models import Product
            cnt = await db.scalar(select(func.count()).select_from(Product))
            if not cnt:
                from app.db import seed
                await seed.seed()
            # 演示：为某款无线鼠标种一个「19:00 抢购」活动（库存有限）
            from datetime import datetime, time as dtime
            from app.services import flash_sale as fs_service

            wm = await db.scalar(
                select(Product).where(Product.category == "mouse", Product.name.like("%无线%")).limit(1)
            )
            if wm:
                launch = datetime.combine(datetime.today(), dtime(19, 0)).timestamp()
                fs_service.configure(wm.sku, total_stock=200, launch_at=launch, hold_seconds=120)
                print(f"[lifespan] flash-sale configured for {wm.sku}", flush=True)
            else:
                print("[lifespan] flash-sale: no wireless mouse found", flush=True)

        # 说明书自动入知识库：启动时用独立 session 做幂等同步（空库/存量库都自愈）。
        # 独立开 session 是为了避开上面 seed 在另一连接提交时的事务快照问题。
        try:
            async with AsyncSessionLocal() as kb_db:
                from app.services import knowledge_service as kb
                synced = await kb.sync_knowledge(kb_db)
                print(f"[lifespan] {synced['detail']}", flush=True)
        except Exception as e:
            # 知识库是增强能力，同步失败不能阻塞服务启动（检索时会给出明确提示）
            print(f"[lifespan] knowledge sync skipped: {type(e).__name__}: {e}", flush=True)

        # 文件资产：说明书/资源/驱动幂等落盘，元数据入 file_assets
        try:
            async with AsyncSessionLocal() as files_db:
                from app.services import file_store
                seeded = await file_store.ensure_seed(files_db)
                print(f"[lifespan] file assets: {seeded['written']} written / "
                      f"{seeded['skipped']} skipped / {seeded['failed']} failed", flush=True)
        except Exception as e:
            print(f"[lifespan] file seed skipped: {type(e).__name__}: {e}", flush=True)

        # 向量索引：只在 PG + embedding 可用时写；不可用则整体跳过，不阻塞启动
        try:
            async with AsyncSessionLocal() as vec_db:
                from app.services import vector_index
                synced = await vector_index.sync(vec_db)
                print(f"[lifespan] vector index: ok={synced.get('ok')} "
                      f"written={synced.get('written')} skipped={synced.get('skipped')} "
                      f"reason={synced.get('reason', '')}", flush=True)
        except Exception as e:
            print(f"[lifespan] vector sync skipped: {type(e).__name__}: {e}", flush=True)
        yield
        await dispose_engine()


app = FastAPI(title=settings.APP_NAME, version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(products.router)
app.include_router(orders.router)
app.include_router(drivers.router)
app.include_router(resources.router)
app.include_router(chat.router)
app.include_router(auth.router)
app.include_router(workbench.router)
app.include_router(flash_sale.router)
app.include_router(llm.router)
app.include_router(rag.router)
app.include_router(knowledge.router)
app.include_router(memory.router)
app.include_router(nlu.router)
app.include_router(websearch.router)
app.include_router(files.router)
app.include_router(catalog.router)
app.include_router(vector.router)


@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    """业务异常 -> 结构化错误码 JSON（整个系统统一出口）。"""
    return JSONResponse(status_code=exc.http_status, content=exc.to_dict())


@app.exception_handler(Exception)
async def unhandled_handler(_: Request, exc: Exception) -> JSONResponse:
    """未预期异常兜底：不暴露栈，统一为 SYS000 错误码。"""
    return JSONResponse(
        status_code=200,
        content={"code": ErrorCode.SYS_UNKNOWN.value, "message": "服务异常，请稍后重试。",
                 "detail": f"{type(exc).__name__}: {exc}"},
    )


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "llm": settings.LLM_BACKEND,                 # .env 里的启动默认值
        "llm_provider": get_registry().active,      # 设置页当前生效的提供方
        "cache": settings.CACHE_BACKEND,
        "rag_reranker": get_reranker().available,   # SiliconFlow 重排是否可用（失败自动降级）
        "websearch": get_websearch().available,      # 百炼联网搜索是否可用（失败自动降级）
        "nlu_mode": settings.NLU_MODE,               # 第一层语义理解：auto | always | off
    }


# 静态资源：商品图 / Hero / 驱动占位等（dev 与 prod 都由 backend 提供）
_static = os.path.join(os.path.dirname(__file__), "..", "static")
if os.path.isdir(_static):
    from fastapi.staticfiles import StaticFiles
    app.mount("/static", StaticFiles(directory=_static), name="static")

# 产品资源库：图片 / 资产包 JSON 通过 HTTP 直接可取（资产包里的 image.url 指向这里）
_assets_dir = os.path.join(os.path.dirname(__file__), "..", "..", "p4-assets")
if os.path.isdir(_assets_dir):
    from fastapi.staticfiles import StaticFiles
    app.mount("/p4-assets", StaticFiles(directory=_assets_dir), name="p4-assets")

# MCP：把资源库以 Streamable HTTP 供出去，别的项目连 /mcp 即可读取。
# 独立入口见 tools/mcp_http.py（8001）与 tools/mcp_server.py（stdio）。
# 这里不用 mount：Starlette 的 Mount 对无尾斜杠的 /mcp 只算 PARTIAL 匹配，
# 会被后面的 SPA catch-all 抢走并返回 405；直接把子应用的 ASGI handler
# 注册成 /mcp 与 /mcp/ 两条精确路由，POST/GET/DELETE 都能正确落到 MCP。
if _mcp_app is not None:
    from starlette.routing import Route as _Route
    _mcp_handler = _mcp_app.routes[0].app
    app.router.routes.append(_Route("/mcp", endpoint=_mcp_handler))
    app.router.routes.append(_Route("/mcp/", endpoint=_mcp_handler))

# 生产：挂载已构建的前端（dev 时由 Vite 提供），并对 SPA 路由做兜底
_dist = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist")
if os.path.isdir(_dist):
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse
    from fastapi import HTTPException
    # 构建产物（JS/CSS/图片）位于 /assets
    _assets = os.path.join(_dist, "assets")
    if os.path.isdir(_assets):
        app.mount("/assets", StaticFiles(directory=_assets), name="assets")
    _index = os.path.join(_dist, "index.html")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        # 已注册的 /api 路由与 /static、/assets 资源优先；其余前端路由回退到 index.html
        if full_path.startswith(("api/", "static/", "assets/", "mcp/", "p4-assets/")):
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(_index)
