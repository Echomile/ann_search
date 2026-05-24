"""v1.2 加分项 C3: 新增 sweep_runs / sweep_points 两张表。

Revision ID: 0002_v1_2_sweep_tables
Revises: 0001_initial
Create Date: 2026-05-24

字段、类型与 :mod:`app.models.sweep` 中定义保持一致。
时间戳列统一使用 ``TIMESTAMPTZ`` + ``NOW()`` 服务器默认值，
JSON 列使用 :class:`sqlalchemy.JSON`，PostgreSQL 后端会落地为 ``JSON``，
SQLite 后端为 ``TEXT``，兼容现有 ``index_records.params``、``datasets.meta_columns``。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002_v1_2_sweep_tables"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建 sweep_runs 与 sweep_points 表及关联索引。"""
    op.create_table(
        "sweep_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("top_k", sa.Integer(), nullable=False, server_default=sa.text("10")),
        sa.Column("query_count", sa.Integer(), nullable=False, server_default=sa.text("200")),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(length=1024), nullable=True),
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
            ["dataset_id"],
            ["datasets.id"],
            name="fk_sweep_runs_dataset_id_datasets",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_sweep_runs_created_by_users",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_sweep_runs_dataset_id",
        "sweep_runs",
        ["dataset_id"],
        unique=False,
    )
    op.create_index(
        "ix_sweep_runs_created_by",
        "sweep_runs",
        ["created_by"],
        unique=False,
    )

    op.create_table(
        "sweep_points",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("sweep_run_id", sa.Integer(), nullable=False),
        sa.Column("backend", sa.String(length=32), nullable=False),
        sa.Column("params_json", sa.JSON(), nullable=False),
        sa.Column("recall", sa.Float(), nullable=False),
        sa.Column("qps", sa.Float(), nullable=False),
        sa.Column("p50_ms", sa.Float(), nullable=False),
        sa.Column("p95_ms", sa.Float(), nullable=False),
        sa.Column("p99_ms", sa.Float(), nullable=True),
        sa.Column("mem_mb", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "on_pareto",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["sweep_run_id"],
            ["sweep_runs.id"],
            name="fk_sweep_points_sweep_run_id_sweep_runs",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_sweep_points_sweep_run_id",
        "sweep_points",
        ["sweep_run_id"],
        unique=False,
    )


def downgrade() -> None:
    """按依赖关系反向删除索引与表。"""
    op.drop_index("ix_sweep_points_sweep_run_id", table_name="sweep_points")
    op.drop_table("sweep_points")

    op.drop_index("ix_sweep_runs_created_by", table_name="sweep_runs")
    op.drop_index("ix_sweep_runs_dataset_id", table_name="sweep_runs")
    op.drop_table("sweep_runs")
