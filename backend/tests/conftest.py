"""pytest 全局夹具。"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """提供基于 ASGI 传输的 httpx 异步测试客户端。

    Yields:
        AsyncClient: 已绑定到 FastAPI 应用的异步客户端。
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
