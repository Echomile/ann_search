"""``GET /api/v1/indexes/cache/stats`` 合并字段端到端测试（F2 part2-b）。

验证目标：
    - 未登录请求返回 ``401``；
    - 登录后返回字段同时包含 :class:`IndexCache` 与 SearchCache 两套 metrics；
    - 触发一次 ``cached_or_compute`` (miss → hit) 后，端点能实时反映
      ``search_cache_hits / search_cache_misses / search_cache_hit_ratio``。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401  触发模型注册
from app.api.deps import get_db
from app.db.base import Base
from app.main import app
from app.services import search_cache
from app.services.ann.cache import IndexCache


class _FakeRedis:
    """最小 in-memory Redis 替身，配合 monkeypatch 注入 search_cache。"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:  # noqa: ARG002
        self.store[key] = value

    async def get(self, key: str) -> str | None:
        return self.store.get(key)


@pytest_asyncio.fixture
async def stats_client() -> AsyncGenerator[AsyncClient, None]:
    """提供绑定到 in-memory SQLite 的端到端测试客户端。"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


async def _login(client: AsyncClient) -> dict[str, str]:
    """注册并登录一个用户，返回 Authorization 头。"""
    reg = await client.post(
        "/api/v1/auth/register",
        json={"username": "cache_stats_user", "password": "pw_for_cache"},
    )
    assert reg.status_code == 201, reg.text
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": "cache_stats_user", "password": "pw_for_cache"},
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest.fixture(autouse=True)
def _reset_caches() -> None:
    """每个用例独立的 SearchCache 计数与 IndexCache 单例状态。"""
    search_cache.reset_cache_metrics()
    search_cache._redis_client = None  # noqa: SLF001
    IndexCache.instance().clear()


async def test_cache_stats_requires_auth(stats_client: AsyncClient) -> None:
    """未登录访问应返回 401（与其他鉴权端点一致）。"""
    resp = await stats_client.get("/api/v1/indexes/cache/stats")
    assert resp.status_code == 401


async def test_cache_stats_merges_index_and_search_cache_fields(
    stats_client: AsyncClient,
) -> None:
    """合并字段完整覆盖 IndexCache + SearchCache 两套 metrics。"""
    headers = await _login(stats_client)

    resp = await stats_client.get("/api/v1/indexes/cache/stats", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    index_keys = {
        "capacity",
        "size",
        "hits",
        "misses",
        "loads",
        "evictions",
        "hit_ratio",
        "cached_index_ids",
    }
    search_keys = {
        "search_cache_hits",
        "search_cache_misses",
        "search_cache_errors",
        "search_cache_hit_ratio",
    }
    assert index_keys.issubset(body.keys()), body
    assert search_keys.issubset(body.keys()), body

    # 初始状态：两层缓存的命中率均为 0，且各计数器为 0。
    assert body["hit_ratio"] == 0.0
    assert body["search_cache_hit_ratio"] == 0.0
    assert body["search_cache_hits"] == 0
    assert body["search_cache_misses"] == 0


async def test_cache_stats_reflects_search_cache_activity(
    stats_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """触发 miss → hit 后端点应实时反映 SearchCache 计数与命中率。"""
    fake = _FakeRedis()
    monkeypatch.setattr(search_cache, "_get_client", lambda: fake)

    async def compute() -> dict[str, Any]:
        return {"hits": [{"rank": 1, "cell_id": "demo", "distance": 0.1}]}

    key = search_cache.make_cache_key(index_id=1, top_k=5, query="cell_demo", filters=None)
    await search_cache.cached_or_compute(key, compute)  # miss
    await search_cache.cached_or_compute(key, compute)  # hit

    headers = await _login(stats_client)
    resp = await stats_client.get("/api/v1/indexes/cache/stats", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["search_cache_hits"] == 1
    assert body["search_cache_misses"] == 1
    assert body["search_cache_errors"] == 0
    assert body["search_cache_hit_ratio"] == 0.5
