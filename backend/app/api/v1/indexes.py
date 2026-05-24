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
from sqlalchemy.orm import joinedload

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
from app.schemas.subgraph import SubgraphEdge, SubgraphNode, SubgraphResponse
from app.services import search as search_service
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
    """读取属于当前用户的索引记录及其数据集。

    P3 优化：通过 ``joinedload(IndexRecord.dataset)`` 把原来的"先查 IndexRecord
    再查 Dataset"两次查询合并为单条 LEFT OUTER JOIN，消除潜在的 N+1 模式，
    在批量调用（如评测页轮询）时显著减少 round-trip。
    """
    stmt = (
        select(IndexRecord)
        .options(joinedload(IndexRecord.dataset))
        .where(IndexRecord.id == index_id)
    )
    record = (await db.execute(stmt)).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="索引不存在")
    dataset = record.dataset
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
    description=(
        "按 ``created_at`` 倒序返回指定数据集的全部索引记录。\n\n"
        "P3 优化：通过 ``joinedload(Dataset.indexes)`` 一次性预加载关系，"
        "把『先校验数据集所有权 + 再查 IndexRecord』的两条 SQL 合并为单条"
        " LEFT OUTER JOIN，消除 N+1 路径。"
    ),
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
    stmt = select(Dataset).options(joinedload(Dataset.indexes)).where(Dataset.id == dataset_id)
    dataset = (await db.execute(stmt)).unique().scalar_one_or_none()
    if dataset is None or dataset.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="数据集不存在")
    records = sorted(
        dataset.indexes,
        key=lambda r: (r.created_at, r.id),
        reverse=True,
    )
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
    summary="两层缓存命中率",
    description=(
        "聚合返回**两层缓存**的命中率与计数器："
        "\n\n"
        "- **IndexCache**（进程内 LRU 常驻 ANN 索引）："
        " ``capacity / size / hits / misses / loads / evictions / hit_ratio / cached_index_ids``；\n"
        "- **SearchCache**（Redis 检索结果缓存，F2 引入）："
        " ``search_cache_hits / search_cache_misses / search_cache_errors / search_cache_hit_ratio``。\n\n"
        "用于一次性观测索引常驻缓存与检索结果缓存的协同效果——命中率长期低意味着"
        "工作集大于 capacity 或 TTL 偏短，应相应调大。"
        "鉴权：当前登录用户均可读（无敏感信息）。"
    ),
)
async def get_cache_stats(current_user: CurrentUser) -> dict[str, Any]:  # noqa: ARG001
    """返回 IndexCache 与 SearchCache 两层缓存命中率与内部计数。"""
    from app.services.search_cache import get_cache_metrics  # noqa: PLC0415

    base = IndexCache.instance().stats()
    base.update(get_cache_metrics())
    return base


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


_SUPPORTED_SUBGRAPH_BACKENDS = {"hnswlib", "adaptive-hnsw"}


