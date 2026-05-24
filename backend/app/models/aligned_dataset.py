"""跨数据集语义对齐表 ORM 模型 (v1.2 D7 扩展功能)。

设计目标：
    - 把多个 :class:`Dataset` 的细胞统一到同一向量空间，作为"虚拟数据集"参与
      跨数据集检索；
    - 物理向量与 cell 映射独立落盘 (``data/aligned/{id}/``)，不污染原始 dataset；
    - 状态机与 :class:`Dataset` 类似：``pending | running | done | failed``。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AlignedDataset(Base):
    """跨数据集对齐后的统一数据集。

    Attributes:
        id: 主键。
        name: 对齐数据集名称，便于前端展示。
        source_dataset_ids_json: 参与对齐的原始 dataset ID 列表，JSON 字符串
            形如 ``"[1, 2, 3]"``；用字符串而非 ARRAY 类型保证 SQLite/PostgreSQL
            兼容。
        method: 对齐方法，取值 ``intersect_only | harmony``。
            ``intersect_only`` 仅取基因集交集后重新 PCA；``harmony`` 在
            intersect 基础上跑 harmonypy 做 batch correction，harmonypy 包
            缺失时降级到 ``intersect_only``。
        target_dim: 对齐后向量维度（默认 30）。
        cell_count: 对齐后总细胞数（所有 source dataset 细胞数之和）。
        common_genes_count: 基因集交集大小，便于评估对齐覆盖度。
        vectors_path: 对齐后向量 ``.npy`` 文件路径。落盘在 ``data/aligned/{id}/vectors.npy``。
        cell_map_path: ``cell_map.json`` 路径，存 ``[{"cell_id": str,
            "source_dataset_id": int}, ...]``，行序与 ``vectors`` 一致。
        status: 状态机：``pending | running | done | failed``。
        created_by: 触发对齐的用户 ID，外键 -> ``users.id``，nullable
            （系统/迁移任务时为空）。
        created_at: 创建时间。
        updated_at: 最近更新时间。
    """

    __tablename__ = "aligned_datasets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_dataset_ids_json: Mapped[str] = mapped_column(String(2048), nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    target_dim: Mapped[int] = mapped_column(Integer, nullable=False)
    cell_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    common_genes_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    vectors_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    cell_map_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending"
    )
    created_by: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
