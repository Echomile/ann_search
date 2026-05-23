"""索引管理路由。

提供 ANN 索引的创建（入队异步构建）、查询、删除与状态查询接口。
所有接口均需通过 :func:`app.api.deps.get_current_user` 鉴权，
且仅允许操作当前用户名下的数据集 / 索引。
"""

from __future__ import annotations

import contextlib
import json
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.api.v1.evaluation import benchmark_result_path
from app.models.dataset import Dataset
from app.models.index_record import IndexRecord
from app.schemas.common import Message
from app.schemas.index import (
    IndexCreate,
    IndexCreateResponse,
    IndexRecordOut,
    IndexStatus,
)
from app.services.ann.cache import IndexCache
from app.tasks.index_task import enqueue_build_index

router = APIRouter(tags=["indexes"])


async def _get_owned_dataset(db, dataset_id: int, user_id: int) -> Dataset:
    """读取属于当前用户的数据集，失败时抛 HTTP 异常。"""
    dataset = await db.get(Dataset, dataset_id)
    if dataset is None or dataset.owner_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="数据集不存在")
    return dataset


async def _get_owned_index(db, index_id: int, user_id: int) -> tuple[IndexRecord, Dataset]:
    """读取属于当前用户的索引记录及其数据集。"""
    record = await db.get(IndexRecord, index_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="索引不存在")
    dataset = await db.get(Dataset, record.dataset_id)
    if dataset is None or dataset.owner_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="索引不存在")
    return record, dataset


