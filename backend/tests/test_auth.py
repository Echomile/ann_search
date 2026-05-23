"""``/api/v1/auth`` 与 ``/api/v1/admin/users`` 路由端到端测试。

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


async def _register_and_login(
    client: AsyncClient, username: str, password: str = "password123"
) -> tuple[int, str]:
    """注册并登录一个用户，返回 ``(user_id, access_token)``。

    管理员相关测试依赖一个事实：每个 ``auth_client`` fixture 都使用独立的内存 DB，
    因此每个测试中"第一个注册的用户"会被 :func:`user_service.create_user` 自动设为
    ``admin`` 角色，后续注册的均为 ``user``。

    Args:
        client: 测试客户端。
        username: 用户名。
        password: 明文密码。

    Returns:
        tuple[int, str]: 新用户 ID 与访问令牌。
    """
    reg = await client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": password},
    )
    assert reg.status_code == 201, reg.text
    user_id = int(reg.json()["id"])
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": username, "password": password},
    )
    assert login.status_code == 200, login.text
    return user_id, login.json()["access_token"]


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


async def test_admin_list_users(auth_client: AsyncClient) -> None:
    """admin 调 ``GET /admin/users`` 应返回 200 且包含全部用户；普通用户返回 403。"""
    _, admin_token = await _register_and_login(auth_client, "admin1")
    _, user_token = await _register_and_login(auth_client, "user1")

    forbidden = await auth_client.get(
        "/api/v1/admin/users",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert forbidden.status_code == 403, forbidden.text

    ok = await auth_client.get(
        "/api/v1/admin/users",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert ok.status_code == 200, ok.text
    users = ok.json()
    assert isinstance(users, list)
    usernames = {u["username"] for u in users}
    assert {"admin1", "user1"}.issubset(usernames)


async def test_admin_update_role(auth_client: AsyncClient) -> None:
    """admin 把另一个 user 提升为 admin 应成功；改自己角色应返回 403。"""
    admin_id, admin_token = await _register_and_login(auth_client, "admin2")
    target_id, _ = await _register_and_login(auth_client, "promotee")

    promote = await auth_client.patch(
        f"/api/v1/admin/users/{target_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"role": "admin"},
    )
    assert promote.status_code == 200, promote.text
    assert promote.json()["role"] == "admin"

    self_modify = await auth_client.patch(
        f"/api/v1/admin/users/{admin_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"role": "user"},
    )
    assert self_modify.status_code == 403, self_modify.text


async def test_admin_delete_user(auth_client: AsyncClient) -> None:
    """admin 删除另一个 user 应成功；删除自己应返回 403。"""
    admin_id, admin_token = await _register_and_login(auth_client, "admin3")
    victim_id, _ = await _register_and_login(auth_client, "victim")

    ok = await auth_client.delete(
        f"/api/v1/admin/users/{victim_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert ok.status_code == 200, ok.text
    assert "detail" in ok.json()

    self_delete = await auth_client.delete(
        f"/api/v1/admin/users/{admin_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert self_delete.status_code == 403, self_delete.text


async def test_admin_reset_password(auth_client: AsyncClient) -> None:
    """admin 重置普通用户密码应返回非空 ``temp_password`` 且可用于登录。"""
    _, admin_token = await _register_and_login(auth_client, "admin4")
    target_id, _ = await _register_and_login(auth_client, "needs_reset", password="original123")

    reset = await auth_client.post(
        f"/api/v1/admin/users/{target_id}/reset-password",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert reset.status_code == 200, reset.text
    body = reset.json()
    temp_password = body.get("temp_password")
    assert isinstance(temp_password, str) and temp_password, body
    assert body["user_id"] == target_id

    relogin = await auth_client.post(
        "/api/v1/auth/login",
        data={"username": "needs_reset", "password": temp_password},
    )
    assert relogin.status_code == 200, relogin.text
