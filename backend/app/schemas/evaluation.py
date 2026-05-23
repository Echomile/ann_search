"""索引评测相关 schema。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class BenchmarkRequest(BaseModel):
    """发起索引基准评测请求。"""

    index_id: int = Field(..., description="待评测索引 ID")
    num_queries: int = Field(100, ge=1, le=10000, description="评测使用的查询样本数量")
    top_k_list: list[int] = Field(
        default_factory=lambda: [10, 100],
        description="Recall 评测使用的 Top-K 取值列表",
    )
    concurrency_list: list[int] = Field(
        default_factory=lambda: [1, 4, 8, 16],
        description="并发压测使用的并发数列表",
    )


class LatencyStats(BaseModel):
    """单一并发档位的延迟统计。"""

    concurrency: int = Field(..., description="并发线程数")
    p50_ms: float = Field(..., description="P50 延迟（毫秒）")
    p95_ms: float = Field(..., description="P95 延迟（毫秒）")
    p99_ms: float = Field(..., description="P99 延迟（毫秒）")
    qps: float = Field(..., description="平均 QPS")
    mean_ms: float = Field(..., description="平均延迟（毫秒）")
    total_queries: int = Field(..., description="本档位总查询数")


class BenchmarkResult(BaseModel):
    """索引评测完整结果。"""

    index_id: int = Field(..., description="索引 ID")
    dataset_id: int | None = Field(None, description="对应数据集 ID")
    backend: str = Field(..., description="索引后端")
    metric: str | None = Field(None, description="距离度量")
    build_time_seconds: float | None = Field(None, description="索引构建耗时（秒）")
    memory_mb: float | None = Field(None, description="索引内存占用（MB）")
    num_queries: int = Field(..., description="实际评测的查询数")
    recalls: dict[str, float] = Field(
        default_factory=dict,
        description="不同 Top-K 下的 Recall，键为 K 的字符串形式",
    )
    latencies: list[LatencyStats] = Field(
        default_factory=list, description="按并发档位统计的延迟与 QPS"
    )
    finished_at: datetime | None = Field(None, description="评测完成时间")


class BenchmarkTaskHandle(BaseModel):
    """评测任务入队句柄。"""

    task_id: str = Field(..., description="ARQ 任务 ID，可用于结果查询")
    index_id: int = Field(..., description="本次评测的索引 ID")
    status: str = Field("queued", description="任务状态")


class SearchLogByDataset(BaseModel):
    """按数据集聚合的检索日志统计。"""

    dataset_id: int = Field(..., description="数据集 ID")
    dataset_name: str = Field(..., description="数据集名称，缺失时回退为 ``#ID``")
    total_queries: int = Field(..., description="该数据集下的检索次数")
    avg_latency_ms: float = Field(..., description="平均延迟（毫秒）")
    p95_latency_ms: float = Field(..., description="P95 延迟（毫秒）")


class SearchLogHourBucket(BaseModel):
    """最近 24 小时内单个小时桶的检索统计。"""

    hour_iso: str = Field(..., description="桶起点 ISO 时间戳（UTC, ``Z`` 后缀）")
    queries: int = Field(..., description="该小时桶内的检索次数")
    avg_latency_ms: float = Field(..., description="该桶平均延迟（毫秒），无数据为 0")


class SearchLogStats(BaseModel):
    """检索日志聚合统计响应。"""

    total_queries: int = Field(..., description="用户全部检索次数")
    overall_avg_latency_ms: float = Field(..., description="全部检索平均延迟（毫秒），无数据为 0")
    overall_p95_latency_ms: float = Field(..., description="P95 延迟（毫秒），无数据为 0")
    by_dataset: list[SearchLogByDataset] = Field(
        default_factory=list, description="按数据集分组的检索统计"
    )
    hourly_24h: list[SearchLogHourBucket] = Field(
        default_factory=list,
        description="最近 24h 滚动 1 小时窗口（共 24 桶，数组最后一个为当前桶）",
    )
