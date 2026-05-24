"""``/api/v1/stats/search`` 路由与统计服务测试。

测试夹具与 :mod:`tests.test_datasets` 保持一致风格：使用 in-memory ``aiosqlite``
并通过 ``app.dependency_overrides[get_db]`` 注入测试会话，避免依赖真实 PostgreSQL。

覆盖：
    - 无日志时 ``total_queries=0``、``by_dataset=[]``、``hourly_24h`` 长度 24；
    - 插入多条 :class:`SearchLog` 后聚合数值（total / avg / p95 / by_dataset）
      与 numpy 的 ``np.percentile`` 期望一致；
    - F13 ``/stats/search-logs/export`` 的 CSV / JSON / 权限过滤路径。
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  # 触发 ORM 注册
from app.api.deps import get_db
from app.db.base import Base
from app.main import app
from app.models.dataset import Dataset
from app.models.search_log import SearchLog
from app.services.stats import EXPORT_CSV_FIELDS

TEST_DSN = "sqlite+aiosqlite:///:memory:"
_test_engine = create_async_engine(
    TEST_DSN,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=_test_engine,
    expire_on_commit=False,
    class_=AsyncSession,
    autoflush=False,
    autocommit=False,
)


async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
    """测试用 ``get_db``：使用本文件内的 SQLite 会话。"""
    async with _TestSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def setup_db() -> AsyncGenerator[None, None]:
    """每个测试创建干净的内存库，并切换 ``get_db`` 到测试库。"""
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_db, None)
        async with _test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def http_client(setup_db: None) -> AsyncGenerator[AsyncClient, None]:
    """绑定到测试库的 httpx 异步客户端。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


async def _login(client: AsyncClient, username: str, password: str) -> tuple[dict[str, str], int]:
    """注册并登录指定用户，返回 ``Authorization`` 头与用户 ID。"""
    resp = await client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 201, resp.text
    user_id = int(resp.json()["id"])

    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": username, "password": password},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}, user_id


