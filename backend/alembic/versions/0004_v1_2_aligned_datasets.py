"""v1.2 扩展功能 D7: 新增 aligned_datasets 表。

Revision ID: 0004_v1_2_aligned_datasets
Revises: 0003_v1_2_dataset_vector_format
Create Date: 2026-05-24

跨数据集语义对齐 (D7) 把多个 Dataset 的细胞统一到同一向量空间中，
对齐后的向量、cell map 等物理产物落盘到 ``data/aligned/{id}/``，
对应的元信息（source_dataset_ids、method、cell_count 等）落地到本表。

设计与 :mod:`app.models.aligned_dataset` 字段一一对应：

    - ``source_dataset_ids_json``: JSON 字符串，存原始 dataset ID 列表；
    - ``method``: ``intersect_only | harmony``（harmonypy 缺失时降级 intersect_only）；
    - ``target_dim``: 对齐后向量维度（默认 30）；
    - ``status``: 与 :class:`Dataset.status` 类似的状态机；
    - ``created_by``: 触发对齐的用户 ID，外键 -> ``users.id``，可空（系统任务）。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0004_v1_2_aligned_datasets"
down_revision = "0003_v1_2_dataset_vector_format"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建 aligned_datasets 表及关联索引。"""
    op.create_table(
        "aligned_datasets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("source_dataset_ids_json", sa.String(length=2048), nullable=False),
        sa.Column("method", sa.String(length=32), nullable=False),
        sa.Column("target_dim", sa.Integer(), nullable=False),
        sa.Column("cell_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "common_genes_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("vectors_path", sa.String(length=1024), nullable=True),
        sa.Column("cell_map_path", sa.String(length=1024), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_aligned_datasets_created_by_users",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_aligned_datasets_created_by",
        "aligned_datasets",
        ["created_by"],
        unique=False,
    )


def downgrade() -> None:
    """回滚：删除索引与表。"""
    op.drop_index("ix_aligned_datasets_created_by", table_name="aligned_datasets")
    op.drop_table("aligned_datasets")
