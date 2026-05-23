"""数据集相关 schema 定义。

约定字段：
    - ``status``: 取值 ``uploading | preprocessing | ready | failed``；
    - ``vector_source``: ``X_pca``、``X_scvi``、``X`` 等；
    - ``meta_columns``: 预处理阶段选出的可过滤离散列名列表。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DatasetBase(BaseModel):
    """数据集基础字段。"""

    name: str = Field(..., min_length=1, max_length=255, description="数据集名称")


class DatasetCreate(DatasetBase):
    """创建数据集请求体。"""


class DatasetUpdate(BaseModel):
    """数据集字段更新请求体（PATCH 语义，全部字段可选）。"""

    name: str | None = Field(
        None, min_length=1, max_length=255, description="新的数据集名称（不修改请勿传）"
    )


class DatasetOut(DatasetBase):
    """数据集详情响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="数据集 ID")
    owner_id: int = Field(..., description="拥有者用户 ID")
    status: str = Field(..., description="状态：uploading | preprocessing | ready | failed")
    cell_count: int | None = Field(None, description="细胞数量")
    vector_dim: int | None = Field(None, description="向量维度")
    vector_source: str | None = Field(None, description="向量来源，如 X_pca、X_scvi、X")
    meta_columns: list[str] | None = Field(None, description="可作为过滤条件的离散列名列表")
    created_at: datetime = Field(..., description="创建时间")


class DatasetStatus(BaseModel):
    """数据集状态视图，用于 ``GET /datasets/{id}/status``。"""

    model_config = ConfigDict(from_attributes=True)

    dataset_id: int = Field(..., description="数据集 ID")
    status: str = Field(..., description="状态：uploading | preprocessing | ready | failed")
    cell_count: int | None = Field(None, description="细胞数量")
    vector_dim: int | None = Field(None, description="向量维度")
    vector_source: str | None = Field(None, description="向量来源")
    meta_columns: list[str] | None = Field(None, description="可作为过滤条件的离散列名列表")


class DatasetUploadResponse(BaseModel):
    """上传数据集响应。"""

    dataset: DatasetOut = Field(..., description="新建的数据集")
    task_id: str = Field(..., description="入队的预处理任务 ID，未启用 Redis 时返回空串")


class DatasetDeleteResponse(BaseModel):
    """删除数据集响应。"""

    deleted: bool = Field(..., description="是否成功删除")
    dataset_id: int = Field(..., description="被删除的数据集 ID")


class UmapResponse(BaseModel):
    """数据集 UMAP 2D 坐标响应。"""

    dataset_id: int = Field(..., description="数据集 ID")
    has_umap: bool = Field(..., description="是否存在已计算的 UMAP 2D 坐标")
    coords: list[list[float]] | None = Field(
        None,
        description="形如 (N, 2) 的 UMAP 坐标数组，缺失时为 null",
    )
    cell_ids: list[str] | None = Field(
        None,
        description="与 coords 行索引一一对应的 cell_id 列表，缺失时为 null",
    )
    sampled: bool = Field(
        ...,
        description="是否触发下采样：原始 N > 50000 时随机下采样至 50000 防止前端崩溃",
    )
    total_cells: int = Field(..., description="该数据集的原始细胞总数（下采样前）")


class OrphanCleanupResponse(BaseModel):
    """孤儿数据集批量清理响应。

    Attributes:
        deleted_ids: 被清理的数据集 ID 列表，按清理顺序排列。
        count: 被清理的数据集数量，等价于 ``len(deleted_ids)``。
    """

    deleted_ids: list[int] = Field(default_factory=list, description="被清理的数据集 ID 列表")
    count: int = Field(..., description="被清理的数据集数量")


class UploadProgressResponse(BaseModel):
    """数据集上传 / 写盘进度响应。

    供前端在 ``axios.onUploadProgress`` 完成后轮询使用，区分浏览器侧"字节进入网络"
    与后端 8 MB 分块写盘的真实进度，并能继续衔接到 Scanpy 预处理阶段。

    Attributes:
        dataset_id: 数据集 ID。
        status: 数据集当前状态：``uploading | preprocessing | ready | failed``。
        bytes_received: 已写盘字节数；非 ``uploading`` 状态可能为 ``None``。
        total_bytes: 上传文件总字节数；``starlette`` 流式上传时可能为 ``None``。
        percent: 进度百分比 ``0..100``；``total_bytes`` 缺失时为 ``None``，前端按 indeterminate 处理。
    """

    dataset_id: int = Field(..., description="数据集 ID")
    status: str = Field(..., description="状态：uploading | preprocessing | ready | failed")
    bytes_received: int | None = Field(
        None, description="已写盘字节数；非 uploading 状态可能为 null"
    )
    total_bytes: int | None = Field(None, description="文件总字节数；streaming 上传时可能 null")
    percent: float | None = Field(None, description="进度百分比 0..100；total_bytes 缺失时为 null")
