"""检索相关 schema。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SearchByCellId(BaseModel):
    """按细胞 ID 检索请求。"""

    dataset_id: int = Field(..., description="数据集 ID")
    cell_id: str = Field(..., description="细胞编号（obs index）")
    top_k: int = Field(10, ge=1, le=1000, description="返回近邻数量")
    filters: dict[str, Any] | None = Field(
        None, description="元数据过滤，例如 {'cell_type': 'T cell'}"
    )
    index_id: int | None = Field(None, description="指定使用的索引 ID，缺省则使用最新 ready 索引")


class SearchByVector(BaseModel):
    """按自定义向量检索请求。"""

    dataset_id: int = Field(..., description="数据集 ID")
    vector: list[float] = Field(..., description="查询向量，长度需与数据集向量维度一致")
    top_k: int = Field(10, ge=1, le=1000, description="返回近邻数量")
    filters: dict[str, Any] | None = Field(None, description="元数据过滤")
    index_id: int | None = Field(None, description="指定使用的索引 ID")


SearchByIdRequest = SearchByCellId
SearchByVectorRequest = SearchByVector


class MultiDatasetSearchRequest(BaseModel):
    """跨数据集联合检索请求。

    支持通过 ``cell_id``（需与 ``source_dataset_id`` 组合）或显式 ``vector`` 发起查询。
    """

    dataset_ids: list[int] = Field(..., min_length=1, description="参与检索的数据集 ID 列表")
    index_ids: list[int] | None = Field(
        None,
        description="与 ``dataset_ids`` 一一对应的索引 ID 列表；为 ``None`` 时每个数据集自动取最新 ready 索引",
    )
    cell_id: str | None = Field(None, description="查询细胞编号，与 ``source_dataset_id`` 联合解析")
    source_dataset_id: int | None = Field(
        None, description="``cell_id`` 所属数据集 ID，缺省取 ``dataset_ids[0]``"
    )
    vector: list[float] | None = Field(None, description="自定义查询向量；与 ``cell_id`` 二选一")
    top_k: int = Field(10, ge=1, le=1000, description="每个数据集返回近邻数")
    filters: dict[str, Any] | None = Field(None, description="元数据过滤条件")


class SearchHit(BaseModel):
    """单条检索命中结果。"""

    rank: int = Field(..., description="结果排名，从 1 开始")
    cell_id: str = Field(..., description="细胞编号")
    distance: float = Field(..., description="与查询向量的距离/相似度得分")
    meta: dict[str, Any] | None = Field(None, description="细胞元信息")
    source_dataset_id: int | None = Field(
        None, description="该结果所属数据集 ID（多数据集检索时填充）"
    )


SearchResult = SearchHit


class SearchResponse(BaseModel):
    """检索响应。"""

    dataset_id: int | None = Field(None, description="数据集 ID（单数据集检索）")
    top_k: int = Field(..., description="实际返回的数量")
    latency_ms: float = Field(..., description="检索耗时（毫秒）")
    index_backend: str | None = Field(None, description="后端实现名，如 hnswlib")
    metric: str | None = Field(None, description="距离度量")
    total_candidates: int | None = Field(None, description="参与排序的候选数量")
    hits: list[SearchHit] = Field(default_factory=list, description="命中结果列表")
