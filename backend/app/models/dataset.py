"""数据集表 ORM 模型。"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Dataset(Base):
    """数据集表。

    Attributes:
        id: 主键。
        owner_id: 数据集拥有者用户 ID，外键 -> ``users.id``。
        name: 数据集名称。
        h5ad_path: 原始 ``.h5ad`` 文件路径。
        vectors_path: 预处理后的向量文件路径（可空）。
        status: 数据集状态，取值 ``uploading|preprocessing|ready|failed``。
        cell_count: 细胞（样本）数量。
        vector_dim: 向量维度。
        vector_source: 向量来源，如 ``X_pca``、``X_scvi`` 等。
        meta_columns: 元信息列描述，JSON 形式。
        created_at: 创建时间。
    """

    __tablename__ = "datasets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    h5ad_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    vectors_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="uploading", server_default="uploading"
    )
    cell_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vector_dim: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vector_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    meta_columns: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
