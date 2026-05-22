"""初始数据库结构：users / datasets / index_records / search_logs。

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-22

字段、类型、约束与 :mod:`app.models` 中定义保持一致，
所有时间戳列使用带时区的 ``TIMESTAMPTZ``，并以 ``NOW()`` 作为服务器侧默认值。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建四张核心业务表及其索引/外键约束。"""
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "role",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'user'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_users_username",
        "users",
        ["username"],
        unique=True,
    )

    op.create_table(
        "datasets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("h5ad_path", sa.String(length=1024), nullable=False),
        sa.Column("vectors_path", sa.String(length=1024), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'uploading'"),
        ),
        sa.Column("cell_count", sa.Integer(), nullable=True),
        sa.Column("vector_dim", sa.Integer(), nullable=True),
        sa.Column("vector_source", sa.String(length=64), nullable=True),
        sa.Column("meta_columns", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name="fk_datasets_owner_id_users",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_datasets_owner_id",
        "datasets",
        ["owner_id"],
        unique=False,
    )

    op.create_table(
        "index_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("backend", sa.String(length=32), nullable=False),
        sa.Column("metric", sa.String(length=16), nullable=False),
        sa.Column("params", sa.JSON(), nullable=True),
        sa.Column("index_path", sa.String(length=1024), nullable=True),
        sa.Column("build_time_seconds", sa.Float(), nullable=True),
        sa.Column("memory_mb", sa.Float(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'building'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            name="fk_index_records_dataset_id_datasets",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_index_records_dataset_id",
        "index_records",
        ["dataset_id"],
        unique=False,
    )

    op.create_table(
        "search_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("top_k", sa.Integer(), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            name="fk_search_logs_dataset_id_datasets",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_search_logs_user_id_users",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_search_logs_dataset_id",
        "search_logs",
        ["dataset_id"],
        unique=False,
    )
    op.create_index(
        "ix_search_logs_user_id",
        "search_logs",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    """按依赖关系反向删除索引与表。"""
    op.drop_index("ix_search_logs_user_id", table_name="search_logs")
    op.drop_index("ix_search_logs_dataset_id", table_name="search_logs")
    op.drop_table("search_logs")

    op.drop_index("ix_index_records_dataset_id", table_name="index_records")
    op.drop_table("index_records")

    op.drop_index("ix_datasets_owner_id", table_name="datasets")
    op.drop_table("datasets")

    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
