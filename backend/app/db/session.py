"""
异步数据库会话层。

- 使用 SQLAlchemy 2.0 async engine，全程 await，不阻塞事件循环。
- 通过 env（DATABASE_URL）切换 SQLite / Postgres，业务代码零改动。
- sessionmaker 用 AsyncSession，配合 FastAPI 依赖注入做 per-request 会话。
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    url = settings.DATABASE_URL
    # SQLite 在异步下需要单连接 + 外键开启；Postgres 用标准连接池
    if url.startswith("sqlite"):
        engine = create_async_engine(
            url,
            echo=False,
            future=True,
            # timeout：sqlite3 在锁等待时最多阻塞 30s 再报 locked；
            # 配合下面的 WAL + busy_timeout，可承受轻量并发写（演示够用）
            connect_args={"check_same_thread": False, "timeout": 30},
            poolclass=None,  # aiosqlite 用 NullPool 行为
        )
        # WAL：读写可并发；busy_timeout：锁冲突时排队而非立即报错
        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_connect(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        return engine
    return create_async_engine(
        url,
        echo=False,
        future=True,
        pool_size=20,
        max_overflow=10,
        pool_pre_ping=True,
    )


engine = _make_engine()

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：每个请求一个会话，结束后关闭。"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """建表（仅 demo 用；生产请用 Alembic 迁移）。"""
    # 导入所有模型，确保 metadata 注册完整
    from app.db import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    await engine.dispose()
