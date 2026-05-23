"""相似检索路由。"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import desc, select
from starlette.responses import StreamingResponse

from app.api.deps import CurrentUser, DbSession
from app.models.dataset import Dataset
from app.models.index_record import IndexRecord
from app.models.search_log import SearchLog
from app.schemas.search import (
    BatchSearchHitGroup,
    BatchSearchRequest,
    BatchSearchResponse,
    EnsembleHit,
    EnsembleSearchRequest,
    EnsembleSearchResponse,
    MultiDatasetSearchRequest,
    SearchByCellId,
    SearchByVector,
    SearchHit,
    SearchResponse,
)
from app.services import search as search_service

try:
    from sse_starlette.sse import EventSourceResponse  # type: ignore[import-not-found]

    _HAS_SSE_STARLETTE = True
except ImportError:  # pragma: no cover - 仅在缺少 sse-starlette 时触发
    EventSourceResponse = None  # type: ignore[assignment]
    _HAS_SSE_STARLETTE = False

router = APIRouter(prefix="/search", tags=["search"])

_STREAM_PER_HIT_DELAY_SEC = 0.02


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
            index_id=record.id,
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
        index_id=record.id,
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


async def _stream_hits_as_sse(
    *,
    hits: list[dict[str, Any]],
    latency_ms: float,
    total_candidates: int,
    index_backend: str | None,
    metric: str | None,
    dataset_id: int,
    top_k: int,
) -> AsyncIterator[dict[str, str]]:
    """按 rank 顺序生成 SSE 事件。

    每条命中作为 ``event: hit`` 推送，最后追加 ``event: done``。
    在 hit 之间 sleep ``_STREAM_PER_HIT_DELAY_SEC``，模拟"边算边推"
    的视觉效果（实际 ANN 已在调用前完成，仅为前端流式体验服务）。

    Args:
        hits: 已按 rank 排序的命中字典列表（含 ``cell_id`` / ``distance`` / ``meta``）。
        latency_ms: ANN 端到端耗时（毫秒），随 done 事件回填。
        total_candidates: 候选总数，随 done 事件回填。
        index_backend: 索引后端名称，随 done 事件回填。
        metric: 距离度量名称，随 done 事件回填。
        dataset_id: 数据集 ID，随 done 事件回填。
        top_k: 实际返回数量，随 done 事件回填。

    Yields:
        dict[str, str]: ``{"event": ..., "data": <json-str>}`` 形态的 SSE payload。
    """
    for item in hits:
        yield {
            "event": "hit",
            "data": json.dumps(
                {
                    "rank": item["rank"],
                    "cell_id": item["cell_id"],
                    "distance": float(item["distance"]),
                    "meta": item.get("meta") or {},
                    "source_dataset_id": item.get("source_dataset_id"),
                },
                ensure_ascii=False,
            ),
        }
        await asyncio.sleep(_STREAM_PER_HIT_DELAY_SEC)
    yield {
        "event": "done",
        "data": json.dumps(
            {
                "dataset_id": dataset_id,
                "top_k": top_k,
                "latency_ms": float(latency_ms),
                "total_candidates": int(total_candidates),
                "index_backend": index_backend,
                "metric": metric,
            },
            ensure_ascii=False,
        ),
    }


def _events_to_streaming_response(
    events: AsyncIterator[dict[str, str]],
) -> StreamingResponse:
    """``sse-starlette`` 不可用时的纯 ``StreamingResponse`` 兜底实现。

    将 ``{"event": ..., "data": ...}`` 字典序列按 SSE 协议拼成
    ``event: <name>\\ndata: <payload>\\n\\n`` 文本块。
    """

    async def _wire() -> AsyncIterator[bytes]:
        async for ev in events:
            event = ev.get("event") or "message"
            data = ev.get("data") or ""
            yield f"event: {event}\ndata: {data}\n\n".encode()

    return StreamingResponse(
        _wire(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/by-vector-stream",
    summary="按向量检索（SSE 流式）",
    description=(
        "Server-Sent Events 版 ``by-vector`` 检索：服务端先用 ANN 计算 Top-K hits，"
        "再按 rank 顺序逐条推送 ``event: hit``，每条间隔约 20ms 以改善前端"
        "\"等结果出来才刷新\"的卡顿感；最后一条为 ``event: done``，"
        "携带 ``latency_ms`` / ``total_candidates`` 等汇总信息。"
        "\n\n"
        "客户端建议通过 ``fetch + ReadableStream`` 自行解析（``EventSource`` 不支持 POST）。"
    ),
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "SSE 流：N 条 ``event: hit`` + 1 条 ``event: done``。",
        },
        503: {"description": "sse-starlette 不可用且兜底实现也未启用时返回。"},
    },
)
async def search_by_vector_stream(
    payload: SearchByVector,
    db: DbSession,
    current_user: CurrentUser,
) -> StreamingResponse:
    """按向量检索 SSE 流式接口。"""
    dataset = await _get_dataset(db, payload.dataset_id)
    if dataset.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"数据集不存在: {payload.dataset_id}"
        )
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
        index_id=record.id,
    )
    latency_ms = float(result.get("query_time_ms", 0.0))
    await _log_search(
        db,
        dataset_id=payload.dataset_id,
        user_id=current_user.id,
        top_k=payload.top_k,
        filters=payload.filters,
        latency_ms=latency_ms,
    )

    events = _stream_hits_as_sse(
        hits=list(result.get("results", [])),
        latency_ms=latency_ms,
        total_candidates=int(result.get("total_candidates", 0) or 0),
        index_backend=result.get("index_backend"),
        metric=result.get("metric"),
        dataset_id=payload.dataset_id,
        top_k=payload.top_k,
    )
    if _HAS_SSE_STARLETTE and EventSourceResponse is not None:
        return EventSourceResponse(events)
    return _events_to_streaming_response(events)


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


_BATCH_MAX_QUERIES = 50


@router.post(
    "/batch",
    response_model=BatchSearchResponse,
    summary="批量检索",
    description=(
        "对单个数据集一次性提交 ``N`` 个查询，后端用 :func:`asyncio.gather` 并发执行；"
        " 每个查询独立写入 F2 Redis 检索缓存（按 query+filter+top_k+index_id 哈希），"
        "可显著降低重复查询成本。"
        "\n\n"
        "- ``queries`` 长度需满足 ``1 <= N <= 50``，超出返回 400；空列表返回 422；\n"
        "- ``cell_id`` 与 ``vector`` 二选一必填，同时给出时按 ``cell_id`` 优先解析；\n"
        "- ``filters`` 在所有查询间共享；``top_k`` 对全部查询统一生效；\n"
        "- 整体 wall-time 与每条查询的 latency 都会回填到响应。"
    ),
)
async def search_batch_endpoint(
    payload: BatchSearchRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> BatchSearchResponse:
    """批量检索：N 个查询并发执行，复用 F2 Redis 检索缓存。"""
    if len(payload.queries) > _BATCH_MAX_QUERIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"queries 数量上限 {_BATCH_MAX_QUERIES}，当前 {len(payload.queries)}",
        )

    dataset = await _get_dataset(db, payload.dataset_id)
    if dataset.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"数据集不存在: {payload.dataset_id}"
        )
    record = await _get_index_record(
        db, dataset_id=payload.dataset_id, index_id=payload.index_id
    )

    if dataset.vector_dim:
        for i, item in enumerate(payload.queries):
            if item.cell_id is None and item.vector is not None and len(item.vector) != dataset.vector_dim:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"queries[{i}] 向量维度 {len(item.vector)} 与数据集维度 "
                        f"{dataset.vector_dim} 不一致"
                    ),
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

    queries: list[tuple[str | None, list[float] | None]] = [
        (item.cell_id, None) if item.cell_id is not None else (None, item.vector)
        for item in payload.queries
    ]

    start = time.perf_counter()
    try:
        per_query = await search_service.async_batch_search(
            queries=queries,
            dataset_dir=dataset_dir,
            backend=backend,
            top_k=payload.top_k,
            filters=payload.filters,
            metric=record.metric,
            index_id=record.id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    total_latency_ms = (time.perf_counter() - start) * 1000.0

    await _log_search(
        db,
        dataset_id=payload.dataset_id,
        user_id=current_user.id,
        top_k=payload.top_k,
        filters={"batch": True, "n": len(payload.queries)},
        latency_ms=total_latency_ms,
    )

    groups: list[BatchSearchHitGroup] = []
    for i, (item, result) in enumerate(zip(payload.queries, per_query, strict=True)):
        hits = [
            SearchHit(
                rank=hit["rank"],
                cell_id=hit["cell_id"],
                distance=hit["distance"],
                meta=hit.get("meta") or {},
                source_dataset_id=hit.get("source_dataset_id"),
            )
            for hit in result.get("results", [])
        ]
        groups.append(
            BatchSearchHitGroup(
                query_index=i,
                query_cell_id=item.cell_id,
                hits=hits,
                latency_ms=float(result.get("query_time_ms", 0.0)),
                cache_hit=bool(result.get("cache_hit", False)),
            )
        )

    return BatchSearchResponse(
        dataset_id=payload.dataset_id,
        top_k=payload.top_k,
        total_queries=len(payload.queries),
        total_latency_ms=total_latency_ms,
        index_backend=record.backend,
        metric=record.metric,
        groups=groups,
    )


_ENSEMBLE_MIN_INDEXES = 2
_ENSEMBLE_MAX_INDEXES = 5
_ENSEMBLE_OVER_FETCH_FACTOR = 3


async def _resolve_query_vector(
    *,
    dataset: Dataset,
    dataset_dir: str,
    cell_id: str | None,
    vector: list[float] | None,
) -> list[float]:
    """将 ensemble 请求的查询统一解析为向量。

    Args:
        dataset: 已校验的数据集对象。
        dataset_dir: 数据集制品目录。
        cell_id: 查询细胞编号，与 ``vector`` 二选一。
        vector: 自定义向量。

    Returns:
        list[float]: 长度等于 ``dataset.vector_dim`` 的查询向量。

    Raises:
        HTTPException: cell_id 不存在或向量维度不一致时抛出。
    """
    if vector is not None:
        if dataset.vector_dim and len(vector) != dataset.vector_dim:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"向量维度 {len(vector)} 与数据集维度 {dataset.vector_dim} 不一致",
            )
        return list(vector)
    artifacts = search_service.load_dataset_artifacts(dataset_dir)
    cid_map: dict[str, int] = artifacts["cell_id_to_index"]
    if cell_id not in cid_map:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"cell_id {cell_id} 不存在于数据集 {dataset.id}",
        )
    return artifacts["vectors"][cid_map[cell_id]].tolist()


async def _execute_one_ensemble_search(
    *,
    dataset: Dataset,
    record: IndexRecord,
    dataset_dir: str,
    query_vector: list[float],
    top_k: int,
    filters: dict[str, Any] | None,
    exclude_cell_id: str | None,
) -> dict[str, Any]:
    """ensemble 检索：在解析完元信息后并发执行的单一子任务。

    传入扩大后的 ``top_k * over_fetch`` 以提升合并召回；上层在 merge 阶段再
    取最终 Top-K。本函数不访问 DB，便于在外层 :func:`asyncio.gather` 中并发。
    ``exclude_cell_id`` 在以 cell_id 发起查询时传入，用于剔除自身命中。
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
        exclude_cell_id=exclude_cell_id,
        metric=record.metric,
    )


