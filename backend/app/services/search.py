"""检索服务：负责加载数据集制品、调用 ANN 后端并组装查询响应。

本模块的同步函数承担实际的向量化检索工作（CPU/numpy 密集型），
另提供 ``async`` 包装函数供 FastAPI 路由直接 ``await`` 使用，
内部通过 :func:`asyncio.to_thread` 卸载到默认线程池，避免阻塞事件循环。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from functools import lru_cache
from typing import Any

import numpy as np
import pandas as pd

from app.core.logging import get_logger

logger = get_logger(__name__)


@lru_cache(maxsize=32)
def load_dataset_artifacts(dataset_dir: str) -> dict[str, Any]:
    """加载数据集的预处理制品。

    约定 ``dataset_dir`` 下包含三个文件：

    - ``vectors.npy``：``(N, D)`` 形状的向量矩阵，必填；
    - ``cell_ids.json``：长度为 ``N`` 的 ``list[str]``，与向量一一对应；
    - ``metadata.parquet`` 或 ``metadata.csv``：每行对应一个细胞的元信息，可选。

    Args:
        dataset_dir: 数据集预处理目录绝对路径。

    Returns:
        dict[str, Any]: ``{"vectors": np.ndarray, "cell_ids": list[str],
        "metadata": pd.DataFrame, "cell_id_to_index": dict[str, int]}``。

    Raises:
        FileNotFoundError: 当目录或必备文件不存在时抛出。
        ValueError: 当向量与 cell_ids 长度不匹配时抛出。
    """
    if not os.path.isdir(dataset_dir):
        raise FileNotFoundError(f"数据集目录不存在: {dataset_dir}")

    vectors_path = os.path.join(dataset_dir, "vectors.npy")
    if not os.path.isfile(vectors_path):
        raise FileNotFoundError(f"缺少向量文件: {vectors_path}")
    vectors = np.load(vectors_path).astype(np.float32, copy=False)

    cell_ids_path = os.path.join(dataset_dir, "cell_ids.json")
    if not os.path.isfile(cell_ids_path):
        raise FileNotFoundError(f"缺少 cell_ids.json: {cell_ids_path}")
    with open(cell_ids_path, encoding="utf-8") as fp:
        cell_ids: list[str] = [str(c) for c in json.load(fp)]

    if len(cell_ids) != vectors.shape[0]:
        raise ValueError(f"cell_ids 数量 {len(cell_ids)} 与向量行数 {vectors.shape[0]} 不一致")

    metadata = _load_metadata(dataset_dir, expected_rows=len(cell_ids))
    cell_id_to_index = {cid: i for i, cid in enumerate(cell_ids)}

    logger.info(
        "加载数据集制品 dir=%s N=%d D=%d metadata_cols=%s",
        dataset_dir,
        vectors.shape[0],
        vectors.shape[1] if vectors.ndim > 1 else 0,
        list(metadata.columns) if metadata is not None else [],
    )
    return {
        "vectors": vectors,
        "cell_ids": cell_ids,
        "metadata": metadata,
        "cell_id_to_index": cell_id_to_index,
    }


@lru_cache(maxsize=32)
def _load_cell_ids(dataset_dir: str) -> tuple[str, ...]:
    """仅加载 ``cell_ids.json``（不触碰向量矩阵），供自动补全等轻量场景使用。

    Args:
        dataset_dir: 数据集制品目录。

    Returns:
        tuple[str, ...]: 全量 cell_id，按原始行序排列；文件缺失时返回空元组。
    """
    path = os.path.join(dataset_dir, "cell_ids.json")
    if not os.path.isfile(path):
        return ()
    with open(path, encoding="utf-8") as fp:
        return tuple(str(c) for c in json.load(fp))


def suggest_cell_ids(dataset_dir: str, query: str, limit: int = 20) -> list[str]:
    """按输入前缀/子串返回 cell_id 候选，用于前端输入框自动补全。

    匹配优先级：前缀命中优先于子串命中，二者均保持原始行序；大小写不敏感。
    ``query`` 为空时直接返回前 ``limit`` 个 cell_id 作为默认候选。

    Args:
        dataset_dir: 数据集制品目录。
        query: 用户已输入的片段。
        limit: 返回候选上限。

    Returns:
        list[str]: 去重后的候选 cell_id 列表，长度不超过 ``limit``。
    """
    ids = _load_cell_ids(dataset_dir)
    if not ids:
        return []
    if not query:
        return list(ids[:limit])
    q = query.lower()
    prefix: list[str] = []
    contains: list[str] = []
    for cid in ids:
        low = cid.lower()
        if low.startswith(q):
            prefix.append(cid)
            if len(prefix) >= limit:
                return prefix[:limit]
        elif q in low and len(contains) < limit:
            contains.append(cid)
    return (prefix + contains)[:limit]


def _load_metadata(dataset_dir: str, expected_rows: int) -> pd.DataFrame:
    """从数据集目录加载 ``metadata.parquet`` 或 ``metadata.csv``。

    Args:
        dataset_dir: 数据集目录。
        expected_rows: 期望的行数，主要用于校验。

    Returns:
        pd.DataFrame: 元数据表，若文件均不存在则返回空表。
    """
    parquet_path = os.path.join(dataset_dir, "metadata.parquet")
    csv_path = os.path.join(dataset_dir, "metadata.csv")
    df: pd.DataFrame | None = None
    if os.path.isfile(parquet_path):
        try:
            df = pd.read_parquet(parquet_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("读取 metadata.parquet 失败，回退 csv: %s", exc)
    if df is None and os.path.isfile(csv_path):
        df = pd.read_csv(csv_path)
    if df is None:
        return pd.DataFrame(index=range(expected_rows))
    if len(df) != expected_rows:
        logger.warning("metadata 行数 %d 与向量行数 %d 不一致", len(df), expected_rows)
    return df.reset_index(drop=True)


def clear_dataset_cache() -> None:
    """清空 :func:`load_dataset_artifacts` 的 LRU 缓存，主要供测试与热更新使用。"""
    load_dataset_artifacts.cache_clear()


def _apply_filters(
    metadata: pd.DataFrame,
    filters: dict[str, Any] | None,
) -> np.ndarray | None:
    """根据 metadata 过滤条件计算布尔掩码。

    支持的过滤值类型：

    - 标量：``equal`` 比较；
    - ``list`` / ``tuple`` / ``set``：``isin`` 比较；
    - ``dict``，含 ``in``、``gte``、``lte``、``gt``、``lt`` 等操作符。

    Args:
        metadata: 元数据表。
        filters: 过滤条件字典；为空返回 ``None`` 表示无过滤。

    Returns:
        np.ndarray | None: 与 metadata 等长的 ``bool`` 掩码，或 ``None``。
    """
    if not filters:
        return None
    if metadata is None or metadata.empty:
        return None
    mask = np.ones(len(metadata), dtype=bool)
    zero_mask = np.zeros(len(metadata), dtype=bool)
    for key, cond in filters.items():
        if key not in metadata.columns:
            logger.warning("过滤字段不存在于 metadata: %s", key)
            return zero_mask
        column = metadata[key]
        if isinstance(cond, (list, tuple, set)):
            mask &= column.isin(list(cond)).to_numpy()
        elif isinstance(cond, dict):
            for op, val in cond.items():
                if op == "in":
                    mask &= column.isin(list(val)).to_numpy()
                elif op == "gte":
                    mask &= (column >= val).to_numpy()
                elif op == "lte":
                    mask &= (column <= val).to_numpy()
                elif op == "gt":
                    mask &= (column > val).to_numpy()
                elif op == "lt":
                    mask &= (column < val).to_numpy()
                elif op in {"eq", "=="}:
                    mask &= (column == val).to_numpy()
                elif op in {"ne", "!="}:
                    mask &= (column != val).to_numpy()
                else:
                    logger.warning("不支持的过滤操作符: %s", op)
                    return zero_mask
        else:
            mask &= (column == cond).to_numpy()
    return mask


def _row_to_meta(metadata: pd.DataFrame, idx: int) -> dict[str, Any]:
    """将 metadata 第 ``idx`` 行转为 ``dict``，处理 NaN 值。"""
    if metadata is None or metadata.empty:
        return {}
    row = metadata.iloc[idx]
    out: dict[str, Any] = {}
    for col, val in row.items():
        if isinstance(val, float) and np.isnan(val):
            out[str(col)] = None
        elif hasattr(val, "item"):
            out[str(col)] = val.item()
        else:
            out[str(col)] = val
    return out


def search_with_backend(
    backend: Any,
    cell_ids: list[str],
    metadata: pd.DataFrame,
    query_vector: np.ndarray,
    top_k: int,
    filters: dict[str, Any] | None = None,
    exclude_indices: set[int] | None = None,
    over_fetch_factor: int = 5,
    metric: str | None = None,
) -> dict[str, Any]:
    """以指定后端执行一次检索的纯函数版本。

    过滤策略：默认采用 **post-filter** —— 先取 ANN ``top_k * over_fetch_factor`` 候选，
    再依据 metadata 掩码筛选。该实现简洁、对所有后端通用；当过滤集合非常小时
    （理论上应改用 pre-filter）会出现召回不足，调用方可以增大 ``over_fetch_factor`` 或
    在 ``filters`` 命中行较少时切换到 brute 后端进行 pre-filter。

    Args:
        backend: 已构建完成的 :class:`IndexBackend` 实例。
        cell_ids: 与底层向量对应的 cell 编号列表。
        metadata: 元数据表。
        query_vector: 查询向量，形状 ``(D,)`` 或 ``(1, D)``。
        top_k: 最终返回近邻数。
        filters: metadata 过滤条件。
        exclude_indices: 需排除的底层索引集合（例如查询点自身）。
        over_fetch_factor: post-filter 时的候选放大倍数。
        metric: 度量名称，用于响应元信息。

    Returns:
        dict[str, Any]: ``{"results": list, "query_time_ms", "total_candidates",
        "index_backend", "metric"}``。
    """
    start = time.perf_counter()
    q = np.atleast_2d(np.asarray(query_vector, dtype=np.float32))
    fetch_k = int(top_k * max(1, over_fetch_factor)) if filters else top_k
    if exclude_indices:
        fetch_k += len(exclude_indices)
    fetch_k = min(max(fetch_k, top_k), len(cell_ids))

    indices, distances = backend.search(q, fetch_k)
    indices = np.asarray(indices)[0]
    distances = np.asarray(distances)[0]

    mask = _apply_filters(metadata, filters)
    results: list[dict[str, Any]] = []
    for idx, dist in zip(indices, distances, strict=False):
        idx_int = int(idx)
        if idx_int < 0 or idx_int >= len(cell_ids):
            continue
        if exclude_indices and idx_int in exclude_indices:
            continue
        if mask is not None and not bool(mask[idx_int]):
            continue
        results.append(
            {
                "rank": len(results) + 1,
                "cell_id": cell_ids[idx_int],
                "distance": float(dist),
                "meta": _row_to_meta(metadata, idx_int),
            }
        )
        if len(results) >= top_k:
            break

    query_time_ms = (time.perf_counter() - start) * 1000.0
    return {
        "results": results,
        "query_time_ms": query_time_ms,
        "total_candidates": int(fetch_k),
        "index_backend": getattr(backend, "name", backend.__class__.__name__),
        "metric": metric or getattr(backend, "metric", None),
    }


def get_index_backend(
    index_id: int,
    dataset_dir: str | None = None,
    backend_name: str | None = None,
    metric: str | None = None,
    dim: int | None = None,
    index_path: str | None = None,
) -> Any:
    """通过 :class:`IndexCache` 获取已加载的索引后端实例。

    本函数延迟导入 ``app.services.ann.cache``，在缓存模块尚未就绪或调用方未
    提供足够元信息时，降级为直接通过工厂构造并 ``load`` 索引文件。

    Args:
        index_id: 索引记录 ID。
        dataset_dir: 数据集制品目录，用于在缓存未命中时构造 brute 兜底。
        backend_name: 后端名，缓存未命中时必填。
        metric: 度量。
        dim: 向量维度。
        index_path: 索引文件路径，用于 ``load``。

    Returns:
        IndexBackend: 可调用 ``search`` 的索引后端实例。

    Raises:
        RuntimeError: 当无法解析到可用索引时抛出。
    """
    try:
        from app.services.ann.cache import IndexCache

        cached = IndexCache.instance().peek(index_id)
        if cached is not None:
            return cached
    except Exception as exc:  # noqa: BLE001
        logger.debug("IndexCache 不可用，降级到直接加载: %s", exc)

    if backend_name is None or dim is None:
        raise RuntimeError(
            f"无法解析索引 {index_id}：IndexCache 不可用且缺少 backend_name/dim 元信息"
        )
    from app.services.ann.factory import create_backend

    backend = create_backend(backend_name, dim=int(dim), metric=metric or "l2")
    if index_path and os.path.isfile(index_path):
        backend.load(index_path)
    elif dataset_dir is not None:
        artifacts = load_dataset_artifacts(dataset_dir)
        backend.build(artifacts["vectors"])
    else:
        raise RuntimeError(f"索引 {index_id} 缺少 index_path 且未提供 dataset_dir")
    return backend


def search_by_vector(
    query_vector: np.ndarray | list[float],
    dataset_dir: str,
    backend: Any,
    top_k: int = 10,
    filters: dict[str, Any] | None = None,
    exclude_cell_id: str | None = None,
    metric: str | None = None,
) -> dict[str, Any]:
    """按查询向量执行检索。

    本函数为同步实现；其 ``async`` 包装见 :func:`async_search_by_vector`。

    Args:
        query_vector: 查询向量，形状 ``(D,)``。
        dataset_dir: 数据集制品目录，用于加载 cell_ids 与 metadata。
        backend: 已加载完成的 :class:`IndexBackend` 实例。
        top_k: 返回近邻数量。
        filters: metadata 过滤条件。
        exclude_cell_id: 需要从结果中剔除的 cell（典型场景：以自身向量查询）。
        metric: 距离度量名，用于回填响应。

    Returns:
        dict[str, Any]: 见 :func:`search_with_backend`。
    """
    artifacts = load_dataset_artifacts(dataset_dir)
    exclude_indices: set[int] | None = None
    if exclude_cell_id is not None:
        cid_map: dict[str, int] = artifacts["cell_id_to_index"]
        if exclude_cell_id in cid_map:
            exclude_indices = {cid_map[exclude_cell_id]}
    return search_with_backend(
        backend=backend,
        cell_ids=artifacts["cell_ids"],
        metadata=artifacts["metadata"],
        query_vector=np.asarray(query_vector, dtype=np.float32),
        top_k=top_k,
        filters=filters,
        exclude_indices=exclude_indices,
        metric=metric,
    )


def search_by_cell_id(
    query_cell_id: str,
    dataset_dir: str,
    backend: Any,
    top_k: int = 10,
    filters: dict[str, Any] | None = None,
    metric: str | None = None,
) -> dict[str, Any]:
    """按 cell_id 执行检索：先解析其向量，再调用 :func:`search_by_vector`。

    Args:
        query_cell_id: 查询细胞 ID。
        dataset_dir: 数据集目录。
        backend: 索引后端实例。
        top_k: 返回近邻数量。
        filters: metadata 过滤条件。
        metric: 距离度量名。

    Returns:
        dict[str, Any]: 检索结果；自动排除自身。

    Raises:
        KeyError: 当 ``query_cell_id`` 不存在于数据集中时抛出。
    """
    artifacts = load_dataset_artifacts(dataset_dir)
    cid_map: dict[str, int] = artifacts["cell_id_to_index"]
    if query_cell_id not in cid_map:
        raise KeyError(f"cell_id 不存在: {query_cell_id}")
    query_idx = cid_map[query_cell_id]
    query_vector = artifacts["vectors"][query_idx]
    return search_with_backend(
        backend=backend,
        cell_ids=artifacts["cell_ids"],
        metadata=artifacts["metadata"],
        query_vector=query_vector,
        top_k=top_k,
        filters=filters,
        exclude_indices={query_idx},
        metric=metric,
    )


async def async_search_by_vector(
    query_vector: np.ndarray | list[float],
    dataset_dir: str,
    backend: Any,
    top_k: int = 10,
    filters: dict[str, Any] | None = None,
    exclude_cell_id: str | None = None,
    metric: str | None = None,
    index_id: int | None = None,
) -> dict[str, Any]:
    """:func:`search_by_vector` 的异步包装。

    Args:
        index_id: 可选，提供时启用 F2 Redis 检索结果缓存（同 query+filter+top_k+index_id
            300s 内命中跳过 ANN 计算）。
    """
    from app.services import search_cache  # noqa: PLC0415  内部依赖避免顶部循环

    async def _compute() -> dict[str, Any]:
        return await asyncio.to_thread(
            search_by_vector,
            query_vector,
            dataset_dir,
            backend,
            top_k,
            filters,
            exclude_cell_id,
            metric,
        )

    if index_id is None:
        return await _compute()
    key = search_cache.make_cache_key(
        index_id=index_id, top_k=top_k, query=query_vector, filters=filters
    )
    return await search_cache.cached_or_compute(key, _compute)


async def async_search_by_cell_id(
    query_cell_id: str,
    dataset_dir: str,
    backend: Any,
    top_k: int = 10,
    filters: dict[str, Any] | None = None,
    metric: str | None = None,
    index_id: int | None = None,
) -> dict[str, Any]:
    """:func:`search_by_cell_id` 的异步包装（带 F2 缓存）。"""
    from app.services import search_cache  # noqa: PLC0415

    async def _compute() -> dict[str, Any]:
        return await asyncio.to_thread(
            search_by_cell_id,
            query_cell_id,
            dataset_dir,
            backend,
            top_k,
            filters,
            metric,
        )

    if index_id is None:
        return await _compute()
    key = search_cache.make_cache_key(
        index_id=index_id, top_k=top_k, query=query_cell_id, filters=filters
    )
    return await search_cache.cached_or_compute(key, _compute)


async def async_batch_search(
    queries: list[tuple[str | None, list[float] | np.ndarray | None]],
    dataset_dir: str,
    backend: Any,
    *,
    top_k: int = 10,
    filters: dict[str, Any] | None = None,
    metric: str | None = None,
    index_id: int | None = None,
) -> list[dict[str, Any]]:
    """批量并发执行 N 个查询，复用 F2 Redis 检索缓存。

    每个查询独立走一次 :func:`async_search_by_cell_id` 或 :func:`async_search_by_vector`，
    传入相同的 ``index_id`` 让 ``cached_or_compute`` 按 per-query key 命中缓存。
    并发由 :func:`asyncio.gather` 调度，底层 ANN 计算仍走线程池。

    Args:
        queries: ``(cell_id, vector)`` 元组列表，二选一非空；同时给出时按 ``cell_id`` 优先。
        dataset_dir: 数据集制品目录。
        backend: 已加载完成的 :class:`IndexBackend` 实例。
        top_k: 每个查询返回的近邻数量。
        filters: 所有查询共享的 metadata 过滤条件。
        metric: 距离度量名。
        index_id: 索引 ID；非 None 时启用 F2 检索缓存（per-query）。

    Returns:
        list[dict[str, Any]]: 与 ``queries`` 等长、顺序一致的结果列表，
        每条形如 :func:`search_with_backend` 输出并可能带 ``cache_hit``。

    Raises:
        KeyError: 任一 ``cell_id`` 不存在于数据集（由底层抛出）。
    """
    coros: list[Any] = []
    for cell_id, vector in queries:
        if cell_id is not None:
            coros.append(
                async_search_by_cell_id(
                    query_cell_id=cell_id,
                    dataset_dir=dataset_dir,
                    backend=backend,
                    top_k=top_k,
                    filters=filters,
                    metric=metric,
                    index_id=index_id,
                )
            )
        else:
            coros.append(
                async_search_by_vector(
                    query_vector=vector,  # type: ignore[arg-type]
                    dataset_dir=dataset_dir,
                    backend=backend,
                    top_k=top_k,
                    filters=filters,
                    metric=metric,
                    index_id=index_id,
                )
            )
    return list(await asyncio.gather(*coros))


def merge_ensemble_results(
    per_index_results: list[dict[str, Any]],
    index_ids: list[int],
    top_k: int,
) -> list[dict[str, Any]]:
    """合并 **同一数据集** 上多个索引的检索结果（多后端 ensemble）。

    对每个索引的 ``distance`` 做 z-score 归一化 ``(d - mean) / std``，
    然后按 ``cell_id`` 聚合：取所有索引中最低（最相似）的归一化分数，
    ``voted_by`` 收集所有命中该 cell 的索引 ID（去重升序）。最终按集成
    分数升序排序，取前 ``top_k`` 条并重排 ``rank``。

    与 :func:`merge_multi_dataset_results` 的差异：
        - ensemble 在同一数据集上、按 cell 去重并记录投票来源；
        - multi-dataset 在不同数据集上、保留每条命中并填 ``source_dataset_id``；
        - 归一化策略：ensemble 用 z-score（更鲁棒于 ANN 分数尺度差异），
          multi-dataset 用 min-max。

    Args:
        per_index_results: 每个索引对应的 :func:`search_with_backend` 输出，
            顺序需与 ``index_ids`` 一一对应。
        index_ids: 与 ``per_index_results`` 对齐的索引 ID 列表。
        top_k: 合并后保留的命中数量。

    Returns:
        list[dict[str, Any]]: 重排后的 ensemble 命中列表，每条形如
        ``{"rank", "cell_id", "score", "voted_by", "meta"}``。
    """
    aggregated: dict[str, dict[str, Any]] = {}
    for idx_id, payload in zip(index_ids, per_index_results, strict=False):
        hits = payload.get("results", [])
        if not hits:
            continue
        distances = np.array([h["distance"] for h in hits], dtype=np.float64)
        mean = float(distances.mean())
        std = float(distances.std())
        normalized = np.zeros_like(distances) if std <= 1e-12 else (distances - mean) / std
        for hit, z_score in zip(hits, normalized, strict=False):
            cid = hit["cell_id"]
            current = aggregated.get(cid)
            if current is None:
                aggregated[cid] = {
                    "cell_id": cid,
                    "score": float(z_score),
                    "voted_by": [int(idx_id)],
                    "meta": hit.get("meta") or {},
                }
            else:
                if float(z_score) < current["score"]:
                    current["score"] = float(z_score)
                if int(idx_id) not in current["voted_by"]:
                    current["voted_by"].append(int(idx_id))
                if not current.get("meta") and hit.get("meta"):
                    current["meta"] = hit.get("meta") or {}

    merged = sorted(aggregated.values(), key=lambda x: x["score"])[:top_k]
    for i, item in enumerate(merged, start=1):
        item["rank"] = i
        item["voted_by"] = sorted(set(item["voted_by"]))
    return merged


def merge_multi_dataset_results(
    per_dataset_results: list[dict[str, Any]],
    dataset_ids: list[int],
    top_k: int,
) -> list[dict[str, Any]]:
    """合并多个数据集的检索结果。

    对每个数据集的 ``distance`` 做 min-max 归一化后统一升序排序，并按 ``rank`` 重排。

    Args:
        per_dataset_results: 每个数据集对应的 :func:`search_with_backend` 输出。
        dataset_ids: 与 ``per_dataset_results`` 一一对应的数据集 ID 列表。
        top_k: 合并后保留的结果数。

    Returns:
        list[dict[str, Any]]: 重排后的命中列表，每条包含 ``source_dataset_id``。
    """
    merged: list[dict[str, Any]] = []
    for ds_id, payload in zip(dataset_ids, per_dataset_results, strict=False):
        hits = payload.get("results", [])
        if not hits:
            continue
        distances = np.array([h["distance"] for h in hits], dtype=np.float64)
        dmin, dmax = float(distances.min()), float(distances.max())
        span = dmax - dmin if dmax > dmin else 1.0
        for h, raw_d in zip(hits, distances, strict=False):
            norm = float((raw_d - dmin) / span)
            merged.append(
                {
                    "rank": 0,
                    "cell_id": h["cell_id"],
                    "distance": float(raw_d),
                    "normalized_distance": norm,
                    "meta": h.get("meta", {}),
                    "source_dataset_id": int(ds_id),
                }
            )
    merged.sort(key=lambda x: x["normalized_distance"])
    final = merged[:top_k]
    for i, item in enumerate(final, start=1):
        item["rank"] = i
    return final


# ---------------------------------------------------------------------------
# D1: 运行时参数调整（参数仪表盘）
# ---------------------------------------------------------------------------

_RUNTIME_PARAM_SUPPORT: dict[str, frozenset[str]] = {
    "hnswlib": frozenset({"ef_search"}),
    "adaptive-hnsw": frozenset({"ef_search"}),
    "faiss-hnsw": frozenset({"ef_search"}),
    "faiss-ivfpq": frozenset({"nprobe"}),
    "brute": frozenset(),
}


def _coerce_int(value: Any) -> int | None:
    """安全地把任意 runtime_params 值转成正整数；失败返回 ``None``。"""
    try:
        out = int(value)
    except (TypeError, ValueError):
        return None
    if out <= 0:
        return None
    return out


def _apply_one_runtime_param(backend: Any, key: str, value: Any) -> tuple[Any, Any] | None:
    """对单个 key 应用 runtime 参数到 ``backend``。

    Args:
        backend: 已加载的 :class:`IndexBackend` 实例。
        key: 参数名（如 ``ef_search`` / ``nprobe``）。
        value: 期望生效的新值；非正整数或类型异常时返回 ``None``。

    Returns:
        tuple[Any, Any] | None: ``(effective_value, restore_payload)``。
            ``restore_payload`` 是 :func:`_restore_one_runtime_param` 使用的私有载荷。
            参数无法应用（如后端未初始化、值非法）时返回 ``None``，
            调用方应将该 key 归入 ``ignored_params``。
    """
    name = getattr(backend, "name", backend.__class__.__name__)
    new_val = _coerce_int(value)
    if new_val is None:
        return None

    if key == "ef_search":
        if name == "hnswlib":
            if backend._index is None:
                return None
            orig = int(backend._ef_search)
            backend.set_ef(new_val)
            return new_val, ("hnswlib_set_ef", orig)
        if name == "adaptive-hnsw":
            # adaptive-hnsw 的 search 内部会自适应调整 ef，结束后重置回 min_ef；
            # 因此 runtime 的 ef_search 映射到 min_ef 起步值才会真正影响结果。
            orig = int(backend.min_ef)
            backend.min_ef = new_val
            return new_val, ("adaptive_min_ef", orig)
        if name == "faiss-hnsw":
            if backend._index is None:
                return None
            orig = int(backend._index.hnsw.efSearch)
            backend._index.hnsw.efSearch = new_val
            return new_val, ("faiss_hnsw_ef", orig)
        return None

    if key == "nprobe":
        if name == "faiss-ivfpq":
            if backend._index is None:
                return None
            orig = int(backend._nprobe)
            backend._index.nprobe = new_val
            backend._nprobe = new_val
            return new_val, ("ivfpq_nprobe", orig)
        return None

    return None


def _restore_one_runtime_param(backend: Any, payload: tuple[str, Any]) -> None:
    """恢复 :func:`_apply_one_runtime_param` 改动过的单个属性。"""
    kind, orig = payload
    try:
        if kind == "hnswlib_set_ef":
            backend.set_ef(int(orig))
        elif kind == "adaptive_min_ef":
            backend.min_ef = int(orig)
        elif kind == "faiss_hnsw_ef":
            if backend._index is not None:
                backend._index.hnsw.efSearch = int(orig)
        elif kind == "ivfpq_nprobe":
            if backend._index is not None:
                backend._index.nprobe = int(orig)
            backend._nprobe = int(orig)
    except Exception as exc:  # noqa: BLE001
        logger.warning("恢复 runtime_param 失败 kind=%s err=%s", kind, exc)


def apply_runtime_params(
    backend: Any, params: dict[str, Any]
) -> tuple[dict[str, Any], list[str], list[tuple[str, Any]]]:
    """把 ``runtime_params`` 应用到 ``backend`` 并返回恢复信息。

    并发限制 (limitation):
        :class:`IndexCache` 是进程内单例，多请求共享同一个 backend 实例。
        两个 ``/with_params`` 请求并发时，参数会互相覆盖且 ``finally`` 顺序
        难以保证。第一版可接受（参数仪表盘多为单用户拖拽场景），未来如需要
        严格隔离可在该函数外加 :class:`asyncio.Lock` 或为每路请求构造独立 backend。

    Args:
        backend: 已加载的 :class:`IndexBackend` 实例。
        params: 用户期望生效的运行时参数字典；空字典表示不调整。

    Returns:
        tuple[dict[str, Any], list[str], list[tuple[str, Any]]]:
            ``(effective_params, ignored_params, restore_payloads)``。
            ``restore_payloads`` 按应用顺序排列，调用方在 ``finally`` 中
            **倒序** 传给 :func:`restore_runtime_params` 复原后端。
    """
    name = getattr(backend, "name", backend.__class__.__name__)
    supported = _RUNTIME_PARAM_SUPPORT.get(name, frozenset())
    effective: dict[str, Any] = {}
    ignored: list[str] = []
    restores: list[tuple[str, Any]] = []
    for key, value in params.items():
        if key not in supported:
            ignored.append(key)
            continue
        outcome = _apply_one_runtime_param(backend, key, value)
        if outcome is None:
            ignored.append(key)
            continue
        eff_val, payload = outcome
        effective[key] = eff_val
        restores.append(payload)
    return effective, ignored, restores


def restore_runtime_params(backend: Any, restores: list[tuple[str, Any]]) -> None:
    """按倒序恢复 :func:`apply_runtime_params` 记录的所有改动。"""
    for payload in reversed(restores):
        _restore_one_runtime_param(backend, payload)


def search_with_runtime_params(
    *,
    query_cell_id: str | None,
    query_vector: list[float] | np.ndarray | None,
    dataset_dir: str,
    backend: Any,
    top_k: int,
    runtime_params: dict[str, Any] | None = None,
    filters: dict[str, Any] | None = None,
    metric: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """带运行时参数调整的检索（同步实现）。

    流程：``apply_runtime_params`` → ``search_by_cell_id`` / ``search_by_vector``
    → ``finally: restore_runtime_params``。整段在调用方线程中串行执行，
    保证单次调用内 apply / search / restore 原子。

    Args:
        query_cell_id: 查询细胞 ID，与 ``query_vector`` 二选一。
        query_vector: 查询向量，与 ``query_cell_id`` 二选一。
        dataset_dir: 数据集制品目录。
        backend: 已加载的 :class:`IndexBackend` 实例。
        top_k: 返回近邻数量。
        runtime_params: 期望生效的运行时参数；空表示不调整。
        filters: metadata 过滤条件。
        metric: 距离度量，用于响应回填。

    Returns:
        tuple[dict[str, Any], dict[str, Any], list[str]]:
            ``(search_result, effective_params, ignored_params)``，
            ``search_result`` 形如 :func:`search_with_backend` 输出。

    Raises:
        ValueError: ``query_cell_id`` 与 ``query_vector`` 同时为空。
        KeyError: ``query_cell_id`` 不存在于数据集。
    """
    if query_cell_id is None and query_vector is None:
        raise ValueError("query_cell_id 与 query_vector 必须二选一")
    effective, ignored, restores = apply_runtime_params(backend, runtime_params or {})
    try:
        if query_cell_id is not None:
            result = search_by_cell_id(
                query_cell_id=query_cell_id,
                dataset_dir=dataset_dir,
                backend=backend,
                top_k=top_k,
                filters=filters,
                metric=metric,
            )
        else:
            result = search_by_vector(
                query_vector=query_vector,
                dataset_dir=dataset_dir,
                backend=backend,
                top_k=top_k,
                filters=filters,
                exclude_cell_id=None,
                metric=metric,
            )
        return result, effective, ignored
    finally:
        restore_runtime_params(backend, restores)


async def async_search_with_runtime_params(
    *,
    query_cell_id: str | None,
    query_vector: list[float] | np.ndarray | None,
    dataset_dir: str,
    backend: Any,
    top_k: int,
    runtime_params: dict[str, Any] | None = None,
    filters: dict[str, Any] | None = None,
    metric: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """:func:`search_with_runtime_params` 的异步包装。

    apply / search / restore 全部在同一个 :func:`asyncio.to_thread` 内执行，
    保证单次调用的原子性；并发请求共享 backend 实例的限制详见
    :func:`apply_runtime_params` 的注释。
    """

    def _do() -> tuple[dict[str, Any], dict[str, Any], list[str]]:
        return search_with_runtime_params(
            query_cell_id=query_cell_id,
            query_vector=query_vector,
            dataset_dir=dataset_dir,
            backend=backend,
            top_k=top_k,
            runtime_params=runtime_params,
            filters=filters,
            metric=metric,
        )

    return await asyncio.to_thread(_do)


# ---------------------------------------------------------------------------
# D7: 对齐数据集检索（跨数据集统一向量空间）
# ---------------------------------------------------------------------------


def search_aligned_dataset(
    aligned: Any,
    query_vector: np.ndarray | list[float],
    top_k: int = 10,
    filters: dict[str, Any] | None = None,
    exclude_cell_id: str | None = None,
) -> dict[str, Any]:
    """在对齐数据集上执行单库检索（D7 扩展功能）。

    对齐数据集的向量来自跨数据集 PCA / harmony 校正后的统一空间，
    无需 min-max 归一化即可直接合并结果。内部用 brute 后端（``l2``）即可，
    与原始数据集的 ANN 后端解耦，简化部署。

    Args:
        aligned: :class:`app.models.aligned_dataset.AlignedDataset` 实例。
        query_vector: 查询向量；维度需与 ``aligned.target_dim`` 一致。
        top_k: 返回近邻数量。
        filters: 元数据过滤条件，作用于合并后的 metadata（典型用例：
            ``{"source_dataset_id": [1, 2]}``）。
        exclude_cell_id: 需要从结果剔除的 cell_id。

    Returns:
        dict[str, Any]: 与 :func:`search_with_backend` 同 shape，但每个 hit 额外携带
        ``source_dataset_id``（取自 ``cell_map``），便于前端展示。

    Raises:
        RuntimeError: 对齐数据集制品缺失或向量维度不匹配。
    """
    from app.services.alignment import load_aligned_artifacts
    from app.services.ann.brute_backend import BruteBackend

    artifacts = load_aligned_artifacts(aligned)
    vectors: np.ndarray = artifacts["vectors"]
    cell_ids: list[str] = artifacts["cell_ids"]
    metadata: pd.DataFrame = artifacts["metadata"]
    cell_map: list[dict[str, Any]] = artifacts["cell_map"]

    query = np.asarray(query_vector, dtype=np.float32)
    if vectors.ndim != 2 or vectors.shape[1] != query.shape[-1]:
        raise RuntimeError(f"对齐向量维度不匹配 expected={vectors.shape[1]} got={query.shape[-1]}")

    backend = BruteBackend(dim=int(vectors.shape[1]), metric="l2")
    backend.build(vectors)

    exclude_indices: set[int] | None = None
    if exclude_cell_id is not None:
        cid_map: dict[str, int] = artifacts["cell_id_to_index"]
        if exclude_cell_id in cid_map:
            exclude_indices = {cid_map[exclude_cell_id]}

    result = search_with_backend(
        backend=backend,
        cell_ids=cell_ids,
        metadata=metadata,
        query_vector=query,
        top_k=top_k,
        filters=filters,
        exclude_indices=exclude_indices,
        metric="l2",
    )

    # 回填 source_dataset_id（从 cell_map 索引）
    sid_by_cell = {entry["cell_id"]: int(entry["source_dataset_id"]) for entry in cell_map}
    for hit in result.get("results", []):
        cid = hit["cell_id"]
        if cid in sid_by_cell:
            hit["source_dataset_id"] = sid_by_cell[cid]
    return result


async def async_search_aligned_dataset(
    aligned: Any,
    query_vector: np.ndarray | list[float],
    top_k: int = 10,
    filters: dict[str, Any] | None = None,
    exclude_cell_id: str | None = None,
) -> dict[str, Any]:
    """:func:`search_aligned_dataset` 的 ``asyncio.to_thread`` 包装。

    把 CPU/numpy 密集型工作卸载到线程池，避免阻塞 FastAPI 事件循环。
    """
    return await asyncio.to_thread(
        search_aligned_dataset,
        aligned,
        query_vector,
        top_k,
        filters,
        exclude_cell_id,
    )
