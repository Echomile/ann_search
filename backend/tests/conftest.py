"""pytest 全局夹具。"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app


class FakeRedis:
    """最小 in-memory Redis 替身：仅支持 ``set(..., ex=...)`` 与 ``get``。

    用于 :mod:`app.services.search_cache` 在测试环境中跳过真实 Redis 依赖。
    通过 ``monkeypatch.setattr(search_cache, "_get_client", lambda: fake)`` 注入。
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:  # noqa: ARG002
        """模拟 ``SET key value EX ttl``；本替身忽略 ``ex``，存活到测试结束。"""
        self.store[key] = value

    async def get(self, key: str) -> str | None:
        """模拟 ``GET key``；不存在返回 ``None``。"""
        return self.store.get(key)


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """提供基于 ASGI 传输的 httpx 异步测试客户端。

    Yields:
        AsyncClient: 已绑定到 FastAPI 应用的异步客户端。
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
