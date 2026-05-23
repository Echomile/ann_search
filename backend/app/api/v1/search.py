"""相似检索路由。"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import desc, select

from app.api.deps import CurrentUser, DbSession
from app.models.dataset import Dataset
from app.models.index_record import IndexRecord
from app.models.search_log import SearchLog
from app.schemas.search import (
    MultiDatasetSearchRequest,
    SearchByCellId,
    SearchByVector,
    SearchHit,
    SearchResponse,
)
from app.services import search as search_service

router = APIRouter(prefix="/search", tags=["search"])


async def _get_dataset(db: DbSession, dataset_id: int) -> Dataset:
    """从数据库读取指定数据集，校验状态。"""
    dataset = await db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"数据集不存在: {dataset_id}"
        )
    if dataset.status not in {"ready", "preprocessing"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"数据集尚未就绪，当前状态: {dataset.status}",
        )
    return dataset


async def _get_index_record(
    db: DbSession,
    *,
    dataset_id: int,
    index_id: int | None,
) -> IndexRecord:
    """获取指定（或最新 ready）索引记录。"""
    if index_id is not None:
        record = await db.get(IndexRecord, index_id)
        if record is None or record.dataset_id != dataset_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"索引不存在: {index_id}"
            )
    else:
        stmt = (
            select(IndexRecord)
            .where(IndexRecord.dataset_id == dataset_id, IndexRecord.status == "ready")
            .order_by(desc(IndexRecord.created_at))
            .limit(1)
        )
        record = (await db.execute(stmt)).scalar_one_or_none()
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"数据集 {dataset_id} 暂无可用索引",
            )
    if record.status != "ready":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"索引尚未 ready: {record.status}",
        )
    return record


def _resolve_dataset_dir(dataset: Dataset) -> str:
    """从 :class:`Dataset` 中解析数据集制品目录。

    优先使用 ``vectors_path``：若其指向 ``.npy`` 文件则取其父目录；
    若为目录则直接使用。
    """
    if dataset.vectors_path:
        path = dataset.vectors_path
        if os.path.isdir(path):
            return path
        return os.path.dirname(path) or path
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"数据集 {dataset.id} 缺少预处理向量路径",
    )


async def _log_search(
    db: DbSession,
    *,
    dataset_id: int,
    user_id: int,
    top_k: int,
    filters: dict[str, Any] | None,
    latency_ms: float,
) -> None:
    """异步写入一条 :class:`SearchLog`，失败仅记录日志不影响返回。"""
    log = SearchLog(
        dataset_id=dataset_id,
        user_id=user_id,
        top_k=top_k,
        filters=filters or None,
        latency_ms=latency_ms,
    )
    db.add(log)
    try:
        await db.commit()
    except Exception:  # noqa: BLE001
        await db.rollback()


def _build_response(
    dataset_id: int | None,
    payload: dict[str, Any],
    top_k: int,
) -> SearchResponse:
    """将服务层 dict 输出包装为 :class:`SearchResponse`。"""
    hits = [
        SearchHit(
            rank=item["rank"],
            cell_id=item["cell_id"],
            distance=item["distance"],
            meta=item.get("meta") or {},
            source_dataset_id=item.get("source_dataset_id"),
        )
        for item in payload.get("results", [])
    ]
    return SearchResponse(
        dataset_id=dataset_id,
        top_k=top_k,
        latency_ms=float(payload.get("query_time_ms", 0.0)),
        index_backend=payload.get("index_backend"),
        metric=payload.get("metric"),
        total_candidates=payload.get("total_candidates"),
        hits=hits,
    )


@router.post(
    "/by-id",
    response_model=SearchResponse,
    summary="按细胞 ID 检索",
    description=(
        "使用数据集内已有的 ``cell_id`` 作为查询点，返回 Top-K 相似细胞。"
        " 查询点自身会从结果中剔除；支持基于 metadata 的过滤条件。"
    ),
)
async def search_by_id(
    payload: SearchByCellId,
    db: DbSession,
    current_user: CurrentUser,
) -> SearchResponse:
    """按 cell_id 检索。"""
    dataset = await _get_dataset(db, payload.dataset_id)
    record = await _get_index_record(db, dataset_id=payload.dataset_id, index_id=payload.index_id)
    dataset_dir = _resolve_dataset_dir(dataset)
    backend = search_service.get_index_backend(
        index_id=record.id,
        dataset_dir=dataset_dir,
        backend_name=record.backend,
        metric=record.metric,
        dim=dataset.vector_dim,
        index_path=record.index_path,
    )
    try:
        result = await search_service.async_search_by_cell_id(
            query_cell_id=payload.cell_id,
            dataset_dir=dataset_dir,
            backend=backend,
            top_k=payload.top_k,
            filters=payload.filters,
            metric=record.metric,
        )
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    await _log_search(
        db,
        dataset_id=payload.dataset_id,
        user_id=current_user.id,
        top_k=payload.top_k,
        filters=payload.filters,
        latency_ms=float(result.get("query_time_ms", 0.0)),
    )
    return _build_response(payload.dataset_id, result, payload.top_k)


@router.post(
    "/by-vector",
    response_model=SearchResponse,
    summary="按向量检索",
    description=(
        "使用用户自定义的高维向量作为查询点，返回 Top-K 相似细胞，"
        "可叠加 metadata 过滤条件。默认采用 post-filter 策略，先取 ``top_k * 5`` 候选再筛选。"
    ),
)
async def search_by_vector(
    payload: SearchByVector,
    db: DbSession,
    current_user: CurrentUser,
) -> SearchResponse:
    """按向量检索。"""
    dataset = await _get_dataset(db, payload.dataset_id)
    record = await _get_index_record(db, dataset_id=payload.dataset_id, index_id=payload.index_id)
    if dataset.vector_dim and len(payload.vector) != dataset.vector_dim:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"向量维度 {len(payload.vector)} 与数据集维度 {dataset.vector_dim} 不一致",
        )
    dataset_dir = _resolve_dataset_dir(dataset)
    backend = search_service.get_index_backend(
        index_id=record.id,
        dataset_dir=dataset_dir,
        backend_name=record.backend,
        metric=record.metric,
        dim=dataset.vector_dim,
        index_path=record.index_path,
    )
    result = await search_service.async_search_by_vector(
        query_vector=payload.vector,
        dataset_dir=dataset_dir,
        backend=backend,
        top_k=payload.top_k,
        filters=payload.filters,
        metric=record.metric,
    )
    await _log_search(
        db,
        dataset_id=payload.dataset_id,
        user_id=current_user.id,
        top_k=payload.top_k,
        filters=payload.filters,
        latency_ms=float(result.get("query_time_ms", 0.0)),
    )
    return _build_response(payload.dataset_id, result, payload.top_k)


@router.post(
    "/multi-dataset",
    response_model=SearchResponse,
    summary="跨数据集联合检索",
    description=(
        "对多个数据集并发执行检索，将各数据集的距离做 min-max 归一化后合并重排，"
        "返回的每条结果带有 ``source_dataset_id``。"
        " 支持以 ``cell_id``（需指定 ``source_dataset_id``）或自定义 ``vector`` 发起查询。"
    ),
)
async def search_multi_dataset(
    payload: MultiDatasetSearchRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> SearchResponse:
    """跨数据集联合检索。"""
    if payload.vector is None and payload.cell_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="cell_id 与 vector 必须二选一",
        )
    if payload.index_ids is not None and len(payload.index_ids) != len(payload.dataset_ids):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="index_ids 长度须与 dataset_ids 一致",
        )

    query_vector: list[float] | None = payload.vector
    if query_vector is None:
        source_id = payload.source_dataset_id or payload.dataset_ids[0]
        source_dataset = await _get_dataset(db, source_id)
        source_dir = _resolve_dataset_dir(source_dataset)
        artifacts = search_service.load_dataset_artifacts(source_dir)
        cid_map: dict[str, int] = artifacts["cell_id_to_index"]
        if payload.cell_id not in cid_map:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"cell_id {payload.cell_id} 不存在于数据集 {source_id}",
            )
        query_vector = artifacts["vectors"][cid_map[payload.cell_id]].tolist()

    resolved: list[tuple[Dataset, IndexRecord, str]] = []
    for i, ds_id in enumerate(payload.dataset_ids):
        idx_id = payload.index_ids[i] if payload.index_ids is not None else None
        ds = await _get_dataset(db, ds_id)
        rec = await _get_index_record(db, dataset_id=ds_id, index_id=idx_id)
        resolved.append((ds, rec, _resolve_dataset_dir(ds)))

    coros = [
        _execute_one_multi_search(
            dataset=ds,
            record=rec,
            dataset_dir=ds_dir,
            query_vector=query_vector,
            top_k=payload.top_k,
            filters=payload.filters,
        )
        for ds, rec, ds_dir in resolved
    ]
    per_dataset = await asyncio.gather(*coros)

    merged_hits = search_service.merge_multi_dataset_results(
        per_dataset_results=per_dataset,
        dataset_ids=payload.dataset_ids,
        top_k=payload.top_k,
    )
    latency_ms = float(sum(p.get("query_time_ms", 0.0) for p in per_dataset))
    hits = [
        SearchHit(
            rank=item["rank"],
            cell_id=item["cell_id"],
            distance=item["distance"],
            meta=item.get("meta") or {},
            source_dataset_id=item.get("source_dataset_id"),
        )
        for item in merged_hits
    ]
    total_candidates = int(sum(p.get("total_candidates", 0) for p in per_dataset))
    await _log_search(
        db,
        dataset_id=payload.dataset_ids[0],
        user_id=current_user.id,
        top_k=payload.top_k,
        filters=payload.filters,
        latency_ms=latency_ms,
    )
    return SearchResponse(
        dataset_id=None,
        top_k=payload.top_k,
        latency_ms=latency_ms,
        index_backend="multi",
        metric=None,
        total_candidates=total_candidates,
        hits=hits,
    )


async def _execute_one_multi_search(
    *,
    dataset: Dataset,
    record: IndexRecord,
    dataset_dir: str,
    query_vector: list[float],
    top_k: int,
    filters: dict[str, Any] | None,
) -> dict[str, Any]:
    """多数据集检索：在已解析好元信息后并发执行的单一子任务。

    本函数刻意不访问数据库，以便外层 :func:`asyncio.gather` 可以安全并发。
    """
    backend = search_service.get_index_backend(
        index_id=record.id,
        dataset_dir=dataset_dir,
        backend_name=record.backend,
        metric=record.metric,
        dim=dataset.vector_dim,
        index_path=record.index_path,
    )
    return await search_service.async_search_by_vector(
        query_vector=query_vector,
        dataset_dir=dataset_dir,
        backend=backend,
        top_k=top_k,
        filters=filters,
        metric=record.metric,
    )
