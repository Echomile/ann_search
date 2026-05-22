"""数据集 CRUD 路由。

所有接口都要求登录（``Depends(get_current_user)``），并仅允许操作自己拥有的数据集。

接口列表：
    - ``POST   /datasets/upload``: 流式上传 .h5ad 并入队预处理任务；
    - ``GET    /datasets``: 列出当前用户拥有的数据集；
    - ``GET    /datasets/{id}``: 数据集详情；
    - ``DELETE /datasets/{id}``: 删除数据集（含磁盘文件与索引目录）；
    - ``GET    /datasets/{id}/status``: 查询数据集预处理状态摘要。
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
    DatasetUploadResponse,
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
        "服务端以 8 MB 分块流式写入磁盘，避免 GB 级文件 OOM；"
        "落盘成功后创建 ``status=uploading`` 的数据集记录，并立即把"
        " ``preprocess_dataset`` 任务入队 ARQ（Redis 不可用时 ``task_id`` 为空串）。"
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
    raw_dir = dataset_service.build_raw_path(current_user.id)
    target = raw_dir / dataset_service.gen_h5ad_filename()

    try:
        written = await dataset_service.stream_to_disk(file, target)
    except Exception as exc:
        target.unlink(missing_ok=True)
        logger.exception("写入上传文件失败 user_id=%s err=%s", current_user.id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="文件写入失败",
        ) from exc
    finally:
        await file.close()

    if written == 0:
        target.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="上传文件为空",
        )

    ds = await dataset_service.create_dataset(
        db,
        owner_id=current_user.id,
        name=name,
        h5ad_path=str(target),
    )

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
