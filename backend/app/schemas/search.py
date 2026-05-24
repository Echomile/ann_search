"""检索相关 schema。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


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


class BatchQueryItem(BaseModel):
    """批量检索单条查询项。

    ``cell_id`` 与 ``vector`` 二选一必填；同时给出时优先按 ``cell_id`` 解析。
    """

    cell_id: str | None = Field(None, description="查询细胞编号（存在时优先生效）")
    vector: list[float] | None = Field(None, description="查询向量；与 ``cell_id`` 二选一")

    @model_validator(mode="after")
    def _check_query_source(self) -> BatchQueryItem:
        """校验 ``cell_id`` 与 ``vector`` 至少给出一项。"""
        if self.cell_id is None and self.vector is None:
            raise ValueError("cell_id 与 vector 必须二选一")
        return self


class BatchSearchRequest(BaseModel):
    """批量检索请求：单数据集 N 个查询并发执行。

    长度范围：``1 <= len(queries) <= 50``；越界由 schema/端点联合拦截。
    """

    dataset_id: int = Field(..., description="数据集 ID")
    index_id: int | None = Field(
        None, description="指定使用的索引 ID；为空时自动取最新 ``status=ready`` 索引"
    )
    queries: list[BatchQueryItem] = Field(..., min_length=1, description="查询项列表，长度 1~50")
    top_k: int = Field(10, ge=1, le=1000, description="每个查询返回的近邻数量")
    filters: dict[str, Any] | None = Field(None, description="所有查询共享的 metadata 过滤条件")


class BatchSearchHitGroup(BaseModel):
    """批量检索单组结果。"""

    query_index: int = Field(..., description="查询索引（与入参 ``queries`` 顺序对齐，从 0 开始）")
    query_cell_id: str | None = Field(None, description="若以 cell_id 发起查询则回填，否则为 None")
    hits: list[SearchHit] = Field(default_factory=list, description="命中结果列表")
    latency_ms: float = Field(..., description="单查询耗时（毫秒）")
    cache_hit: bool = Field(False, description="是否命中 F2 Redis 检索缓存")


class BatchSearchResponse(BaseModel):
    """批量检索响应。"""

    dataset_id: int = Field(..., description="数据集 ID")
    top_k: int = Field(..., description="每个查询返回的近邻数量")
    total_queries: int = Field(..., description="批量查询总数")
    total_latency_ms: float = Field(..., description="批量整体 wall-clock 耗时（毫秒）")
    index_backend: str | None = Field(None, description="后端实现名，如 hnswlib")
    metric: str | None = Field(None, description="距离度量")
    groups: list[BatchSearchHitGroup] = Field(
        default_factory=list, description="按 ``query_index`` 升序的结果分组"
    )


class EnsembleSearchRequest(BaseModel):
    """多后端 ensemble 检索请求。

    在 **同一数据集** 上并发跑 2~5 个 ``status=ready`` 索引（如 ``hnswlib`` +
    ``faiss-ivfpq``），把各路结果按 z-score 归一化后合并，按 cell 取最小归一化
    分数排序，最终输出带 ``voted_by`` 投票信息的 Top-K 命中。
    """

    dataset_id: int = Field(..., description="数据集 ID")
    index_ids: list[int] = Field(
        ...,
        min_length=1,
        description=(
            "参与 ensemble 的索引 ID 列表，需同属一个 ``dataset_id`` 且均为 ``ready``；"
            "长度需落在 ``[2, 5]``，越界由端点统一返回 400"
        ),
    )
    query: BatchQueryItem = Field(..., description="查询项：``cell_id`` 与 ``vector`` 二选一")
    top_k: int = Field(10, ge=1, le=1000, description="最终合并后返回的近邻数量")
    filters: dict[str, Any] | None = Field(None, description="元数据过滤条件，所有索引共享")


class EnsembleHit(BaseModel):
    """ensemble 合并后的单条命中结果。"""

    rank: int = Field(..., description="结果排名，从 1 开始")
    cell_id: str = Field(..., description="细胞编号")
    score: float = Field(
        ...,
        description="集成后的归一化分数（取各索引 z-score 最低值，越小越相似）",
    )
    voted_by: list[int] = Field(
        default_factory=list, description="命中该 cell 的索引 ID 列表（去重升序）"
    )
    meta: dict[str, Any] | None = Field(None, description="细胞元信息")


class EnsembleSearchResponse(BaseModel):
    """多后端 ensemble 检索响应。"""

    dataset_id: int = Field(..., description="数据集 ID")
    top_k: int = Field(..., description="最终返回的近邻数量")
    latency_ms: float = Field(..., description="端到端 wall-clock 耗时（毫秒）")
    hits: list[EnsembleHit] = Field(default_factory=list, description="集成后的命中列表")
    per_index_latency_ms: dict[str, float] = Field(
        default_factory=dict,
        description="各索引 ANN 计算耗时（毫秒），key 为 ``str(index_id)``",
    )


class SearchWithParamsRequest(BaseModel):
    """带运行时参数调整的检索请求（D1 交互式参数仪表盘后端入参）。

    在不重建索引的前提下，通过 ``runtime_params`` 临时调整后端的查询期参数
    （例如 hnswlib 的 ``ef_search`` 或 faiss-ivfpq 的 ``nprobe``），返回新的
    Top-K，便于前端"参数滑块 -> 实时预览"交互。``cell_id`` 与 ``vector``
    二选一。
    """

    dataset_id: int = Field(..., description="数据集 ID")
    index_id: int | None = Field(
        None, description="指定使用的索引 ID；为空时取最新 ``status=ready`` 索引"
    )
    cell_id: str | None = Field(None, description="查询细胞编号，与 ``vector`` 二选一")
    vector: list[float] | None = Field(
        None, description="查询向量；长度需与数据集维度一致，与 ``cell_id`` 二选一"
    )
    top_k: int = Field(10, ge=1, le=1000, description="返回近邻数量")
    runtime_params: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "运行时后端参数；按后端支持的 key 生效：``ef_search``"
            "（hnswlib / adaptive-hnsw / faiss-hnsw）、``nprobe``（faiss-ivfpq）。"
            "不支持的 key 会落入响应的 ``ignored_params``。"
        ),
    )
    filters: dict[str, Any] | None = Field(
        None, description="metadata 过滤条件（与 by-id / by-vector 同语义）"
    )

    @model_validator(mode="after")
    def _check_query_source(self) -> SearchWithParamsRequest:
        """校验 ``cell_id`` 与 ``vector`` 至少给出一项。"""
        if self.cell_id is None and self.vector is None:
            raise ValueError("cell_id 与 vector 必须二选一")
        return self


class SearchResponseWithParams(SearchResponse):
    """``/search/with_params`` 响应：在标准 :class:`SearchResponse` 基础上回填实际生效的参数。"""

    effective_params: dict[str, Any] = Field(
        default_factory=dict,
        description="本次检索实际生效的 runtime 参数（如 ``{'ef_search': 128}``）",
    )
    ignored_params: list[str] = Field(
        default_factory=list,
        description="被忽略的参数 key 列表（不被当前后端支持，或类型转换失败）",
    )
