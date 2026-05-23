"""索引记录表 ORM 模型。"""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.dataset import Dataset


class IndexRecord(Base):
    """ANN 索引记录表。

    Attributes:
        id: 主键。
        dataset_id: 对应数据集 ID，外键 -> ``datasets.id``。
        backend: 索引后端，取值 ``hnswlib|faiss-hnsw|faiss-ivfpq|brute``。
        metric: 距离度量，例如 ``l2|cosine|ip``。
        params: 构建参数（如 ``M``、``ef_construction`` 等），JSON 形式。
        index_path: 索引文件落盘路径。
        build_time_seconds: 索引构建耗时（秒）。
        memory_mb: 索引内存占用估计（MB）。
        status: 索引状态，取值 ``building|ready|failed``。
        created_at: 创建时间。
        dataset: 关联的数据集对象。声明 ``lazy='raise'`` 避免意外懒加载，
            所有访问都必须通过 ``joinedload(IndexRecord.dataset)`` 等显式预加载，
            从而避免 N+1 查询场景。
    """

    __tablename__ = "index_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    backend: Mapped[str] = mapped_column(String(32), nullable=False)
    metric: Mapped[str] = mapped_column(String(16), nullable=False, default="l2")
    params: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    index_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    build_time_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    memory_mb: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="building", server_default="building"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    dataset: Mapped["Dataset"] = relationship(
        "Dataset",
        back_populates="indexes",
        lazy="raise",
    )
