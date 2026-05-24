"""数据预处理 ARQ 任务。

后台流程：
    1. 取 ``Dataset`` 记录，将状态置为 ``preprocessing``；
    2. 通过 ``preprocess_h5ad`` 完成向量化与元信息落盘；
    3. 成功写回向量路径/维度/细胞数等字段，置 ``ready``；
    4. 失败置 ``failed`` 并记录错误日志。

同时提供 :func:`enqueue_preprocess`，供 FastAPI 路由层在上传完成后入队。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from arq import create_pool
from arq.connections import RedisSettings

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models.dataset import Dataset
from app.services.preprocess import preprocess_h5ad

logger = get_logger(__name__)


async def _set_status(dataset_id: int, status: str, **fields: Any) -> None:
    """在独立事务中更新数据集状态与可选字段。

    Args:
        dataset_id: 数据集 ID。
        status: 目标状态。
        **fields: 需要一并写回的其它字段（如 ``vectors_path``、``cell_count``）。
    """
    async with AsyncSessionLocal() as db:
        ds = await db.get(Dataset, dataset_id)
        if ds is None:
            logger.warning("更新状态时数据集不存在 dataset_id=%s", dataset_id)
            return
        ds.status = status
        for key, value in fields.items():
            if hasattr(ds, key):
                setattr(ds, key, value)
        await db.commit()


async def preprocess_dataset(ctx: dict[str, Any], dataset_id: int) -> dict[str, Any]:
    """ARQ 后台任务：读取 .h5ad、Scanpy 预处理、向量化、更新 DB。

    流程：
        1. 从 DB 取 Dataset，置 ``status=preprocessing``；
        2. 调 :func:`app.services.preprocess.preprocess_h5ad` 完成向量化；
        3. 把结果（cell_count、vector_dim、vector_source、vectors_path、meta_columns）写回 Dataset；
        4. 成功置 ``status=ready``，失败置 ``status=failed`` 并记录 error 日志。

    Args:
        ctx: ARQ 任务上下文。
        dataset_id: 数据集 ID。

    Returns:
        dict: 预处理结果或错误描述。
    """
    logger.info("preprocess_dataset 入队执行 dataset_id=%s", dataset_id)

    async with AsyncSessionLocal() as db:
        ds = await db.get(Dataset, dataset_id)
        if ds is None:
            logger.error("数据集不存在 dataset_id=%s", dataset_id)
            return {"ok": False, "error": "dataset not found", "dataset_id": dataset_id}
        h5ad_path = ds.h5ad_path
        ds.status = "preprocessing"
        await db.commit()

    dataset_dir = Path(settings.PROCESSED_DIR) / str(dataset_id)

    try:
        result = await asyncio.to_thread(preprocess_h5ad, h5ad_path, dataset_dir)
    except Exception as exc:
        logger.exception("预处理失败 dataset_id=%s err=%s", dataset_id, exc)
        await _set_status(dataset_id, "failed")
        return {"ok": False, "error": str(exc), "dataset_id": dataset_id}

    await _set_status(
        dataset_id,
        "ready",
        vectors_path=result["vectors_path"],
        cell_count=result["cell_count"],
        vector_dim=result["vector_dim"],
        vector_source=result["vector_source"],
        vector_format=result.get("vector_format", "dense"),
        meta_columns=result["meta_columns"],
    )
    logger.info(
        "预处理完成 dataset_id=%s cells=%d dim=%d",
        dataset_id,
        result["cell_count"],
        result["vector_dim"],
    )
    return {"ok": True, "dataset_id": dataset_id, "result": result}


async def enqueue_preprocess(dataset_id: int) -> str:
    """把预处理任务入队 ARQ，返回 ``job_id``。

    通过 ``arq.create_pool(RedisSettings.from_dsn(settings.REDIS_URL))`` 建立连接，
    入队完成后立刻释放。当 Redis 不可用时记录告警并返回空串，避免阻塞上传链路。

    Args:
        dataset_id: 数据集 ID。

    Returns:
        str: ARQ ``job_id``，入队失败或 Redis 不可用时返回空字符串。
    """
    try:
        pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis 不可用，无法入队 dataset_id=%s err=%s", dataset_id, exc)
        return ""

    try:
        job = await pool.enqueue_job("preprocess_dataset", dataset_id)
        if job is None:
            logger.warning("入队返回空 job dataset_id=%s", dataset_id)
            return ""
        return str(job.job_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("入队失败 dataset_id=%s err=%s", dataset_id, exc)
        return ""
    finally:
        await pool.close()