@router.post(
    "/ensemble",
    response_model=EnsembleSearchResponse,
    summary="多后端 ensemble 检索",
    description=(
        "对同一数据集并发使用 2~5 个 ``status=ready`` 索引（如 ``hnswlib`` + "
        "``faiss-ivfpq``）执行检索；每路结果按 z-score 归一化 ``(d-mean)/std`` 后"
        "再按 ``cell_id`` 聚合：取所有索引中最低（最相似）的归一化分数为 ``score``，"
        "``voted_by`` 列出命中该 cell 的索引 ID。"
        "\n\n"
        "- ``index_ids`` 长度 2~5；少于 2 或大于 5 返回 400；\n"
        "- 所有 ``index_ids`` 必须同属 ``dataset_id`` 且状态为 ``ready``，否则 400；\n"
        "- 单路按 ``top_k * 3`` 取候选以提升合并召回，最终输出严格截断到 ``top_k``。"
    ),
)
async def search_ensemble(
    payload: EnsembleSearchRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> EnsembleSearchResponse:
    """多后端 ensemble 检索。"""
    if (
        len(payload.index_ids) < _ENSEMBLE_MIN_INDEXES
        or len(payload.index_ids) > _ENSEMBLE_MAX_INDEXES
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"index_ids 数量需在 [{_ENSEMBLE_MIN_INDEXES}, {_ENSEMBLE_MAX_INDEXES}] "
                f"区间，当前 {len(payload.index_ids)}"
            ),
        )
    if len(set(payload.index_ids)) != len(payload.index_ids):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="index_ids 中存在重复 ID",
        )

    dataset = await _get_dataset(db, payload.dataset_id)
    if dataset.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"数据集不存在: {payload.dataset_id}"
        )

    records: list[IndexRecord] = []
    for idx_id in payload.index_ids:
        record = await db.get(IndexRecord, idx_id)
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"索引不存在: {idx_id}"
            )
        if record.dataset_id != payload.dataset_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"索引 {idx_id} 不属于数据集 {payload.dataset_id}"
                    f"（实际 dataset_id={record.dataset_id}）"
                ),
            )
        if record.status != "ready":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"索引 {idx_id} 尚未 ready: {record.status}",
            )
        records.append(record)

    dataset_dir = _resolve_dataset_dir(dataset)
    query_vector = await _resolve_query_vector(
        dataset=dataset,
        dataset_dir=dataset_dir,
        cell_id=payload.query.cell_id,
        vector=payload.query.vector,
    )

    fetch_k = int(payload.top_k * _ENSEMBLE_OVER_FETCH_FACTOR)
    start = time.perf_counter()
    per_index = await asyncio.gather(
        *[
            _execute_one_ensemble_search(
                dataset=dataset,
                record=rec,
                dataset_dir=dataset_dir,
                query_vector=query_vector,
                top_k=fetch_k,
                filters=payload.filters,
                exclude_cell_id=payload.query.cell_id,
            )
            for rec in records
        ]
    )
    latency_ms = (time.perf_counter() - start) * 1000.0

    merged = search_service.merge_ensemble_results(
        per_index_results=list(per_index),
        index_ids=[rec.id for rec in records],
        top_k=payload.top_k,
    )
    per_index_latency = {
        str(rec.id): float(p.get("query_time_ms", 0.0))
        for rec, p in zip(records, per_index, strict=True)
    }

    await _log_search(
        db,
        dataset_id=payload.dataset_id,
        user_id=current_user.id,
        top_k=payload.top_k,
        filters={"ensemble": True, "index_ids": payload.index_ids},
        latency_ms=latency_ms,
    )

    hits = [
        EnsembleHit(
            rank=item["rank"],
            cell_id=item["cell_id"],
            score=float(item["score"]),
            voted_by=list(item.get("voted_by", [])),
            meta=item.get("meta") or {},
        )
        for item in merged
    ]
    return EnsembleSearchResponse(
        dataset_id=payload.dataset_id,
        top_k=payload.top_k,
        latency_ms=latency_ms,
        hits=hits,
        per_index_latency_ms=per_index_latency,
    )