async def test_search_stats_empty(http_client: AsyncClient) -> None:
    """无日志时返回零值聚合，hourly_24h 长度恒为 24 且全部为 0。"""
    headers, _ = await _login(http_client, "stats_empty", "passw0rd")

    resp = await http_client.get("/api/v1/stats/search", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["total_queries"] == 0
    assert body["overall_avg_latency_ms"] == 0.0
    assert body["overall_p95_latency_ms"] == 0.0
    assert body["by_dataset"] == []

    assert len(body["hourly_24h"]) == 24
    for bucket in body["hourly_24h"]:
        assert bucket["queries"] == 0
        assert bucket["avg_latency_ms"] == 0.0
        assert bucket["hour_iso"].endswith("Z")


async def test_search_stats_aggregation(http_client: AsyncClient) -> None:
    """插入多条 SearchLog 后聚合数值应与 numpy 计算一致。"""
    headers, user_id = await _login(http_client, "stats_agg", "passw0rd")

    ds_a_latencies = [10.0, 20.0, 30.0, 100.0]
    ds_b_latencies = [50.0]
    all_latencies = ds_a_latencies + ds_b_latencies

    now = datetime.now(tz=UTC)
    async with _TestSessionLocal() as session:
        ds_a = Dataset(owner_id=user_id, name="ds-a", h5ad_path="/tmp/a.h5ad", status="ready")
        ds_b = Dataset(owner_id=user_id, name="ds-b", h5ad_path="/tmp/b.h5ad", status="ready")
        session.add_all([ds_a, ds_b])
        await session.flush()
        ds_a_id = int(ds_a.id)
        ds_b_id = int(ds_b.id)

        for latency in ds_a_latencies:
            session.add(
                SearchLog(
                    dataset_id=ds_a_id,
                    user_id=user_id,
                    top_k=10,
                    filters=None,
                    latency_ms=latency,
                    created_at=now - timedelta(minutes=30),
                )
            )
        session.add(
            SearchLog(
                dataset_id=ds_b_id,
                user_id=user_id,
                top_k=5,
                filters=None,
                latency_ms=ds_b_latencies[0],
                created_at=now - timedelta(minutes=10),
            )
        )
        await session.commit()

    resp = await http_client.get("/api/v1/stats/search", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["total_queries"] == len(all_latencies)
    assert body["overall_avg_latency_ms"] == pytest.approx(float(np.mean(all_latencies)))
    assert body["overall_p95_latency_ms"] == pytest.approx(float(np.percentile(all_latencies, 95)))

    assert len(body["by_dataset"]) == 2
    by_id = {item["dataset_id"]: item for item in body["by_dataset"]}
    a_stat = by_id[ds_a_id]
    assert a_stat["dataset_name"] == "ds-a"
    assert a_stat["total_queries"] == len(ds_a_latencies)
    assert a_stat["avg_latency_ms"] == pytest.approx(float(np.mean(ds_a_latencies)))
    assert a_stat["p95_latency_ms"] == pytest.approx(float(np.percentile(ds_a_latencies, 95)))

    b_stat = by_id[ds_b_id]
    assert b_stat["dataset_name"] == "ds-b"
    assert b_stat["total_queries"] == len(ds_b_latencies)
    assert b_stat["avg_latency_ms"] == pytest.approx(ds_b_latencies[0])
    assert b_stat["p95_latency_ms"] == pytest.approx(ds_b_latencies[0])

    assert len(body["hourly_24h"]) == 24
    total_in_buckets = sum(b["queries"] for b in body["hourly_24h"])
    assert total_in_buckets == len(all_latencies)
    current_bucket = body["hourly_24h"][-1]
    assert current_bucket["queries"] == len(all_latencies)
    assert current_bucket["avg_latency_ms"] == pytest.approx(float(np.mean(all_latencies)))


async def test_search_stats_isolates_users(http_client: AsyncClient) -> None:
    """B 用户应当看不到 A 用户的检索日志。"""
    headers_a, user_a = await _login(http_client, "stats_user_a", "passw0rd")
    headers_b, _ = await _login(http_client, "stats_user_b", "passw0rd")

    now = datetime.now(tz=UTC)
    async with _TestSessionLocal() as session:
        ds = Dataset(owner_id=user_a, name="solo", h5ad_path="/tmp/solo.h5ad", status="ready")
        session.add(ds)
        await session.flush()
        session.add(
            SearchLog(
                dataset_id=int(ds.id),
                user_id=user_a,
                top_k=10,
                filters=None,
                latency_ms=42.0,
                created_at=now - timedelta(minutes=5),
            )
        )
        await session.commit()

    resp = await http_client.get("/api/v1/stats/search", headers=headers_a)
    assert resp.status_code == 200
    assert resp.json()["total_queries"] == 1

    resp = await http_client.get("/api/v1/stats/search", headers=headers_b)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_queries"] == 0
    assert body["by_dataset"] == []


async def _seed_dataset_and_logs(
    user_id: int, count: int, *, dataset_name: str = "export-ds"
) -> tuple[int, list[int]]:
    """为指定用户造一个数据集 + ``count`` 条 SearchLog，返回 ``(dataset_id, log_ids)``。"""
    log_ids: list[int] = []
    base = datetime.now(tz=UTC) - timedelta(minutes=count)
    async with _TestSessionLocal() as session:
        ds = Dataset(
            owner_id=user_id,
            name=dataset_name,
            h5ad_path=f"/tmp/{dataset_name}.h5ad",
            status="ready",
        )
        session.add(ds)
        await session.flush()
        ds_id = int(ds.id)
        for offset in range(count):
            log = SearchLog(
                dataset_id=ds_id,
                user_id=user_id,
                top_k=10,
                filters=None,
                latency_ms=float(offset + 1) * 5.0,
                created_at=base + timedelta(minutes=offset),
            )
            session.add(log)
            await session.flush()
            log_ids.append(int(log.id))
        await session.commit()
    return ds_id, log_ids


async def test_export_search_logs_csv_default(http_client: AsyncClient) -> None:
    """默认 ``format=csv``：返回 ``text/csv`` 流，首行为固定表头且数据行数匹配。"""
    headers, user_id = await _login(http_client, "export_csv", "passw0rd")
    _, log_ids = await _seed_dataset_and_logs(user_id, count=3)

    resp = await http_client.get("/api/v1/stats/search-logs/export", headers=headers)
    assert resp.status_code == 200, resp.text
    assert "text/csv" in resp.headers["content-type"].lower()

    reader = csv.reader(io.StringIO(resp.text))
    rows = list(reader)
    assert rows[0] == list(EXPORT_CSV_FIELDS)
    data_rows = rows[1:]
    assert len(data_rows) == len(log_ids)
    id_col = EXPORT_CSV_FIELDS.index("id")
    user_col = EXPORT_CSV_FIELDS.index("user_id")
    assert [int(r[id_col]) for r in data_rows] == sorted(log_ids)
    assert all(int(r[user_col]) == user_id for r in data_rows)


async def test_export_search_logs_json_format(http_client: AsyncClient) -> None:
    """``format=json`` 返回 ``{items, total, truncated}`` 结构，items 数量与种子数据一致。"""
    headers, user_id = await _login(http_client, "export_json", "passw0rd")
    _, log_ids = await _seed_dataset_and_logs(user_id, count=4)

    resp = await http_client.get(
        "/api/v1/stats/search-logs/export",
        params={"format": "json"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/json")

    body = json.loads(resp.text)
    assert set(body.keys()) >= {"items", "total", "truncated"}
    assert body["total"] == len(log_ids)
    assert body["truncated"] is False
    assert len(body["items"]) == len(log_ids)
    assert all(item["user_id"] == user_id for item in body["items"])
    assert [item["id"] for item in body["items"]] == sorted(log_ids)


async def test_export_search_logs_admin_sees_all_users(http_client: AsyncClient) -> None:
    """admin（首个注册者）可以看到全部用户日志；普通用户只能看到自己。"""
    headers_admin, admin_id = await _login(http_client, "export_admin", "passw0rd")
    headers_user, normal_id = await _login(http_client, "export_user", "passw0rd")

    _, admin_log_ids = await _seed_dataset_and_logs(admin_id, count=2, dataset_name="admin-ds")
    _, user_log_ids = await _seed_dataset_and_logs(normal_id, count=3, dataset_name="user-ds")

    resp = await http_client.get(
        "/api/v1/stats/search-logs/export",
        params={"format": "json"},
        headers=headers_admin,
    )
    assert resp.status_code == 200, resp.text
    admin_body = json.loads(resp.text)
    admin_user_ids = {item["user_id"] for item in admin_body["items"]}
    assert admin_user_ids == {admin_id, normal_id}
    assert admin_body["total"] == len(admin_log_ids) + len(user_log_ids)

    resp = await http_client.get(
        "/api/v1/stats/search-logs/export",
        params={"format": "json"},
        headers=headers_user,
    )
    assert resp.status_code == 200, resp.text
    user_body = json.loads(resp.text)
    assert user_body["total"] == len(user_log_ids)
    assert {item["user_id"] for item in user_body["items"]} == {normal_id}
