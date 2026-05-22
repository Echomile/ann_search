"""检索日志表 ORM 模型。"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SearchLog(Base):
    """检索请求日志。

    Attributes:
        id: 主键。
        dataset_id: 数据集 ID，外键 -> ``datasets.id``。
        user_id: 发起检索的用户 ID，外键 -> ``users.id``。
        top_k: 返回的近邻数量。
        filters: 元数据过滤条件（如 ``cell_type`` 等），JSON 形式。
        latency_ms: 单次检索耗时（毫秒）。
        created_at: 创建时间。
    """

    __tablename__ = "search_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    top_k: Mapped[int] = mapped_column(Integer, nullable=False)
    filters: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
