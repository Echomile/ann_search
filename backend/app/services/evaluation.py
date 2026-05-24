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


# ---------------------------------------------------------------------------
# v1.2 扩展功能 C3: ANN-Benchmarks 风格 recall-QPS 参数扫描
# ---------------------------------------------------------------------------


def _mark_pareto(points: list[tuple[float, float]]) -> list[bool]:
    """标记每个 ``(recall, qps)`` 点是否在帕累托前沿（双目标均越大越好）。

    支配关系：``j`` 支配 ``i`` ⟺ ``recall_j >= recall_i`` 且 ``qps_j >= qps_i``，
    且至少一项严格大于。被任意点支配的 ``i`` 不在前沿。

    Args:
        points: ``[(recall, qps), ...]``，长度 ``n``。

    Returns:
        list[bool]: 长度 ``n`` 的标记数组，``True`` 表示在帕累托前沿。
    """
    n = len(points)
    on_pareto = [True] * n
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            r_i, q_i = points[i]
            r_j, q_j = points[j]
            if r_j >= r_i and q_j >= q_i and (r_j > r_i or q_j > q_i):
                on_pareto[i] = False
                break
    return on_pareto


def _evaluate_single_point(
    backend: Any,
    queries: np.ndarray,
    truth_indices: np.ndarray,
    top_k: int,
    concurrency: int = 1,
) -> dict[str, float]:
    """对单一参数组合下的已构建 ``backend`` 进行评测。

    复用 :func:`compute_recall` 算 Recall@K，复用 :func:`_stress_test` 算延迟分位数与 QPS；
    内存占用调用 ``backend.memory_mb()``，异常时回退到 ``0.0``。

    Args:
        backend: 已构建索引且已应用查询期参数的后端实例。
        queries: 查询向量，``(num_queries, D)``。
        truth_indices: ground truth 索引，``(num_queries, k')``，``k' >= top_k``。
        top_k: 截断的 Top-K，用于 recall 与延迟测算。
        concurrency: 并发档位，默认 ``1``（单线程）。

    Returns:
        dict[str, float]: ``{"recall", "qps", "p50_ms", "p95_ms", "p99_ms", "mem_mb"}``。
    """
    approx_indices, _ = backend.search(queries, top_k)
    recall = compute_recall(np.asarray(approx_indices), truth_indices, top_k)
    stats = _stress_test(backend, queries, top_k=top_k, concurrency=int(concurrency))
    try:
        mem_mb = float(backend.memory_mb())
    except Exception:  # noqa: BLE001
        mem_mb = 0.0
    return {
        "recall": float(recall),
        "qps": float(stats["qps"]),
        "p50_ms": float(stats["p50_ms"]),
        "p95_ms": float(stats["p95_ms"]),
        "p99_ms": float(stats["p99_ms"]),
        "mem_mb": mem_mb,
    }


def _apply_query_param(backend: Any, backend_name: str, params: dict[str, Any]) -> None:
    """将查询期参数应用到已构建的后端实例上。

    - ``hnswlib`` / ``adaptive-hnsw``：调用 ``set_ef`` 更新 ``ef_search``。
    - ``faiss-hnsw``：通过 ``index.hnsw.efSearch`` 直接修改。
    - ``faiss-ivfpq``：通过 ``index.nprobe`` 直接修改。
    - ``brute``：忽略，无参数。

    Args:
        backend: 后端实例。
        backend_name: 后端名（区分 hnswlib / faiss-hnsw 等）。
        params: 形如 ``{"ef_search": 64}`` 或 ``{"nprobe": 16}``。
    """
    if backend_name in {"hnswlib", "adaptive-hnsw"}:
        ef = params.get("ef_search")
        if ef is not None and hasattr(backend, "set_ef"):
            backend.set_ef(int(ef))
    elif backend_name == "faiss-hnsw":
        ef = params.get("ef_search")
        if ef is not None and getattr(backend, "_index", None) is not None:
            backend._index.hnsw.efSearch = int(ef)  # noqa: SLF001
    elif backend_name == "faiss-ivfpq":
        nprobe = params.get("nprobe")
        if nprobe is not None and getattr(backend, "_index", None) is not None:
            backend._index.nprobe = int(nprobe)  # noqa: SLF001


