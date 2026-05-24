"""跨数据集语义对齐 schema (v1.2 D7 加分项)。

约定字段：
    - ``method``: ``intersect_only | harmony``；harmonypy 不可用时降级。
    - ``status``: 状态机 ``pending | running | done | failed``。
    - ``source_dataset_ids``: 解析后的整数列表，对应数据库列
      ``source_dataset_ids_json`` 的反序列化结果。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

AlignMethod = Literal["intersect_only", "harmony"]


class AlignRequest(BaseModel):
    """触发跨数据集对齐请求体。

    Attributes:
        source_dataset_ids: 参与对齐的原始数据集 ID 列表，长度需 >= 2。
        method: 对齐策略；``harmony`` 依赖 harmonypy 包，未安装时降级为
            ``intersect_only`` 并在响应里通过 ``method`` 字段如实回填。
        target_dim: 对齐后向量维度，默认 30。
        name: 对齐数据集名称，不传时根据 ``method`` + ``source_dataset_ids``
            自动生成。
    """

    source_dataset_ids: list[int] = Field(
        ..., min_length=2, description="参与对齐的原始数据集 ID 列表，长度需 >= 2"
    )
    method: AlignMethod = Field("intersect_only", description="对齐方法：intersect_only 或 harmony")
    target_dim: int = Field(30, ge=2, le=512, description="对齐后向量维度，默认 30")
    name: str | None = Field(
        None,
        max_length=255,
        description="对齐数据集名称；不传时按 ``aligned-{method}-{ids}`` 自动生成",
    )


class AlignedDatasetRead(BaseModel):
    """对齐数据集响应体。

    Attributes:
        id: 对齐数据集 ID。
        name: 名称。
        source_dataset_ids: 参与对齐的原始 dataset ID 列表。
        method: 实际使用的对齐方法（harmonypy 缺失时会回填为 intersect_only）。
        target_dim: 向量维度。
        cell_count: 对齐后总细胞数。
        common_genes_count: 基因集交集大小。
        status: 状态机当前值。
        created_by: 触发对齐的用户 ID，可空。
        created_at, updated_at: 时间戳。
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int = Field(..., description="对齐数据集 ID")
    name: str = Field(..., description="对齐数据集名称")
    source_dataset_ids: list[int] = Field(
        ...,
        description="参与对齐的原始数据集 ID 列表",
        validation_alias=AliasChoices("source_dataset_ids", "source_dataset_ids_json"),
    )
    method: str = Field(..., description="对齐方法：intersect_only | harmony")
    target_dim: int = Field(..., description="对齐后向量维度")
    cell_count: int = Field(..., description="对齐后总细胞数")
    common_genes_count: int = Field(..., description="基因集交集大小")
    status: str = Field(..., description="状态：pending | running | done | failed")
    created_by: int | None = Field(None, description="触发对齐的用户 ID")
    created_at: datetime = Field(..., description="创建时间")
    updated_at: datetime = Field(..., description="最近更新时间")

    @field_validator("source_dataset_ids", mode="before")
    @classmethod
    def _parse_source_ids(cls, value: Any) -> Any:
        """允许 ORM 直传 ``source_dataset_ids_json`` 字符串。

        ORM 模型只保存 JSON 字符串，read schema 在序列化时按需反序列化为
        ``list[int]``；同时兼容已经是 list 的输入（手工构造）。
        """
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except (TypeError, ValueError):
                return []
            if isinstance(parsed, list):
                return [int(x) for x in parsed]
            return []
        return value


class AlignedDatasetDeleteResponse(BaseModel):
    """删除对齐数据集响应。"""

    deleted: bool = Field(..., description="是否成功删除")
    aligned_dataset_id: int = Field(..., description="被删除的对齐数据集 ID")
