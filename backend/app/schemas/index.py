"""索引相关 schema。"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

BackendName = Literal["hnswlib", "faiss-hnsw", "faiss-ivfpq", "brute"]
MetricName = Literal["l2", "cosine", "ip"]
IndexStatusName = Literal["building", "ready", "failed"]


class IndexCreate(BaseModel):
    """创建索引请求体。"""

    backend: BackendName = Field(..., description="ANN 后端：hnswlib|faiss-hnsw|faiss-ivfpq|brute")
    metric: MetricName = Field("l2", description="距离度量：l2|cosine|ip")
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="后端构建参数，例如 hnswlib 的 M / ef_construction / ef_search",
    )


class IndexRecordOut(BaseModel):
    """索引详情响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="索引 ID")
    dataset_id: int = Field(..., description="数据集 ID")
    backend: str = Field(..., description="后端类型")
    metric: str = Field(..., description="距离度量")
    params: dict[str, Any] | None = Field(None, description="构建参数")
    index_path: str | None = Field(None, description="索引文件路径")
    build_time_seconds: float | None = Field(None, description="构建耗时（秒）")
    memory_mb: float | None = Field(None, description="内存占用（MB）")
    status: IndexStatusName = Field(..., description="状态：building|ready|failed")
    created_at: datetime = Field(..., description="创建时间")


class IndexRead(IndexRecordOut):
    """``IndexRead`` 是 :class:`IndexRecordOut` 的别名，保留以兼容旧路由。"""


class IndexCreateResponse(BaseModel):
    """创建索引返回值，包含数据库记录与异步 job 标识。"""

    index: IndexRecordOut = Field(..., description="新建的索引记录")
    task_id: str = Field(..., description="ARQ 任务 job_id，可用于查询进度")


class IndexStatus(BaseModel):
    """索引构建状态。"""

    id: int = Field(..., description="索引 ID")
    status: IndexStatusName = Field(..., description="状态：building|ready|failed")
    backend: str = Field(..., description="后端类型")
    build_time_seconds: float | None = Field(None, description="构建耗时（秒）")
    memory_mb: float | None = Field(None, description="内存占用（MB）")
