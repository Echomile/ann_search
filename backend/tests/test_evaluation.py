"""索引评测核心逻辑测试。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  确保所有模型注册到 metadata
from app.db.base import Base
from app.models.dataset import Dataset
from app.models.search_log import SearchLog
from app.models.user import User
from app.services.ann.brute_backend import BruteBackend
from app.services.evaluation import (
    benchmark_index,
    compute_recall,
)
from app.services.stats import compute_search_stats


def test_compute_recall_full_match() -> None:
    """approx == ground truth 时 Recall 应为 1.0。"""
    truth = np.array([[0, 1, 2, 3, 4], [5, 6, 7, 8, 9]])
    approx = truth.copy()
    assert compute_recall(approx, truth, k=5) == pytest.approx(1.0)


def test_compute_recall_zero_overlap() -> None:
    """完全无交集时 Recall 应为 0.0。"""
    truth = np.array([[0, 1, 2, 3, 4]])
    approx = np.array([[10, 11, 12, 13, 14]])
    assert compute_recall(approx, truth, k=5) == pytest.approx(0.0)


def test_compute_recall_partial_overlap_handcrafted() -> None:
    """手工构造交集大小，验证 Recall = 平均交集 / k。"""
    truth = np.array(
        [
            [0, 1, 2, 3, 4],
            [10, 11, 12, 13, 14],
        ]
    )
    approx = np.array(
        [
            [0, 1, 2, 99, 100],  # 交集 3
            [10, 11, 50, 51, 52],  # 交集 2
        ]
    )
    expected = (3 + 2) / (2 * 5)
    assert compute_recall(approx, truth, k=5) == pytest.approx(expected)


def test_compute_recall_truncates_k_when_arrays_shorter() -> None:
    """当 ``k`` 超过实际列数时应按可用宽度截断。"""
    truth = np.array([[0, 1, 2]])
    approx = np.array([[0, 1, 2]])
    assert compute_recall(approx, truth, k=10) == pytest.approx(1.0)


def test_compute_recall_handles_unordered_neighbors() -> None:
    """顺序不同但元素相同应判为完全匹配。"""
    truth = np.array([[0, 1, 2, 3, 4]])
    approx = np.array([[4, 3, 2, 1, 0]])
    assert compute_recall(approx, truth, k=5) == pytest.approx(1.0)


def test_benchmark_index_runs_end_to_end() -> None:
    """对 brute 后端自身评测时 Recall 必为 1.0，且各档位统计字段完整。"""
    rng = np.random.default_rng(11)
    vectors = rng.normal(size=(64, 6)).astype(np.float32)
    backend = BruteBackend(dim=6, metric="l2")
    backend.build(vectors)

    result = benchmark_index(
        backend=backend,
        vectors=vectors,
        index_id=42,
        dataset_id=7,
        metric="l2",
        num_queries=8,
        top_k_list=[5, 10],
        concurrency_list=[1, 2],
    )

    assert result["index_id"] == 42
    assert result["dataset_id"] == 7
    assert result["backend"] == "brute"
    assert set(result["recalls"].keys()) == {"5", "10"}
    for v in result["recalls"].values():
        assert v == pytest.approx(1.0)
    concurrencies = {entry["concurrency"] for entry in result["latencies"]}
    assert concurrencies == {1, 2}
    for entry in result["latencies"]:
        assert entry["p50_ms"] >= 0
        assert entry["p95_ms"] >= entry["p50_ms"]
        assert entry["p99_ms"] >= entry["p95_ms"]
        assert entry["qps"] >= 0.0
        assert entry["total_queries"] == 8


# ---------------------------------------------------------------------------
# SearchLog 统计聚合测试（SQLite 内存库，走 numpy 兜底分位数）
# ---------------------------------------------------------------------------

_STATS_DSN = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def stats_session() -> AsyncGenerator[AsyncSession, None]:
    """提供一个独立的内存 SQLite 会话，每个测试自动建表/拆表。"""
    engine = create_async_engine(
        _STATS_DSN,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_local = async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
        class_=AsyncSession,
        autoflush=False,
        autocommit=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with session_local() as session:
        try:
            yield session
        finally:
            await session.rollback()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _make_user(session: AsyncSession, username: str) -> User:
    """新建并刷新一个测试用户。"""
    user = User(username=username, password_hash="x")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _make_dataset(session: AsyncSession, owner_id: int, name: str) -> Dataset:
    """新建并刷新一个测试数据集。"""
    ds = Dataset(
        owner_id=owner_id,
        name=name,
        h5ad_path=f"/tmp/{name}.h5ad",
        status="ready",
    )
    session.add(ds)
    await session.commit()
    await session.refresh(ds)
    return ds


async def test_search_log_stats_empty(stats_session: AsyncSession) -> None:
    """新用户无日志时，total=0、percentiles=0.0、24 个桶全 0 且 hour_iso 带 Z。"""
    user = await _make_user(stats_session, "empty_user")
    user_id = int(user.id)

    result = await compute_search_stats(stats_session, user_id=user_id)

    assert result["total_queries"] == 0
    assert result["overall_avg_latency_ms"] == 0.0
    assert result["overall_p95_latency_ms"] == 0.0
    assert result["by_dataset"] == []
    assert len(result["hourly_24h"]) == 24
    for bucket in result["hourly_24h"]:
        assert bucket["queries"] == 0
        assert bucket["avg_latency_ms"] == 0.0
        assert bucket["hour_iso"].endswith("Z")


async def test_search_log_stats_with_data(stats_session: AsyncSession) -> None:
    """插入 3 条日志验证聚合结果、按数据集分组与滚动桶逻辑。"""
    user = await _make_user(stats_session, "logger_user")
    user_id = int(user.id)
    ds_a = await _make_dataset(stats_session, user_id, "ds_a")
    ds_b = await _make_dataset(stats_session, user_id, "ds_b")
    # numpy 兜底路径会 rollback，提前缓存 PK 避免随后访问触发懒加载
    ds_a_id, ds_b_id = int(ds_a.id), int(ds_b.id)

    now = datetime.now(tz=UTC)
    stats_session.add_all(
        [
            SearchLog(
                dataset_id=ds_a_id,
                user_id=user_id,
                top_k=10,
                filters=None,
                latency_ms=10.0,
                created_at=now,
            ),
            SearchLog(
                dataset_id=ds_a_id,
                user_id=user_id,
                top_k=10,
                filters={"cell_type": "T"},
                latency_ms=20.0,
                created_at=now - timedelta(minutes=5),
            ),
            SearchLog(
                dataset_id=ds_b_id,
                user_id=user_id,
                top_k=20,
                filters=None,
                latency_ms=120.0,
                created_at=now - timedelta(hours=2),
            ),
        ]
    )
    await stats_session.commit()

    result = await compute_search_stats(stats_session, user_id=user_id)

    assert result["total_queries"] == 3
    assert result["overall_avg_latency_ms"] == pytest.approx((10.0 + 20.0 + 120.0) / 3)
    assert result["overall_p95_latency_ms"] == pytest.approx(
        float(np.percentile([10.0, 20.0, 120.0], 95))
    )

    by_ds = {b["dataset_id"]: b for b in result["by_dataset"]}
    assert by_ds[ds_a_id]["total_queries"] == 2
    assert by_ds[ds_a_id]["dataset_name"] == "ds_a"
    assert by_ds[ds_a_id]["avg_latency_ms"] == pytest.approx(15.0)
    assert by_ds[ds_a_id]["p95_latency_ms"] == pytest.approx(float(np.percentile([10.0, 20.0], 95)))
    assert by_ds[ds_b_id]["total_queries"] == 1
    assert by_ds[ds_b_id]["dataset_name"] == "ds_b"
    assert by_ds[ds_b_id]["avg_latency_ms"] == pytest.approx(120.0)
    assert by_ds[ds_b_id]["p95_latency_ms"] == pytest.approx(120.0)

    assert len(result["hourly_24h"]) == 24
    total_in_buckets = sum(b["queries"] for b in result["hourly_24h"])
    assert total_in_buckets == 3
    # 当前桶覆盖 [now-1h, now]，应包含 now 和 now-5min 两条
    assert result["hourly_24h"][-1]["queries"] == 2

    only_a = await compute_search_stats(stats_session, user_id=user_id, dataset_id=ds_a_id)
    assert only_a["total_queries"] == 2
    assert {b["dataset_id"] for b in only_a["by_dataset"]} == {ds_a_id}
