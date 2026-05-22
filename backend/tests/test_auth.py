"""``/api/v1/auth`` 路由端到端测试。

使用 ``aiosqlite`` 内存数据库 + ``app.dependency_overrides[get_db]`` 注入测试会话，
避免触达真实 PostgreSQL，同时通过 ``ASGITransport`` 直接驱动 FastAPI 应用。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# 触发模型注册：确保 Base.metadata 包含所有业务表。
from app import models  # noqa: F401
from app.api.deps import get_db
from app.db.base import Base
from app.main import app


@pytest_asyncio.fixture
async def auth_client() -> AsyncGenerator[AsyncClient, None]:
    """提供绑定到 in-memory SQLite 的鉴权测试客户端。

    通过 ``StaticPool`` 让所有会话共享同一个内存数据库连接，
    并在测试结束后清理依赖覆盖与连接池。

    Yields:
        AsyncClient: 已绑定 ASGI 应用、且 ``get_db`` 已被覆盖的异步客户端。
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    test_session_maker = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with test_session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


async def test_register_and_me(auth_client: AsyncClient) -> None:
    """完整走通注册 → 登录 → 访问 ``/auth/me`` 链路。"""
    register_resp = await auth_client.post(
        "/api/v1/auth/register",
        json={"username": "alice", "password": "password123"},
    )
    assert register_resp.status_code == 201, register_resp.text
    user_data = register_resp.json()
    assert user_data["username"] == "alice"
    assert "id" in user_data
    assert user_data["role"] in {"user", "admin"}

    login_resp = await auth_client.post(
        "/api/v1/auth/login",
        data={"username": "alice", "password": "password123"},
    )
    assert login_resp.status_code == 200, login_resp.text
    token_data = login_resp.json()
    assert token_data["token_type"] == "bearer"
    assert isinstance(token_data["access_token"], str) and token_data["access_token"]
    assert token_data["user"]["username"] == "alice"

    me_resp = await auth_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token_data['access_token']}"},
    )
    assert me_resp.status_code == 200, me_resp.text
    me_data = me_resp.json()
    assert me_data["id"] == user_data["id"]
    assert me_data["username"] == "alice"


async def test_register_duplicate(auth_client: AsyncClient) -> None:
    """重复注册同一用户名应返回 ``400``。"""
    payload = {"username": "bob", "password": "password123"}
    first = await auth_client.post("/api/v1/auth/register", json=payload)
    assert first.status_code == 201, first.text

    second = await auth_client.post("/api/v1/auth/register", json=payload)
    assert second.status_code == 400, second.text


async def test_login_wrong_password(auth_client: AsyncClient) -> None:
    """密码错误的登录应返回 ``401``。"""
    register_resp = await auth_client.post(
        "/api/v1/auth/register",
        json={"username": "carol", "password": "password123"},
    )
    assert register_resp.status_code == 201, register_resp.text

    bad_login = await auth_client.post(
        "/api/v1/auth/login",
        data={"username": "carol", "password": "wrong-password"},
    )
    assert bad_login.status_code == 401, bad_login.text
