"""索引评测服务：Recall、QPS、延迟分位数与内存占用等指标计算。

注：检索日志聚合统计（``GET /stats/search`` 后端逻辑）位于
:mod:`app.services.stats`，本模块只关心 ANN 索引基准评测。
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import Any

import numpy as np

from app.core.logging import get_logger
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
        raise ValueError(f"approx/ground_truth 查询数不一致: {approx.shape[0]} vs {truth.shape[0]}")
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
