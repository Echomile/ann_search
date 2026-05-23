"""检索日志统计相关 schema。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DatasetStat(BaseModel):
    """单个数据集的检索统计聚合项。"""

    dataset_id: int = Field(..., description="数据集 ID")
    dataset_name: str | None = Field(
        None,
        description="数据集名称；当数据集已被删除而日志仍存在时为空。",
    )
    total_queries: int = Field(..., description="该数据集累计查询次数（不限时间窗）")
    avg_latency_ms: float = Field(..., description="平均检索延迟（毫秒）")
    p95_latency_ms: float = Field(..., description="P95 检索延迟（毫秒）")


class HourlyBucket(BaseModel):
    """最近 24 小时按小时聚合的检索分桶。"""

    hour_iso: str = Field(
        ...,
        description="ISO 8601 UTC 整点时间，例如 ``2026-05-23T10:00:00Z``。",
    )
    queries: int = Field(..., description="该小时查询次数；无数据则为 0")
    avg_latency_ms: float = Field(..., description="该小时平均延迟（毫秒）；无数据则为 0")


class SearchStatsResponse(BaseModel):
    """检索日志统计响应。"""

    total_queries: int = Field(..., description="累计查询次数（所有时间）")
    overall_avg_latency_ms: float = Field(..., description="整体平均检索延迟（毫秒）")
    overall_p95_latency_ms: float = Field(..., description="整体 P95 检索延迟（毫秒）")
    by_dataset: list[DatasetStat] = Field(
        default_factory=list,
        description="按数据集分组的统计列表，按 ``dataset_id`` 升序排列。",
    )
    hourly_24h: list[HourlyBucket] = Field(
        default_factory=list,
        description="最近 24 小时按小时聚合的时间序列，长度固定为 24，缺数据补 0。",
    )
