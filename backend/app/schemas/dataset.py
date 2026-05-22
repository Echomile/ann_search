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
