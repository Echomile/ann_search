"""v1.2 扩展功能 C5: datasets 表新增 vector_format 字段。

Revision ID: 0003_v1_2_dataset_vector_format
Revises: 0002_v1_2_sweep_tables
Create Date: 2026-05-24

为支持稀疏感知 ANN（C5），数据集需声明向量格式：

    - ``dense``  : 既有 PCA 30~50 维稠密向量，落盘 ``vectors.npy``；
    - ``sparse`` : HVG 5000 维稀疏向量（CSR），落盘 ``vectors.npz``；
      配合 :class:`SparseBruteBackend` 直接检索。

已存在的数据行通过 ``server_default='dense'`` 自动回填，保持向后兼容。
SQLite 通过 ``batch_alter_table`` 走重建路径，PostgreSQL 直接 ``ADD COLUMN``。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0003_v1_2_dataset_vector_format"
down_revision = "0002_v1_2_sweep_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """在 datasets 表添加 vector_format 列，默认 ``dense``。"""
    with op.batch_alter_table("datasets") as batch_op:
        batch_op.add_column(
            sa.Column(
                "vector_format",
                sa.String(length=16),
                nullable=False,
                server_default=sa.text("'dense'"),
            )
        )


def downgrade() -> None:
    """回滚：删除 vector_format 列。"""
    with op.batch_alter_table("datasets") as batch_op:
        batch_op.drop_column("vector_format")
