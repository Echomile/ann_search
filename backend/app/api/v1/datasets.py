"""数据集 CRUD 路由。

所有接口都要求登录（``Depends(get_current_user)``），并仅允许操作自己拥有的数据集。

接口列表：
    - ``POST   /datasets/upload``: 流式上传 .h5ad 并入队预处理任务（同名返回 409）；
    - ``GET    /datasets``: 列出当前用户拥有的数据集；
    - ``DELETE /datasets/orphan``: 批量清理 ``status=failed`` 或向量文件缺失的孤儿数据集；
    - ``GET    /datasets/{id}``: 数据集详情；
    - ``DELETE /datasets/{id}``: 删除数据集（含磁盘文件与索引目录）；
    - ``GET    /datasets/{id}/status``: 查询数据集预处理状态摘要；
    - ``GET    /datasets/{id}/upload-progress``: 查询数据集上传 / 写盘进度（供前端轮询）；
    - ``GET    /datasets/{id}/umap``: 获取数据集 UMAP 2D 坐标用于可视化。
"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from app.api.deps import CurrentUser, DbSession
from app.core.logging import get_logger
from app.models.dataset import Dataset
from app.schemas.dataset import (
    DatasetDeleteResponse,
    DatasetOut,
    DatasetStatus,
    DatasetUpdate,
    DatasetUploadResponse,
    OrphanCleanupResponse,
    UmapResponse,
    UploadProgressResponse,
)
from app.services import dataset_service
from app.tasks.preprocess_task import enqueue_preprocess

router = APIRouter(prefix="/datasets", tags=["datasets"])
logger = get_logger(__name__)


def _ensure_owner(ds: Dataset | None, user_id: int) -> Dataset:
    """校验数据集存在且属于当前用户，否则抛出对应 HTTP 异常。

    Args:
        ds: 查询到的数据集对象（可能为 ``None``）。
        user_id: 当前用户 ID。

    Raises:
        HTTPException: 不存在返回 ``404``，非拥有者返回 ``403``。

    Returns:
        Dataset: 校验通过的数据集对象。
    """
    if ds is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="数据集不存在")
    if ds.owner_id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该数据集")
    return ds


@router.post(
    "/upload",
    response_model=DatasetUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="上传数据集",
    description=(
        "通过 ``multipart/form-data`` 上传 ``.h5ad`` 文件。"
        "落盘流程：先创建 ``status=uploading`` 的数据集记录获取 ID，"
        "然后以 8 MB 分块流式写盘（每块写完后把已写字节数登记到进程内进度字典，"
        "供 ``GET /datasets/{id}/upload-progress`` 实时读取），"
        "最后把 ``preprocess_dataset`` 任务入队 ARQ（Redis 不可用时 ``task_id`` 为空串）。"
        "写盘失败或文件为空会回滚 DB 行与磁盘文件，并清理进度记录。"
    ),
)
async def upload_dataset(
    current_user: CurrentUser,
    db: DbSession,
    name: str = Form(..., min_length=1, max_length=255, description="数据集名称"),
    file: UploadFile = File(..., description="待上传的 .h5ad 文件"),
) -> DatasetUploadResponse:
    """上传 .h5ad 并入队预处理任务。

    Args:
        current_user: 当前登录用户。
        db: 异步数据库会话。
        name: 数据集名称。
        file: 上传的文件。

    Returns:
        DatasetUploadResponse: 新建的数据集与预处理任务 ID。
    """
    if await dataset_service.name_exists(db, owner_id=current_user.id, name=name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="数据集名称已存在，请换个名字或先删除旧的",
        )

    raw_dir = dataset_service.build_raw_path(current_user.id)
    target = raw_dir / dataset_service.gen_h5ad_filename()

    ds = await dataset_service.create_dataset(
        db,
        owner_id=current_user.id,
        name=name,
        h5ad_path=str(target),
    )
    total_bytes = file.size

    dataset_service.set_upload_progress(ds.id, 0, total_bytes)

    async def _rollback_dataset() -> None:
        """写盘失败时回滚：删半成品文件 + 删 DB 行 + 清进度。"""
        target.unlink(missing_ok=True)
        dataset_service.clear_upload_progress(ds.id)
        try:
            await db.delete(ds)
            await db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("回滚上传失败的数据集行失败 dataset_id=%s err=%s", ds.id, exc)

    try:
        written = await dataset_service.stream_to_disk(
            file,
            target,
            on_chunk=lambda n: dataset_service.set_upload_progress(ds.id, n, total_bytes),
        )
    except Exception as exc:
        await _rollback_dataset()
        logger.exception("写入上传文件失败 user_id=%s err=%s", current_user.id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="文件写入失败",
        ) from exc
    finally:
        await file.close()

    if written == 0:
        await _rollback_dataset()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="上传文件为空",
        )

    dataset_service.clear_upload_progress(ds.id)

    try:
        task_id = await enqueue_preprocess(ds.id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("入队预处理任务失败 dataset_id=%s err=%s", ds.id, exc)
        task_id = ""

    return DatasetUploadResponse(
        dataset=DatasetOut.model_validate(ds),
        task_id=task_id,
    )


@router.get(
    "",
    response_model=list[DatasetOut],
    summary="数据集列表",
    description="返回当前用户拥有的数据集，按创建时间倒序排列。",
)
async def list_datasets(current_user: CurrentUser, db: DbSession) -> list[DatasetOut]:
    """列出当前用户拥有的数据集。

    Args:
        current_user: 当前登录用户。
        db: 异步数据库会话。

    Returns:
        list[DatasetOut]: 数据集列表。
    """
    rows = await dataset_service.list_user_datasets(db, current_user.id)
    return [DatasetOut.model_validate(ds) for ds in rows]


@router.delete(
    "/orphan",
    response_model=OrphanCleanupResponse,
    summary="清理失败数据集",
    description=(
        "批量删除当前用户名下所有 ``status='failed'`` 或缺失向量文件 "
        "(``status='ready'`` 但 ``vectors_path`` 为空 / 文件不存在) 的孤儿数据集，"
        "并清理对应的磁盘文件、``processed/{id}`` 与 ``indexes/{id}`` 目录。"
        "返回被清理的数据集 ID 列表与数量；如无任何孤儿，``count=0``、``deleted_ids=[]``。"
    ),
)
async def cleanup_orphan_datasets(
    current_user: CurrentUser,
    db: DbSession,
) -> OrphanCleanupResponse:
    """批量清理孤儿数据集。

    Args:
        current_user: 当前登录用户。
        db: 异步数据库会话。

    Returns:
        OrphanCleanupResponse: 包含 ``deleted_ids`` 与 ``count`` 的响应。
    """
    deleted_ids = await dataset_service.cleanup_orphan_datasets(db, current_user.id)
    return OrphanCleanupResponse(deleted_ids=deleted_ids, count=len(deleted_ids))


@router.get(
    "/{dataset_id}",
    response_model=DatasetOut,
    summary="数据集详情",
    description="返回指定数据集的详细信息；不存在返回 ``404``，非拥有者返回 ``403``。",
)
async def get_dataset(
    dataset_id: int,
    current_user: CurrentUser,
    db: DbSession,
) -> DatasetOut:
    """获取数据集详情。

    Args:
        dataset_id: 数据集 ID。
        current_user: 当前登录用户。
        db: 异步数据库会话。

    Returns:
        DatasetOut: 数据集详情。
    """
    ds = await dataset_service.get_dataset(db, dataset_id)
    ds = _ensure_owner(ds, current_user.id)
    return DatasetOut.model_validate(ds)


@router.patch(
    "/{dataset_id}",
    response_model=DatasetOut,
    summary="重命名数据集",
    description=(
        "局部更新数据集字段，目前仅支持 ``name``。"
        "重名（同用户名下已存在）返回 ``409``，非拥有者返回 ``403``，不存在返回 ``404``；"
        "传入 ``name`` 与现值相同时直接返回原数据集。"
    ),
)
async def update_dataset(
    dataset_id: int,
    payload: DatasetUpdate,
    current_user: CurrentUser,
    db: DbSession,
) -> DatasetOut:
    """重命名数据集。

    Args:
        dataset_id: 目标数据集 ID。
        payload: PATCH 字段（仅 ``name``）。
        current_user: 当前登录用户。
        db: 异步数据库会话。

    Returns:
        DatasetOut: 更新后的数据集。

    Raises:
        HTTPException: 404 不存在 / 403 非拥有者 / 409 同名占用 / 400 无字段更新。
    """
    ds = await dataset_service.get_dataset(db, dataset_id)
    ds = _ensure_owner(ds, current_user.id)
    if payload.name is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="请至少提供一个可更新字段"
        )
    try:
        ds = await dataset_service.rename_dataset(db, dataset=ds, new_name=payload.name)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="数据集名称已存在，请换个名字或先删除旧的",
        ) from None
    return DatasetOut.model_validate(ds)


@router.delete(
    "/{dataset_id}",
    response_model=DatasetDeleteResponse,
    summary="删除数据集",
    description=(
        "删除数据集 DB 记录（级联到 ``index_records``），并清理磁盘上的"
        " ``h5ad_path``、``vectors_path`` 以及 ``processed/{id}``、``indexes/{id}`` 目录。"
        "不存在返回 ``404``，非拥有者返回 ``403``。"
    ),
)
async def delete_dataset(
    dataset_id: int,
    current_user: CurrentUser,
    db: DbSession,
) -> DatasetDeleteResponse:
    """删除数据集及其磁盘文件。

    Args:
        dataset_id: 数据集 ID。
        current_user: 当前登录用户。
        db: 异步数据库会话。

    Returns:
        DatasetDeleteResponse: 删除结果。
    """
    ds = await dataset_service.get_dataset(db, dataset_id)
    ds = _ensure_owner(ds, current_user.id)
    await dataset_service.delete_dataset(db, ds)
    return DatasetDeleteResponse(deleted=True, dataset_id=dataset_id)


@router.get(
    "/{dataset_id}/status",
    response_model=DatasetStatus,
    summary="数据集状态",
    description=(
        "返回数据集的状态摘要，包括预处理状态、细胞数、向量维度、向量来源以及"
        "可作为过滤条件的元信息列名列表。常用于前端轮询查看预处理进度。"
    ),
)
async def get_dataset_status(
    dataset_id: int,
    current_user: CurrentUser,
    db: DbSession,
) -> DatasetStatus:
    """获取数据集状态摘要。

    Args:
        dataset_id: 数据集 ID。
        current_user: 当前登录用户。
        db: 异步数据库会话。

    Returns:
        DatasetStatus: 状态摘要。
    """
    ds = await dataset_service.get_dataset(db, dataset_id)
    ds = _ensure_owner(ds, current_user.id)
    return DatasetStatus(
        dataset_id=ds.id,
        status=ds.status,
        cell_count=ds.cell_count,
        vector_dim=ds.vector_dim,
        vector_source=ds.vector_source,
        meta_columns=ds.meta_columns,  # type: ignore[arg-type]
    )


@router.get(
    "/{dataset_id}/upload-progress",
    response_model=UploadProgressResponse,
    summary="数据集上传 / 写盘进度",
    description=(
        "返回数据集的后端写盘进度，供前端在 ``axios.onUploadProgress`` 完成后轮询使用。"
        "``status=uploading`` 时返回 ``bytes_received`` / ``total_bytes`` / ``percent``，"
        "区别于浏览器侧的字节传输进度；进入 ``preprocessing`` 后这三项可能为 ``null``，"
        "前端可按 indeterminate 切到 Scanpy 预处理文案。"
        "不存在返回 ``404``，非拥有者返回 ``403``。"
    ),
)
async def get_dataset_upload_progress(
    dataset_id: int,
    current_user: CurrentUser,
    db: DbSession,
) -> UploadProgressResponse:
    """获取数据集上传 / 写盘进度。

    Args:
        dataset_id: 数据集 ID。
        current_user: 当前登录用户。
        db: 异步数据库会话。

    Returns:
        UploadProgressResponse: 含状态、已写盘字节数与百分比的进度对象。
    """
    ds = await dataset_service.get_dataset(db, dataset_id)
    ds = _ensure_owner(ds, current_user.id)

    record = dataset_service.get_upload_progress(dataset_id)
    bytes_received: int | None = None
    total_bytes: int | None = None
    percent: float | None = None
    if record is not None:
        raw_received = record.get("bytes_received")
        raw_total = record.get("total_bytes")
        bytes_received = int(raw_received) if raw_received is not None else None
        total_bytes = int(raw_total) if raw_total is not None else None
        if bytes_received is not None and total_bytes:
            percent = round(bytes_received / total_bytes * 100, 2)

    return UploadProgressResponse(
        dataset_id=dataset_id,
        status=ds.status,
        bytes_received=bytes_received,
        total_bytes=total_bytes,
        percent=percent,
    )


@router.get(
    "/{dataset_id}/umap",
    response_model=UmapResponse,
    summary="获取 UMAP 2D 坐标",
    description=(
        "返回数据集的 UMAP 2D 散点坐标，供前端 Plotly 可视化使用。"
        "对超过 50 000 细胞的数据集，使用固定种子 ``RandomState(42)`` 等概率下采样到 50 000 个，"
        "并通过 ``sampled=true`` 标记，避免大数据集把前端浏览器拖崩。"
        "若数据集尚未生成 UMAP（``data/processed/{id}/umap_2d.npy`` 缺失），"
        "返回 ``has_umap=false`` 与空坐标，HTTP 状态码仍为 ``200``，方便前端做兜底降级。"
        "不存在返回 ``404``，非拥有者返回 ``403``。"
    ),
)
async def get_dataset_umap(
    dataset_id: int,
    current_user: CurrentUser,
    db: DbSession,
) -> UmapResponse:
    """获取数据集 UMAP 2D 坐标。

    Args:
        dataset_id: 数据集 ID。
        current_user: 当前登录用户。
        db: 异步数据库会话。

    Returns:
        UmapResponse: UMAP 坐标响应；文件缺失时 ``has_umap=False`` 且 ``coords=None``。
    """
    ds = await dataset_service.get_dataset(db, dataset_id)
    ds = _ensure_owner(ds, current_user.id)

    coords, cell_ids, sampled, total = dataset_service.load_umap_2d(dataset_id)
    has_umap = coords is not None
    return UmapResponse(
        dataset_id=dataset_id,
        has_umap=has_umap,
        coords=coords,
        cell_ids=cell_ids,
        sampled=sampled,
        total_cells=total if total > 0 else (ds.cell_count or 0),
    )
