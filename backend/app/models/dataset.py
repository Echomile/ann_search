"""数据集表 ORM 模型。"""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.index_record import IndexRecord


class Dataset(Base):
    """数据集表。

    Attributes:
        id: 主键。
        owner_id: 数据集拥有者用户 ID，外键 -> ``users.id``。
        name: 数据集名称。
        h5ad_path: 原始 ``.h5ad`` 文件路径。
        vectors_path: 预处理后的向量文件路径（可空）。``dense`` 数据集落盘为
            ``vectors.npy``；``sparse`` 数据集落盘为 ``vectors.npz``（CSR 矩阵）。
        status: 数据集状态，取值 ``uploading|preprocessing|ready|failed``。
        cell_count: 细胞（样本）数量。
        vector_dim: 向量维度。``dense`` 通常 30~50（PCA）；``sparse`` 通常
            1000~5000（HVG 基因数）。
        vector_source: 向量来源标签，描述具体的提取方式，例如 ``X_pca``、
            ``X_scvi``、``HVG_raw_sparse``。
        vector_format: 向量存储格式，``dense`` 走 PCA/稠密 npy；``sparse``
            走 HVG/稀疏 npz，配合 :class:`SparseBruteBackend` 使用。
        meta_columns: 元信息列描述，JSON 形式。
        created_at: 创建时间。
        indexes: 与本数据集关联的索引记录列表。声明 ``lazy='raise'`` 避免
            意外的懒加载查询，所有访问都必须通过 ``selectinload`` / ``joinedload``
            预加载；``passive_deletes=True`` 配合 DB 端 ``ON DELETE CASCADE``
            自动级联删除，无需 ORM 主动 load children。
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
    vector_format: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="dense",
        server_default="dense",
        doc='向量格式: "dense" 走 PCA 降维, "sparse" 直接用 HVG 原始稀疏矩阵',
    )
    meta_columns: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    indexes: Mapped[list["IndexRecord"]] = relationship(
        "IndexRecord",
        back_populates="dataset",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
    )
