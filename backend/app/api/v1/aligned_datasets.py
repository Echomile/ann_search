"""跨数据集语义对齐路由 (v1.2 D7 扩展功能)。

所有接口都要求登录（``Depends(get_current_user)``）。对齐数据集本身不强制所有权
（不同用户可共享对齐结果），但 :func:`align_endpoint` 会校验调用方对所有
``source_dataset_ids`` 的拥有权，避免越权对齐他人的数据。

接口列表：
    - ``POST   /datasets/align``         : 触发跨数据集对齐（同步）；
    - ``GET    /datasets/aligned``       : 列出当前用户创建的对齐数据集；
    - ``GET    /datasets/aligned/{id}``  : 对齐数据集详情；
    - ``DELETE /datasets/aligned/{id}``  : 删除对齐数据集（含磁盘文件）。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core.logging import get_logger
from app.models.aligned_dataset import AlignedDataset
from app.models.dataset import Dataset
from app.schemas.aligned import (
    AlignedDatasetDeleteResponse,
    AlignedDatasetRead,
    AlignRequest,
)
from app.services import alignment as alignment_service

router = APIRouter(prefix="/datasets", tags=["aligned-datasets"])
logger = get_logger(__name__)


@router.post(
    "/align",
    response_model=AlignedDatasetRead,
    status_code=status.HTTP_201_CREATED,
    summary="触发跨数据集对齐",
    description=(
        "同步执行跨数据集对齐，把多个原始 ``Dataset`` 的细胞统一到同一向量空间。\n\n"
        "- ``method=intersect_only`` (默认)：取基因集交集，在统一 gene 空间上重新跑"
        " ``normalize_total + log1p + scale + PCA(target_dim)``；\n"
        "- ``method=harmony``：intersect 之后调用 ``harmonypy`` 做 batch correction；"
        "harmonypy 未安装时优雅降级为 ``intersect_only``，响应里通过 ``method`` 字段如实回填；\n"
        "- ``source_dataset_ids`` 长度必须 >= 2，且全部归属当前用户；\n"
        "- 同步实现，预期 < 60s for ≤3 datasets。\n\n"
        "落盘到 ``{DATA_DIR}/aligned/{id}/``，包含 ``vectors.npy`` + ``cell_ids.json``"
        " + ``cell_map.json`` + ``metadata.parquet``。"
    ),
)
async def align_endpoint(
    req: AlignRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> AlignedDatasetRead:
    """触发跨数据集对齐（同步）。

    Args:
        req: 对齐请求参数。
        current_user: 当前登录用户。
        db: 异步数据库会话。

    Returns:
        AlignedDatasetRead: 新建的对齐数据集详情。

    Raises:
        HTTPException: 403 越权 / 404 数据集不存在 / 422 source_dataset_ids
            参数非法（如长度 < 2）/ 500 对齐流程内部错误。
    """
    # 校验所有 source dataset 归属当前用户
    for ds_id in req.source_dataset_ids:
        ds = await db.get(Dataset, ds_id)
        if ds is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"数据集不存在: {ds_id}"
            )
        if ds.owner_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"无权访问数据集 {ds_id}",
            )

    try:
        aligned_id = await alignment_service.align_datasets(
            session=db,
            dataset_ids=list(req.source_dataset_ids),
            method=req.method,
            target_dim=int(req.target_dim),
            user_id=current_user.id,
            name=req.name,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except RuntimeError as exc:
        logger.exception("对齐流程失败: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc

    aligned = await db.get(AlignedDataset, aligned_id)
    if aligned is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="对齐数据集创建后无法读回",
        )
    return AlignedDatasetRead.model_validate(aligned)


@router.get(
    "/aligned",
    response_model=list[AlignedDatasetRead],
    summary="列出对齐数据集",
    description=(
        "返回当前用户创建的所有对齐数据集，按 ``created_at`` 倒序。"
        "``created_by`` 为空（系统任务）的记录对所有登录用户可见。"
    ),
)
async def list_aligned(
    current_user: CurrentUser,
    db: DbSession,
) -> list[AlignedDatasetRead]:
    """列出对齐数据集。"""
    stmt = (
        select(AlignedDataset)
        .where(
            (AlignedDataset.created_by == current_user.id) | (AlignedDataset.created_by.is_(None))
        )
        .order_by(AlignedDataset.created_at.desc(), AlignedDataset.id.desc())
    )
    result = await db.execute(stmt)
    rows = list(result.scalars().all())
    return [AlignedDatasetRead.model_validate(r) for r in rows]


@router.get(
    "/aligned/{aligned_id}",
    response_model=AlignedDatasetRead,
    summary="对齐数据集详情",
    description=(
        "返回指定对齐数据集的详细信息；不存在返回 ``404``，"
        "``created_by`` 非空且不匹配当前用户时返回 ``403``。"
    ),
)
async def get_aligned(
    aligned_id: int,
    current_user: CurrentUser,
    db: DbSession,
) -> AlignedDatasetRead:
    """获取对齐数据集详情。"""
    aligned = await db.get(AlignedDataset, aligned_id)
    if aligned is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="对齐数据集不存在")
    if aligned.created_by is not None and aligned.created_by != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该对齐数据集")
    return AlignedDatasetRead.model_validate(aligned)


@router.delete(
    "/aligned/{aligned_id}",
    response_model=AlignedDatasetDeleteResponse,
    summary="删除对齐数据集",
    description=(
        "删除对齐数据集 DB 记录，并清理 ``{DATA_DIR}/aligned/{id}/`` 整个目录。"
        "不存在返回 ``404``，非拥有者返回 ``403``。"
    ),
)
async def delete_aligned(
    aligned_id: int,
    current_user: CurrentUser,
    db: DbSession,
) -> AlignedDatasetDeleteResponse:
    """删除对齐数据集。"""
    aligned = await db.get(AlignedDataset, aligned_id)
    if aligned is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="对齐数据集不存在")
    if aligned.created_by is not None and aligned.created_by != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该对齐数据集")

    alignment_service.cleanup_aligned_files(aligned)
    await db.delete(aligned)
    await db.commit()
    return AlignedDatasetDeleteResponse(deleted=True, aligned_dataset_id=aligned_id)
