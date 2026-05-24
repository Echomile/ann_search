"""索引构建 ARQ 任务。

提供：
    - :func:`build_index`：ARQ Worker 入口，按 ``IndexRecord`` 配置加载向量、构建索引、落盘并回写 DB；
    - :func:`enqueue_build_index`：在 FastAPI 中调用 ARQ Pool 入队构建任务。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np
from arq.connections import ArqRedis
from scipy import sparse as sp

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models.dataset import Dataset
from app.models.index_record import IndexRecord
from app.services.ann.cache import IndexCache
from app.services.ann.factory import create_backend

logger = get_logger(__name__)


def _index_file_path(dataset_id: int, index_id: int) -> Path:
    """根据 ``dataset_id`` 与 ``index_id`` 生成索引落盘路径。"""
    base = Path(settings.INDEX_DIR) / str(dataset_id)
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{index_id}.bin"


async def build_index(ctx: dict[str, Any], index_id: int) -> dict[str, Any]:
    """构建 ANN 索引并落盘。

    流程：
        1. 取 :class:`IndexRecord` 与关联 :class:`Dataset`；
        2. 加载 ``vectors.npy``（``dataset.vectors_path``）；
        3. 通过 :func:`create_backend` 创建后端实例并调用 ``build``；
        4. 记录耗时与内存占用，调用 ``save`` 持久化；
        5. 写回 :class:`IndexRecord` 的 ``status / index_path / build_time_seconds / memory_mb``。

    Args:
        ctx: ARQ 任务上下文。
        index_id: :class:`IndexRecord` 主键。

    Returns:
        dict: ``{"index_id", "status", "index_path", "build_time_seconds", "memory_mb"}``。
    """
    logger.info("build_index 入队执行 index_id=%s", index_id)
    result: dict[str, Any] = {"index_id": index_id, "status": "failed"}

    async with AsyncSessionLocal() as db:
        record = await db.get(IndexRecord, index_id)
        if record is None:
            logger.error("IndexRecord 不存在 index_id=%s", index_id)
            return result

        dataset = await db.get(Dataset, record.dataset_id)
        if dataset is None:
            record.status = "failed"
            await db.commit()
            logger.error("Dataset 不存在 dataset_id=%s", record.dataset_id)
            return result

        if not dataset.vectors_path or not dataset.vector_dim:
            record.status = "failed"
            await db.commit()
            logger.error(
                "Dataset 未完成预处理 dataset_id=%s vectors_path=%s vector_dim=%s",
                dataset.id,
                dataset.vectors_path,
                dataset.vector_dim,
            )
            return result

        try:
            vector_format = getattr(dataset, "vector_format", "dense") or "dense"
            if vector_format == "sparse":
                # SparseBruteBackend 直接消费 CSR，其它后端无法处理稀疏向量
                if record.backend != "sparse-brute":
                    raise RuntimeError(
                        f"vector_format=sparse 仅支持 sparse-brute 后端，实际 {record.backend}"
                    )
                vectors = sp.load_npz(dataset.vectors_path)
                if vectors.dtype != np.float32:
                    vectors = vectors.astype(np.float32, copy=False)
            else:
                vectors = np.load(dataset.vectors_path).astype(np.float32, copy=False)
            backend = create_backend(record.backend, dataset.vector_dim, record.metric)
            params = dict(record.params or {})

            start = time.perf_counter()
            backend.build(vectors, **params)
            build_time = float(time.perf_counter() - start)

            out_path = _index_file_path(dataset.id, record.id)
            backend.save(str(out_path))

            record.index_path = str(out_path)
            record.build_time_seconds = build_time
            record.memory_mb = float(backend.memory_mb())
            record.status = "ready"
            await db.commit()

            IndexCache.instance().evict(index_id)

            result = {
                "index_id": index_id,
                "status": "ready",
                "index_path": str(out_path),
                "build_time_seconds": build_time,
                "memory_mb": record.memory_mb,
            }
            logger.info(
                "build_index 完成 index_id=%s backend=%s time=%.3fs memory=%.2fMB",
                index_id,
                record.backend,
                build_time,
                record.memory_mb,
            )
        except Exception as exc:  # noqa: BLE001
            record.status = "failed"
            await db.commit()
            logger.exception("build_index 失败 index_id=%s err=%s", index_id, exc)

    return result


async def enqueue_build_index(arq_pool: ArqRedis, index_id: int) -> str:
    """向 ARQ 队列投递一个 :func:`build_index` 任务。

    Args:
        arq_pool: 已初始化的 :class:`ArqRedis` 连接池（通常由 ``app.state.arq`` 提供）。
        index_id: 待构建的 :class:`IndexRecord` 主键。

    Returns:
        str: ARQ 分配的 ``job_id``，可用于后续状态查询。

    Raises:
        RuntimeError: 入队失败（例如同名 job 已存在且未过期）。
    """
    job = await arq_pool.enqueue_job("build_index", index_id)
    if job is None:
        raise RuntimeError(f"build_index 入队失败 index_id={index_id}")
    return job.job_id
