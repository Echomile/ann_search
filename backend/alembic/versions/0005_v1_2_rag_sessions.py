"""v1.2 扩展功能 D4: 新增 rag_sessions / rag_messages 两张表。

Revision ID: 0005_v1_2_rag_sessions
Revises: 0004_v1_2_aligned_datasets
Create Date: 2026-05-24

字段、类型与 :mod:`app.models.rag` 保持一致。LLM Function Calling Agent 风格
的多轮会话需要持久化所有 user / assistant / tool 消息，并把每一步的 tool_calls
与 tool_results 落地，便于前端透明展示与历史回放。

兼容性:
    - SQLite 通过 ``batch_alter_table`` 走重建路径；本次纯新增表，无需 batch。
    - PostgreSQL 直接 ``CREATE TABLE`` + ``ON DELETE CASCADE``。

依赖关系:
    - down_revision 指向 D7 的 ``0004_v1_2_aligned_datasets``，保持 alembic 链
      单 head；如未来 D7 调整自身 migration 编号，本文件需同步更新 down_revision。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0005_v1_2_rag_sessions"
down_revision = "0004_v1_2_aligned_datasets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建 rag_sessions + rag_messages 表及关联索引。"""
    op.create_table(
        "rag_sessions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "title",
            sa.String(length=255),
            nullable=False,
            server_default=sa.text("'新对话'"),
        ),
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
            ["user_id"],
            ["users.id"],
            name="fk_rag_sessions_user_id_users",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_rag_sessions_user_id",
        "rag_sessions",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "rag_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("tool_calls_json", sa.Text(), nullable=True),
        sa.Column("tool_results_json", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["rag_sessions.id"],
            name="fk_rag_messages_session_id_rag_sessions",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_rag_messages_session_id",
        "rag_messages",
        ["session_id"],
        unique=False,
    )


def downgrade() -> None:
    """按依赖关系反向删除索引与表。"""
    op.drop_index("ix_rag_messages_session_id", table_name="rag_messages")
    op.drop_table("rag_messages")

    op.drop_index("ix_rag_sessions_user_id", table_name="rag_sessions")
    op.drop_table("rag_sessions")
