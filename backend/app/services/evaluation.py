"""索引评测服务：Recall、QPS、延迟分位数与内存占用等指标计算。"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.dataset import Dataset
from app.models.search_log import SearchLog
from app.services.ann.brute_backend import BruteBackend

logger = get_logger(__name__)


def compute_recall(
    approx_indices: np.ndarray,
    ground_truth_indices: np.ndarray,
    k: int,
) -> float:
    """计算 Recall@k。

    对每条查询，计算近似检索结果与 ground truth 在前 ``k`` 个邻居上的交集大小，
    再除以 ``k`` 求平均。

    Args:
        approx_indices: 近似检索结果索引，形状 ``(num_queries, k')``，``k' >= k``。
        ground_truth_indices: 真实 Top-k 索引，形状 ``(num_queries, k'')``，``k'' >= k``。
        k: 截断的 Top-K。

    Returns:
        float: 平均 Recall@k，范围 ``[0, 1]``。
    """
    if k <= 0:
        return 0.0
    approx = np.asarray(approx_indices)
    truth = np.asarray(ground_truth_indices)
    if approx.ndim == 1:
        approx = approx[None, :]
    if truth.ndim == 1:
        truth = truth[None, :]
    if approx.shape[0] != truth.shape[0]:
        raise ValueError(
            f"approx/ground_truth 查询数不一致: {approx.shape[0]} vs {truth.shape[0]}"
        )
    k_eff = min(k, approx.shape[1], truth.shape[1])
    if k_eff <= 0:
        return 0.0
    hits = 0
    num_queries = approx.shape[0]
    for i in range(num_queries):
        approx_set = {int(x) for x in approx[i, :k_eff]}
        truth_set = {int(x) for x in truth[i, :k_eff]}
        hits += len(approx_set & truth_set)
    return float(hits) / float(num_queries * k_eff)


def _percentile(values: list[float], pct: float) -> float:
    """计算给定百分位数（毫秒）。``values`` 为空时返回 ``0.0``。"""
    if not values:
        return 0.0
    return float(np.percentile(np.asarray(values, dtype=np.float64), pct))


def _stress_test(
    backend: Any,
    queries: np.ndarray,
    top_k: int,
    concurrency: int,
) -> dict[str, float]:
    """在给定并发档位下执行单点检索压测。

    每条查询单独提交到线程池，统计每次调用的端到端延迟。

    Args:
        backend: 已构建索引的后端。
        queries: 查询向量，形状 ``(num_queries, D)``。
        top_k: 每次检索的 Top-K。
        concurrency: 线程池并发数。

    Returns:
        dict[str, float]: ``{"p50_ms", "p95_ms", "p99_ms", "mean_ms", "qps", "total_queries"}``。
    """
    num_queries = int(queries.shape[0])
    if num_queries == 0:
        return {
            "p50_ms": 0.0,
            "p95_ms": 0.0,
            "p99_ms": 0.0,
            "mean_ms": 0.0,
            "qps": 0.0,
            "total_queries": 0,
        }
    latencies: list[float] = []

    def _one(idx: int) -> float:
        t0 = time.perf_counter()
        backend.search(queries[idx : idx + 1], top_k)
        return (time.perf_counter() - t0) * 1000.0

    wall_start = time.perf_counter()
    if concurrency <= 1:
        for i in range(num_queries):
            latencies.append(_one(i))
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(_one, i) for i in range(num_queries)]
            for fut in as_completed(futures):
                latencies.append(fut.result())
    wall_elapsed = time.perf_counter() - wall_start
    qps = num_queries / wall_elapsed if wall_elapsed > 0 else 0.0
    return {
        "p50_ms": _percentile(latencies, 50),
        "p95_ms": _percentile(latencies, 95),
        "p99_ms": _percentile(latencies, 99),
        "mean_ms": float(np.mean(latencies)) if latencies else 0.0,
        "qps": float(qps),
        "total_queries": num_queries,
    }


def benchmark_index(
    backend: Any,
    vectors: np.ndarray,
    *,
    index_id: int,
    dataset_id: int | None = None,
    metric: str = "l2",
    num_queries: int = 100,
    top_k_list: list[int] | None = None,
    concurrency_list: list[int] | None = None,
    build_time_seconds: float | None = None,
    memory_mb: float | None = None,
    ground_truth_backend: Any | None = None,
    seed: int = 42,
) -> dict[str, Any]:
    """对已构建的索引执行完整基准评测。

    评测流程：

    1. 从 ``vectors`` 中随机抽取 ``num_queries`` 行作为查询；
    2. 使用同一份向量构造 ``BruteBackend`` 作为 ground truth（除非由
       ``ground_truth_backend`` 传入已有的 brute 后端）；
    3. 对 ``top_k_list`` 的每个 K，计算 Recall@K；
    4. 对 ``concurrency_list`` 的每个并发档位，调用 :func:`_stress_test` 收集
       延迟分位数与 QPS。

    Args:
        backend: 已构建的待评测索引后端。
        vectors: 数据集底层向量，``(N, D)``。
        index_id: 索引 ID，用于回填响应。
        dataset_id: 数据集 ID，可选。
        metric: 度量名，用于响应。
        num_queries: 采样查询数。
        top_k_list: Recall 评测的 K 列表。
        concurrency_list: 并发压测档位列表。
        build_time_seconds: 索引构建耗时，由调用方提供。
        memory_mb: 索引内存占用；缺省时回退到 ``backend.memory_mb()``。
        ground_truth_backend: 若提供则跳过 brute 构造步骤。
        seed: 随机种子，保证可复现。

    Returns:
        dict[str, Any]: 见 :class:`app.schemas.evaluation.BenchmarkResult`。
    """
    top_k_list = sorted(set(top_k_list or [10, 100]))
    concurrency_list = sorted(set(concurrency_list or [1, 4, 8, 16]))
    rng = np.random.default_rng(seed)
    n = int(vectors.shape[0])
    sample_size = min(int(num_queries), n)
    query_idx = rng.choice(n, size=sample_size, replace=False)
    queries = vectors[query_idx].astype(np.float32, copy=False)

    max_k = max(top_k_list)
    max_k = min(max_k, n)

    if ground_truth_backend is None:
        logger.info("评测构造 brute ground truth backend，N=%d D=%d", n, vectors.shape[1])
        gt = BruteBackend(dim=int(vectors.shape[1]), metric=metric)
        gt.build(vectors)
    else:
        gt = ground_truth_backend
    truth_indices, _ = gt.search(queries, max_k)
    truth_indices = np.asarray(truth_indices)

    approx_indices, _ = backend.search(queries, max_k)
    approx_indices = np.asarray(approx_indices)

    recalls: dict[str, float] = {}
    for k in top_k_list:
        recalls[str(k)] = compute_recall(approx_indices, truth_indices, k)

    latencies: list[dict[str, float]] = []
    for c in concurrency_list:
        stats = _stress_test(backend, queries, top_k=top_k_list[0], concurrency=int(c))
        stats["concurrency"] = int(c)
        latencies.append(stats)

    mem = memory_mb
    if mem is None:
        try:
            mem = float(backend.memory_mb())
        except Exception:  # noqa: BLE001
            mem = None

    result: dict[str, Any] = {
        "index_id": int(index_id),
        "dataset_id": dataset_id,
        "backend": getattr(backend, "name", backend.__class__.__name__),
        "metric": metric,
        "build_time_seconds": build_time_seconds,
        "memory_mb": mem,
        "num_queries": sample_size,
        "recalls": recalls,
        "latencies": latencies,
        "finished_at": datetime.now(tz=UTC).isoformat(),
    }
    logger.info(
        "benchmark_index index_id=%s backend=%s recalls=%s",
        index_id,
        result["backend"],
        recalls,
    )
    return result


def _to_naive_utc(value: datetime) -> datetime:
    """统一时间表示：将 aware datetime 转为 UTC naive，已是 naive 则原样返回。"""
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


async def _percentile_via_sql_or_numpy(
    db: AsyncSession,
    *,
    base_filter: list[Any],
    percentile: float,
) -> float:
    """计算 ``latency_ms`` 在给定过滤条件下的某个分位数。

    优先尝试 PostgreSQL 原生 ``percentile_cont(p).within_group(...)``；
    若数据库不支持（典型情况 SQLite + aiosqlite），自动 rollback 后回退到
    拉取 latency 列再用 :func:`numpy.percentile` 计算。

    Args:
        db: 异步数据库会话。
        base_filter: ``SearchLog`` 上的过滤表达式列表。
        percentile: 分位数百分比，取值范围 0~100。

    Returns:
        float: 指定分位的延迟（毫秒），无数据返回 ``0.0``。
    """
    frac = float(percentile) / 100.0
    try:
        value = await db.scalar(
            select(
                func.percentile_cont(frac).within_group(SearchLog.latency_ms.asc())
            ).where(*base_filter, SearchLog.latency_ms.isnot(None))
        )
        return float(value) if value is not None else 0.0
    except Exception as exc:  # noqa: BLE001
        logger.debug("percentile_cont 不可用, fallback numpy: p=%s err=%s", percentile, exc)
        await db.rollback()

    latencies_raw = (
        await db.execute(
            select(SearchLog.latency_ms).where(
                *base_filter, SearchLog.latency_ms.isnot(None)
            )
        )
    ).scalars().all()
    latencies = [float(v) for v in latencies_raw if v is not None]
    if not latencies:
        return 0.0
    return float(np.percentile(np.asarray(latencies, dtype=np.float64), percentile))


async def compute_search_log_stats(
    db: AsyncSession,
    *,
    user_id: int,
    dataset_id: int | None = None,
) -> dict[str, Any]:
    """聚合指定用户的检索日志统计。

    通过 SQLAlchemy 异步聚合避免拉全表，关键指标计算如下：

    - ``total_queries``：``COUNT(*)``；
    - ``overall_avg_latency_ms``：``AVG(latency_ms)``；
    - ``overall_p95_latency_ms``：优先 PostgreSQL 原生 ``percentile_cont``，
      失败时回退到拉取 latency 列再用 :func:`numpy.percentile`（兼容 SQLite 测试环境）；
    - ``by_dataset``：按 ``dataset_id`` 分组，左连接 :class:`Dataset` 取名称，
      每个数据集再单独算 P95；
    - ``hourly_24h``：固定 24 个滚动 1 小时桶，``bucket[i] = [now - (24-i)h, now - (23-i)h]``，
      数组最后一个为 ``[now - 1h, now]``。无 ``date_trunc`` 依赖，跨数据库行为一致，
      也避免了"在 ``now.minute < 30`` 时 30min 前的事件落入前一整点桶"的语义不稳定。

    Args:
        db: 异步数据库会话。
        user_id: 当前用户 ID，用于过滤日志。
        dataset_id: 仅统计指定数据集，``None`` 表示全部。

    Returns:
        dict[str, Any]: 匹配 :class:`app.schemas.evaluation.SearchLogStats` 的字典。
    """
    base_filter: list[Any] = [SearchLog.user_id == user_id]
    if dataset_id is not None:
        base_filter.append(SearchLog.dataset_id == dataset_id)

    now = datetime.now(tz=UTC)
    day_ago = now - timedelta(days=1)

    total_queries = int(
        await db.scalar(select(func.count()).select_from(SearchLog).where(*base_filter)) or 0
    )

    avg_value = await db.scalar(select(func.avg(SearchLog.latency_ms)).where(*base_filter))
    overall_avg_latency_ms = float(avg_value) if avg_value is not None else 0.0

    overall_p95_latency_ms = (
        await _percentile_via_sql_or_numpy(db, base_filter=base_filter, percentile=95)
        if total_queries > 0
        else 0.0
    )

    by_dataset_rows = (
        await db.execute(
            select(
                SearchLog.dataset_id,
                Dataset.name,
                func.count(SearchLog.id).label("cnt"),
                func.avg(SearchLog.latency_ms).label("avg_lat"),
            )
            .join(Dataset, Dataset.id == SearchLog.dataset_id, isouter=True)
            .where(*base_filter)
            .group_by(SearchLog.dataset_id, Dataset.name)
            .order_by(func.count(SearchLog.id).desc())
        )
    ).all()

    by_dataset: list[dict[str, Any]] = []
    for row in by_dataset_rows:
        ds_id = int(row.dataset_id)
        per_filter = [*base_filter, SearchLog.dataset_id == ds_id]
        ds_p95 = await _percentile_via_sql_or_numpy(db, base_filter=per_filter, percentile=95)
        by_dataset.append(
            {
                "dataset_id": ds_id,
                "dataset_name": row.name or f"#{ds_id}",
                "total_queries": int(row.cnt),
                "avg_latency_ms": float(row.avg_lat) if row.avg_lat is not None else 0.0,
                "p95_latency_ms": ds_p95,
            }
        )

    naive_now = _to_naive_utc(now)
    bucket_starts: list[datetime] = [
        naive_now - timedelta(hours=24 - i) for i in range(24)
    ]
    bucket_counts = [0] * 24
    bucket_lats: list[list[float]] = [[] for _ in range(24)]

    raw_rows = (
        await db.execute(
            select(SearchLog.created_at, SearchLog.latency_ms).where(
                *base_filter, SearchLog.created_at >= day_ago
            )
        )
    ).all()
    for created_at, lat in raw_rows:
        if created_at is None or not isinstance(created_at, datetime):
            continue
        ts = _to_naive_utc(created_at)
        hours_ago = (naive_now - ts).total_seconds() / 3600.0
        if hours_ago < 0:
            hours_ago = 0.0
        if hours_ago >= 24:
            continue
        idx = 23 - int(hours_ago)
        idx = max(0, min(23, idx))
        bucket_counts[idx] += 1
        if lat is not None:
            bucket_lats[idx].append(float(lat))

    hourly_24h: list[dict[str, Any]] = []
    for i, start in enumerate(bucket_starts):
        lats = bucket_lats[i]
        avg = float(sum(lats) / len(lats)) if lats else 0.0
        hourly_24h.append(
            {
                "hour_iso": start.isoformat() + "Z",
                "queries": int(bucket_counts[i]),
                "avg_latency_ms": avg,
            }
        )

    return {
        "total_queries": total_queries,
        "overall_avg_latency_ms": overall_avg_latency_ms,
        "overall_p95_latency_ms": overall_p95_latency_ms,
        "by_dataset": by_dataset,
        "hourly_24h": hourly_24h,
    }
