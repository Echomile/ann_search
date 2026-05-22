"""索引基准评测 ARQ 任务。"""

from __future__ import annotations

import json
import os
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


async def benchmark_index_task(
    ctx: dict[str, Any],
    index_id: int,
    num_queries: int = 100,
    top_k_list: list[int] | None = None,
    concurrency_list: list[int] | None = None,
) -> dict[str, Any]:
    """ARQ 后台任务：运行索引性能评测，结果存盘。

    本任务会：

    1. 从数据库读取 :class:`IndexRecord` 与关联 :class:`Dataset`；
    2. 通过 :func:`app.services.search.get_index_backend` 取得已加载的索引；
    3. 加载数据集向量并调用 :func:`app.services.evaluation.benchmark_index`；
    4. 将结果序列化为 JSON，写入 ``${INDEX_DIR}/benchmarks/{index_id}.json``。

    Args:
        ctx: ARQ 任务上下文，包含 ``job_id`` 等元信息。
        index_id: 索引记录 ID。
        num_queries: 采样查询数量。
        top_k_list: Recall 评测的 K 列表，默认 ``[10, 100]``。
        concurrency_list: 并发压测档位，默认 ``[1, 4, 8, 16]``。

    Returns:
        dict[str, Any]: 评测结果（同 :func:`benchmark_index`），并附 ``task_id``、``result_path``。
    """
    from app.core.config import settings
    from app.db.session import AsyncSessionLocal
    from app.models.dataset import Dataset
    from app.models.index_record import IndexRecord
    from app.services import search as search_service
    from app.services.evaluation import benchmark_index

    top_k_list = top_k_list or [10, 100]
    concurrency_list = concurrency_list or [1, 4, 8, 16]

    logger.info(
        "benchmark_index_task 启动 index_id=%s num_queries=%s top_k=%s conc=%s",
        index_id,
        num_queries,
        top_k_list,
        concurrency_list,
    )

    async with AsyncSessionLocal() as db:
        record = await db.get(IndexRecord, index_id)
        if record is None:
            raise RuntimeError(f"索引不存在: {index_id}")
        dataset = await db.get(Dataset, record.dataset_id)
        if dataset is None:
            raise RuntimeError(f"数据集不存在: {record.dataset_id}")
        dataset_dir = _resolve_dataset_dir(dataset.vectors_path)
        artifacts = search_service.load_dataset_artifacts(dataset_dir)
        backend = search_service.get_index_backend(
            index_id=record.id,
            dataset_dir=dataset_dir,
            backend_name=record.backend,
            metric=record.metric,
            dim=dataset.vector_dim,
            index_path=record.index_path,
        )
        result = benchmark_index(
            backend=backend,
            vectors=artifacts["vectors"],
            index_id=record.id,
            dataset_id=dataset.id,
            metric=record.metric or "l2",
            num_queries=num_queries,
            top_k_list=top_k_list,
            concurrency_list=concurrency_list,
            build_time_seconds=record.build_time_seconds,
            memory_mb=record.memory_mb,
        )

    out_dir = os.path.join(settings.INDEX_DIR, "benchmarks")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{int(index_id)}.json")
    with open(out_path, "w", encoding="utf-8") as fp:
        json.dump(result, fp, ensure_ascii=False, indent=2, default=str)
    logger.info("benchmark_index_task 完成 index_id=%s result=%s", index_id, out_path)
    result["task_id"] = str(ctx.get("job_id") or "") if isinstance(ctx, dict) else ""
    result["result_path"] = out_path
    return result


def _resolve_dataset_dir(vectors_path: str | None) -> str:
    """从 ``Dataset.vectors_path`` 推导数据集制品目录。"""
    if not vectors_path:
        raise RuntimeError("数据集缺少 vectors_path")
    if os.path.isdir(vectors_path):
        return vectors_path
    return os.path.dirname(vectors_path) or vectors_path