@router.get(
    "/indexes/{index_id}/subgraph",
    response_model=SubgraphResponse,
    summary="HNSW 邻居子图",
    description=(
        "返回 HNSW 索引在指定 cell 周围的局部邻居图，用于 v1.2 D2 扩展功能的"
        " 小世界图可视化。\n\n"
        "仅 ``hnswlib`` 与 ``adaptive-hnsw`` 后端支持；其他后端返回"
        " ``400 该后端不暴露图结构``。\n\n"
        "Query 参数：\n"
        "- ``cell_id`` (必填)：查询起点 cell_id，会通过数据集 ``cell_ids`` 映射到 hnswlib label；\n"
        "- ``depth`` (默认 ``2``，范围 ``1-3``)：BFS 深度；\n"
        "- ``layer`` (默认 ``0``，范围 ``0-N``)：HNSW 层，0 是底层最稠密；\n"
        "- ``max_nodes`` (默认 ``200``，范围 ``10-500``)：安全上限，防高 depth 爆炸；"
        "命中上限会把响应 ``truncated`` 置为 ``true``。\n\n"
        "实现兼容性：若运行时 hnswlib 不暴露 ``get_neighbors_list`` 邻接表 API，"
        "后端会回退到基于 ``knn_query`` 的 Top-M 近邻图近似（仅用于可视化，"
        "拓扑量级与真实 HNSW 一致但边集不完全相同）。"
    ),
)
async def get_index_subgraph(
    index_id: int,
    current_user: CurrentUser,
    db: DbSession,
    cell_id: str,
    depth: int = 2,
    layer: int = 0,
    max_nodes: int = 200,
) -> SubgraphResponse:
    """返回 HNSW 索引在指定 cell 周围的局部邻居子图（D2 扩展功能）。

    Args:
        index_id: 索引 ID。
        current_user: 当前登录用户（鉴权 + 所有权校验）。
        db: 异步数据库会话。
        cell_id: 查询起点 cell_id。
        depth: BFS 深度，``1-3``。
        layer: HNSW 层。
        max_nodes: 节点数上限。

    Returns:
        SubgraphResponse: 子图节点 / 边 / 元数据。

    Raises:
        HTTPException: 索引不存在 / 非 HNSW 系后端 / cell_id 找不到 / 参数非法。
    """
    if not 1 <= depth <= 3:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"depth 应在 [1, 3]，收到 {depth}",
        )
    if layer < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"layer 应 >= 0，收到 {layer}",
        )
    if not 10 <= max_nodes <= 500:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"max_nodes 应在 [10, 500]，收到 {max_nodes}",
        )

    record, dataset = await _get_owned_index(db, index_id, current_user.id)
    if record.backend not in _SUPPORTED_SUBGRAPH_BACKENDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"该后端不暴露图结构: backend={record.backend}",
        )
    if record.status != "ready":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"索引尚未 ready: {record.status}",
        )

    # 解析 dataset_dir（兼容 vectors_path 既可指文件也可指目录）
    if not dataset.vectors_path:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"数据集 {dataset.id} 缺少预处理向量路径",
        )
    vp = dataset.vectors_path
    dataset_dir = vp if os.path.isdir(vp) else (os.path.dirname(vp) or vp)

    # 拿到后端实例与 cell_ids 映射
    try:
        backend = search_service.get_index_backend(
            index_id=record.id,
            dataset_dir=dataset_dir,
            backend_name=record.backend,
            metric=record.metric,
            dim=dataset.vector_dim,
            index_path=record.index_path,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"加载索引后端失败: {exc}",
        ) from exc

    artifacts = search_service.load_dataset_artifacts(dataset_dir)
    cell_ids: list[str] = artifacts["cell_ids"]
    cid_map: dict[str, int] = artifacts["cell_id_to_index"]
    if cell_id not in cid_map:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"cell_id 不存在: {cell_id}",
        )
    entry_label = int(cid_map[cell_id])

    metadata = artifacts.get("metadata")
    cell_type_col = (
        metadata["cell_type"]
        if metadata is not None and "cell_type" in getattr(metadata, "columns", [])
        else None
    )

    try:
        raw = backend.get_local_subgraph(
            entry_label=entry_label,
            depth=int(depth),
            layer=int(layer),
            max_nodes=int(max_nodes),
        )
    except AttributeError as exc:
        # 后端未实现该接口（理论上 _SUPPORTED_SUBGRAPH_BACKENDS 已拦截，此处兜底）
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"该后端不暴露图结构: {exc}",
        ) from exc
    except IndexError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc

    nodes_out: list[SubgraphNode] = []
    for item in raw["nodes"]:
        label = int(item["id"])
        if label < 0 or label >= len(cell_ids):
            continue
        cid = cell_ids[label]
        ctype: str | None = None
        if cell_type_col is not None:
            v = cell_type_col.iloc[label]
            if v is not None and not (isinstance(v, float) and v != v):  # 非 NaN
                ctype = str(v)
        nodes_out.append(
            SubgraphNode(
                label=label,
                cell_id=cid,
                depth=int(item["depth"]),
                is_entry=(label == entry_label),
                is_topk=False,
                cell_type=ctype,
            )
        )
    edges_out = [SubgraphEdge(src=int(e["src"]), dst=int(e["dst"])) for e in raw["edges"]]

    return SubgraphResponse(
        nodes=nodes_out,
        edges=edges_out,
        entry_label=entry_label,
        entry_cell_id=cell_id,
        layer=int(raw["layer"]),
        depth=int(raw["depth"]),
        truncated=bool(raw["truncated"]),
        backend=record.backend,
    )