def _build_backend_for_sweep(
    backend_name: str,
    vectors: np.ndarray,
    metric: str,
) -> Any:
    """根据后端名构造并 ``build`` 一次后端，供后续参数复用同一索引。

    针对 ``faiss-ivfpq``：当数据规模较小时按 ``min(64, N // 4)`` 启发式调小 ``nlist``，
    避免 ``IndexIVFPQ.train`` 报 ``N >= nlist`` 失败。

    Args:
        backend_name: 后端名。
        vectors: 训练向量，``(N, D)``。
        metric: 距离度量。

    Returns:
        Any: 已构建的 :class:`IndexBackend` 实例。
    """
    from app.services.ann.factory import create_backend

    backend = create_backend(backend_name, dim=int(vectors.shape[1]), metric=metric)
    n = int(vectors.shape[0])
    build_kwargs: dict[str, Any] = {}
    if backend_name == "faiss-ivfpq":
        # nlist 启发式: sqrt(N), 限制在 [8, 4096], 且不超过 N // 4 避免 IVF train 失败
        nlist_target = int(max(8, min(4096, n**0.5)))
        nlist = max(1, min(nlist_target, max(1, n // 4)))
        m = _pick_pq_m(int(vectors.shape[1]))
        build_kwargs.update({"nlist": nlist, "m": m, "nbits": 8})
    backend.build(vectors, **build_kwargs)
    return backend


def _pick_pq_m(dim: int) -> int:
    """为 ``faiss-ivfpq`` 挑选一个能整除 ``dim`` 的 ``m``，默认 8 不行就回退。"""
    for candidate in (8, 4, 2, 1):
        if dim % candidate == 0:
            return candidate
    return 1


def _params_grid_for_backend(
    backend_name: str,
    ef_search_grid: list[int],
    nprobe_grid: list[int],
) -> list[dict[str, Any]]:
    """根据后端名返回参数栅格列表。"""
    if backend_name in {"hnswlib", "faiss-hnsw", "adaptive-hnsw"}:
        return [{"ef_search": int(v)} for v in ef_search_grid]
    if backend_name == "faiss-ivfpq":
        return [{"nprobe": int(v)} for v in nprobe_grid]
    return [{}]


async def param_sweep(
    session: Any,
    dataset_id: int,
    backends: list[str],
    top_k: int = 10,
    query_count: int = 200,
    ef_search_grid: list[int] | None = None,
    nprobe_grid: list[int] | None = None,
    user_id: int | None = None,
) -> int:
    """对一个数据集做参数扫描，落库 :class:`SweepRun` + 多条 :class:`SweepPoint`。

    扫描栅格：

    - ``hnswlib`` / ``faiss-hnsw`` / ``adaptive-hnsw``：``ef_search_grid``，缺省
      ``[16, 32, 64, 128, 256, 512]``。
    - ``faiss-ivfpq``：``nprobe_grid``，缺省 ``[4, 8, 16, 32, 64, 128]``。
    - ``brute``：单点（无查询期参数，recall=1.0）。

    流程：

    1. 加载数据集 ``vectors.npy``；
    2. 固定 seed 抽 ``query_count`` 条查询；
    3. 构造一次 :class:`BruteBackend` 作为 ground truth；
    4. 对每个 backend 先 ``build`` 一次，多个查询期参数复用同一索引；
    5. 收集 ``(recall, qps, p50_ms, p95_ms, p99_ms, mem_mb)``；
    6. 调用 :func:`_mark_pareto` 标记前沿；
    7. 写入 ``sweep_runs`` + ``sweep_points``，状态 ``done``。

    Args:
        session: ``AsyncSession``，由调用方提供。
        dataset_id: 目标数据集 ID。
        backends: 后端名列表。
        top_k: 评测 Top-K。
        query_count: 查询样本数。
        ef_search_grid: HNSW 系扫描栅格。
        nprobe_grid: IVF-PQ 扫描栅格。
        user_id: 触发者用户 ID。

    Returns:
        int: 新建的 ``sweep_runs.id``。

    Raises:
        ValueError: 数据集不存在或缺少向量文件、或 backend 名非法。
    """
    from datetime import UTC, datetime

    from app.models.dataset import Dataset
    from app.models.sweep import SweepPoint, SweepRun
    from app.services.ann.factory import list_backends

    valid_backends = set(list_backends())
    invalid = [b for b in backends if b not in valid_backends]
    if invalid:
        raise ValueError(f"未知 ANN 后端: {invalid}; 可选: {sorted(valid_backends)}")

    ef_grid = list(ef_search_grid) if ef_search_grid else [16, 32, 64, 128, 256, 512]
    nprobe_grid_final = list(nprobe_grid) if nprobe_grid else [4, 8, 16, 32, 64, 128]

    dataset = await session.get(Dataset, dataset_id)
    if dataset is None:
        raise ValueError(f"数据集不存在: {dataset_id}")
    if not dataset.vectors_path:
        raise ValueError(f"数据集 {dataset_id} 缺少 vectors_path，无法扫描")

    vectors_path = dataset.vectors_path
    import os as _os

    if _os.path.isdir(vectors_path):
        vectors_file = _os.path.join(vectors_path, "vectors.npy")
    else:
        vectors_file = vectors_path
    if not _os.path.isfile(vectors_file):
        raise ValueError(f"数据集 {dataset_id} 向量文件不存在: {vectors_file}")

    vectors = np.load(vectors_file).astype(np.float32, copy=False)
    n, dim = int(vectors.shape[0]), int(vectors.shape[1])
    metric = "l2"

    rng = np.random.default_rng(42)
    sample_size = min(int(query_count), n)
    query_idx = rng.choice(n, size=sample_size, replace=False)
    queries = np.ascontiguousarray(vectors[query_idx], dtype=np.float32)

    gt_backend = BruteBackend(dim=dim, metric=metric)
    gt_backend.build(vectors)
    truth_indices, _ = gt_backend.search(queries, max(top_k, 1))
    truth_indices = np.asarray(truth_indices)

    run = SweepRun(
        dataset_id=int(dataset_id),
        created_by=user_id,
        status="running",
        top_k=int(top_k),
        query_count=int(sample_size),
        started_at=datetime.now(tz=UTC),
    )
    session.add(run)
    # 第一次 commit 先把 status=running 的 run 落库，
    # 这样即便后续评测失败，rollback 也不会丢失 run 主键，便于异常分支单独写入 failed 状态。
    await session.commit()
    await session.refresh(run)
    run_id = int(run.id)
    logger.info(
        "param_sweep 启动 run_id=%s dataset_id=%s backends=%s n=%d dim=%d query_count=%d",
        run_id,
        dataset_id,
        backends,
        n,
        dim,
        sample_size,
    )

    collected: list[tuple[SweepPoint, float, float]] = []
    try:
        for backend_name in backends:
            if backend_name == "brute":
                # ground truth 已经构造好，直接复用
                metrics = _evaluate_single_point(gt_backend, queries, truth_indices, top_k=top_k)
                point = SweepPoint(
                    sweep_run_id=run_id,
                    backend=backend_name,
                    params_json={},
                    recall=metrics["recall"],
                    qps=metrics["qps"],
                    p50_ms=metrics["p50_ms"],
                    p95_ms=metrics["p95_ms"],
                    p99_ms=metrics["p99_ms"],
                    mem_mb=metrics["mem_mb"],
                    on_pareto=False,
                )
                collected.append((point, metrics["recall"], metrics["qps"]))
                continue

            backend = _build_backend_for_sweep(backend_name, vectors, metric)
            grid = _params_grid_for_backend(backend_name, ef_grid, nprobe_grid_final)
            for params in grid:
                _apply_query_param(backend, backend_name, params)
                metrics = _evaluate_single_point(backend, queries, truth_indices, top_k=top_k)
                point = SweepPoint(
                    sweep_run_id=run_id,
                    backend=backend_name,
                    params_json=dict(params),
                    recall=metrics["recall"],
                    qps=metrics["qps"],
                    p50_ms=metrics["p50_ms"],
                    p95_ms=metrics["p95_ms"],
                    p99_ms=metrics["p99_ms"],
                    mem_mb=metrics["mem_mb"],
                    on_pareto=False,
                )
                collected.append((point, metrics["recall"], metrics["qps"]))

        pareto_flags = _mark_pareto([(r, q) for _, r, q in collected])
        for (point, _, _), flag in zip(collected, pareto_flags, strict=True):
            point.on_pareto = bool(flag)
            session.add(point)

        run.status = "done"
        run.finished_at = datetime.now(tz=UTC)
        run.error = None
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        run_fail = await session.get(SweepRun, run_id)
        if run_fail is not None:
            run_fail.status = "failed"
            run_fail.finished_at = datetime.now(tz=UTC)
            run_fail.error = str(exc)[:1000]
            await session.commit()
        logger.exception("param_sweep 失败 run_id=%s", run_id)
        raise

    pareto_count = sum(1 for flag in pareto_flags if flag)
    logger.info(
        "param_sweep 完成 run_id=%s 共 %d 个数据点，pareto=%d",
        run_id,
        len(collected),
        pareto_count,
    )
    return run_id
