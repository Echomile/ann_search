"""异步数据库会话管理。

提供 :data:`async_engine`、:data:`AsyncSessionLocal` 与 :func:`get_db` 依赖。
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

async_engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True,
    pool_pre_ping=True,
)

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：提供一个异步数据库会话。

    Yields:
        AsyncSession: 异步数据库会话，作用域结束后自动关闭。
    """
    async with AsyncSessionLocal() as session:
        yield session
