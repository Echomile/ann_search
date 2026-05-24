"""参数扫描相关 Pydantic schema (v1.2 C3 加分项)。

字段约定：
    - ``backends`` 取值集合：``hnswlib | faiss-hnsw | faiss-ivfpq | brute |
      adaptive-hnsw``，与 :func:`app.services.ann.factory.create_backend` 对齐。
    - ``ef_search_grid``：用于 ``hnswlib`` / ``faiss-hnsw`` / ``adaptive-hnsw``。
    - ``nprobe_grid``：用于 ``faiss-ivfpq``。
    - ``brute`` 不接受任何查询期参数，仅产出 1 个数据点 (recall=1.0)。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_DEFAULT_EF_GRID: tuple[int, ...] = (16, 32, 64, 128, 256, 512)
_DEFAULT_NPROBE_GRID: tuple[int, ...] = (4, 8, 16, 32, 64, 128)


class SweepRunCreate(BaseModel):
    """触发一次参数扫描的请求体。"""

    dataset_id: int = Field(..., description="目标数据集 ID")
    backends: list[str] = Field(
        ...,
        min_length=1,
        description=(
            "需要扫描的 ANN 后端列表，取值: "
            "``hnswlib | faiss-hnsw | faiss-ivfpq | brute | adaptive-hnsw``"
        ),
    )
    top_k: int = Field(10, ge=1, le=1000, description="评测使用的 Top-K，默认 10")
    query_count: int = Field(200, ge=1, le=10000, description="评测使用的查询样本数，默认 200")
    ef_search_grid: list[int] | None = Field(
        None,
        description=(
            "针对 hnswlib / faiss-hnsw / adaptive-hnsw 的 ef_search 取值列表，"
            "缺省使用 [16, 32, 64, 128, 256, 512]"
        ),
    )
    nprobe_grid: list[int] | None = Field(
        None,
        description=("针对 faiss-ivfpq 的 nprobe 取值列表，缺省使用 [4, 8, 16, 32, 64, 128]"),
    )


class SweepPointRead(BaseModel):
    """扫描单个数据点输出。"""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="数据点主键")
    backend: str = Field(..., description="后端名")
    params_json: dict[str, Any] = Field(
        default_factory=dict, description="查询期参数，例如 {ef_search: 64}"
    )
    recall: float = Field(..., description="Recall@top_k")
    qps: float = Field(..., description="单线程吞吐 (queries per second)")
    p50_ms: float = Field(..., description="P50 延迟 (ms)")
    p95_ms: float = Field(..., description="P95 延迟 (ms)")
    p99_ms: float | None = Field(None, description="P99 延迟 (ms)")
    mem_mb: float = Field(..., description="索引内存占用 (MB)")
    on_pareto: bool = Field(..., description="是否在 (recall, qps) 帕累托前沿")
    created_at: datetime = Field(..., description="创建时间")


class SweepRunRead(BaseModel):
    """扫描任务详情，附带嵌入的数据点列表。"""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="扫描任务主键")
    dataset_id: int = Field(..., description="数据集 ID")
    created_by: int | None = Field(None, description="触发者用户 ID，可空")
    status: str = Field(..., description="状态: pending | running | done | failed")
    top_k: int = Field(..., description="评测 Top-K")
    query_count: int = Field(..., description="评测查询样本数")
    started_at: datetime = Field(..., description="任务开始时间")
    finished_at: datetime | None = Field(None, description="任务完成时间")
    error: str | None = Field(None, description="失败时的错误信息")
    created_at: datetime = Field(..., description="创建时间")
    points: list[SweepPointRead] = Field(
        default_factory=list,
        description="按 recall 升序排列的全部数据点 (含 on_pareto 标记)",
    )
    pareto_count: int = Field(0, description="``on_pareto=True`` 的数据点数量")


def default_ef_search_grid() -> list[int]:
    """返回默认 ef_search 扫描栅格，避免在多处硬编码。"""
    return list(_DEFAULT_EF_GRID)


def default_nprobe_grid() -> list[int]:
    """返回默认 nprobe 扫描栅格，避免在多处硬编码。"""
    return list(_DEFAULT_NPROBE_GRID)