@router.post(
    "/datasets/{dataset_id}/indexes",
    response_model=IndexCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="构建索引",
    description=(
        "按指定后端与参数为目标数据集构建 ANN 索引。\n\n"
        "- 仅允许操作当前用户名下、``status=ready`` 的数据集；\n"
        "- 会立即在数据库创建 ``status=building`` 的 :class:`IndexRecord`，"
        "并将真正的构建工作通过 ARQ 投递到后台 worker 执行；\n"
        "- 响应中的 ``task_id`` 为 ARQ ``job_id``，可与日志或后续接口结合用于追踪进度。"
    ),
)
async def create_index(
    dataset_id: int,
    payload: IndexCreate,
    current_user: CurrentUser,
    db: DbSession,
    request: Request,
) -> IndexCreateResponse:
    """创建索引记录并入队后台构建任务。

    Args:
        dataset_id: 数据集 ID。
        payload: :class:`IndexCreate` 请求体。
        current_user: 当前用户。
        db: 异步数据库会话。
        request: FastAPI 请求对象，用于获取 ``app.state.arq``。

    Raises:
        HTTPException: 数据集不存在 / 非 ready 状态 / ARQ 未就绪时。

    Returns:
        IndexCreateResponse: 新建索引与异步任务 ID。
    """
    dataset = await _get_owned_dataset(db, dataset_id, current_user.id)
    if dataset.status != "ready":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"数据集尚未就绪: status={dataset.status}",
        )

    arq_pool = getattr(request.app.state, "arq", None)
    if arq_pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="后台任务队列不可用，无法入队索引构建任务",
        )

    record = IndexRecord(
        dataset_id=dataset.id,
        backend=payload.backend,
        metric=payload.metric,
        params=dict(payload.params or {}),
        status="building",
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    try:
        task_id = await enqueue_build_index(arq_pool, record.id)
    except RuntimeError as exc:
        record.status = "failed"
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    return IndexCreateResponse(
        index=IndexRecordOut.model_validate(record),
        task_id=task_id,
    )


@router.get(
    "/datasets/{dataset_id}/indexes",
    response_model=list[IndexRecordOut],
    summary="数据集索引列表",
    description="按 ``created_at`` 倒序返回指定数据集的全部索引记录。",
)
async def list_dataset_indexes(
    dataset_id: int,
    current_user: CurrentUser,
    db: DbSession,
) -> list[IndexRecordOut]:
    """列出数据集下的所有索引记录。

    Args:
        dataset_id: 数据集 ID。
        current_user: 当前用户。
        db: 异步数据库会话。

    Returns:
        list[IndexRecordOut]: 按创建时间倒序的索引列表。
    """
    await _get_owned_dataset(db, dataset_id, current_user.id)
    stmt = (
        select(IndexRecord)
        .where(IndexRecord.dataset_id == dataset_id)
        .order_by(IndexRecord.created_at.desc())
    )
    result = await db.execute(stmt)
    records = list(result.scalars().all())
    return [IndexRecordOut.model_validate(r) for r in records]


@router.get(
    "/indexes/{index_id}",
    response_model=IndexRecordOut,
    summary="索引详情",
    description="返回索引详情，包括构建参数、耗时、内存占用与状态。",
)
async def get_index(
    index_id: int,
    current_user: CurrentUser,
    db: DbSession,
) -> IndexRecordOut:
    """读取索引详情。

    Args:
        index_id: 索引 ID。
        current_user: 当前用户。
        db: 异步数据库会话。

    Returns:
        IndexRecordOut: 索引详情。
    """
    record, _ = await _get_owned_index(db, index_id, current_user.id)
    return IndexRecordOut.model_validate(record)


@router.get(
    "/indexes/{index_id}/status",
    response_model=IndexStatus,
    summary="索引状态",
    description="轻量接口：返回索引的状态、后端、构建耗时与内存占用，适合前端轮询。",
)
async def get_index_status(
    index_id: int,
    current_user: CurrentUser,
    db: DbSession,
) -> IndexStatus:
    """读取索引构建状态。

    Args:
        index_id: 索引 ID。
        current_user: 当前用户。
        db: 异步数据库会话。

    Returns:
        IndexStatus: 状态摘要。
    """
    record, _ = await _get_owned_index(db, index_id, current_user.id)
    return IndexStatus(
        id=record.id,
        status=record.status,  # type: ignore[arg-type]
        backend=record.backend,
        build_time_seconds=record.build_time_seconds,
        memory_mb=record.memory_mb,
    )


@router.get(
    "/indexes/cache/stats",
    summary="索引缓存命中率",
    description=(
        "返回进程内 :class:`IndexCache` 的命中率与计数器："
        " ``capacity / size / hits / misses / loads / evictions / hit_ratio / cached_index_ids``。"
        "用于观测 ANN 索引常驻缓存的效果，命中率长期低意味着工作集大于 capacity 应调大。"
        "鉴权：当前登录用户均可读（无敏感信息）。"
    ),
)
async def get_cache_stats(current_user: CurrentUser) -> dict[str, Any]:  # noqa: ARG001
    """返回 :class:`IndexCache` 命中率与内部计数。"""
    return IndexCache.instance().stats()


@router.get(
    "/indexes/{index_id}/latest-benchmark",
    summary="索引最近一次评测结果",
    description=(
        "从索引视角读取 ``benchmark_index_task`` 落盘的最近一次评测 JSON。"
        "和 ``GET /evaluation/{index_id}/latest`` 等价，但走 indexes 路由方便前端在索引详情页直接调用。"
        "索引不存在或非拥有者返回 404；尚未评测返回 ``{has_benchmark: false}`` + 200。"
    ),
)
async def get_index_latest_benchmark(
    index_id: int,
    current_user: CurrentUser,
    db: DbSession,
) -> dict[str, Any]:
    """读取索引的最近一次评测结果（无评测时降级而非 404）。"""
    record, _ = await _get_owned_index(db, index_id, current_user.id)
    path = benchmark_result_path(record.id)
    if not os.path.exists(path):
        return {"index_id": record.id, "has_benchmark": False, "result": None}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {"index_id": record.id, "has_benchmark": True, "result": data}


@router.delete(
    "/indexes/{index_id}",
    response_model=Message,
    summary="删除索引",
    description="级联删除索引记录、磁盘索引文件并清理进程内缓存。",
)
async def delete_index(
    index_id: int,
    current_user: CurrentUser,
    db: DbSession,
) -> Message:
    """删除索引。

    Args:
        index_id: 索引 ID。
        current_user: 当前用户。
        db: 异步数据库会话。

    Returns:
        Message: 删除提示。
    """
    record, _ = await _get_owned_index(db, index_id, current_user.id)
    path = record.index_path

    IndexCache.instance().evict(record.id)

    await db.delete(record)
    await db.commit()

    if path and os.path.exists(path):
        with contextlib.suppress(OSError):
            os.remove(path)

    return Message(detail=f"索引 {index_id} 已删除")
