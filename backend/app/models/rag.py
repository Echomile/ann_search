"""RAG 多轮对话相关 ORM 模型（v1.2 D4 加分项）。

设计目标:
    - :class:`RagSession` 描述一次完整的多轮对话，按用户分桶。
    - :class:`RagMessage` 记录会话中按时间顺序的所有消息（user / assistant /
      tool），含 LLM 决策的 ``tool_calls_json`` 与执行后的 ``tool_results_json``。

字段约定:
    - 时间戳列统一使用 ``TIMESTAMPTZ`` + ``NOW()`` 服务器默认值；
    - JSON / 文本字段使用 :class:`sqlalchemy.Text`，PostgreSQL 后端为 ``TEXT``，
      SQLite 后端为 ``TEXT``，避免后端方言差异；
    - 子表通过 DB 端 ``ON DELETE CASCADE`` + SQLAlchemy ``passive_deletes`` 级联删除；
    - relationship 显式声明 ``lazy='raise'``，强制调用方使用 ``selectinload`` /
      ``joinedload`` 预加载，避免懒加载 N+1。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class RagSession(Base):
    """RAG 多轮对话会话表。

    Attributes:
        id: 主键。
        user_id: 会话所属用户 ID，外键 -> ``users.id``，``ON DELETE CASCADE``。
        title: 会话标题；默认取首条 user query 前 50 字符，供前端列表展示。
        created_at: 创建时间。
        updated_at: 最近一次追加消息的时间，每次 :meth:`RagService.chat_with_tools`
            完成后由业务侧触发更新。
        messages: 该会话下按时间顺序的所有消息；``lazy='raise'`` 强制显式预加载。
    """

    __tablename__ = "rag_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(
        String(255), nullable=False, default="新对话", server_default="新对话"
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

    messages: Mapped[list[RagMessage]] = relationship(
        "RagMessage",
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
        order_by="RagMessage.id.asc()",
    )


class RagMessage(Base):
    """RAG 单条消息表。

    Attributes:
        id: 主键。
        session_id: 所属会话 ID，外键 -> ``rag_sessions.id``，``ON DELETE CASCADE``。
        role: 消息角色，取值 ``user | assistant | tool | system``。
        content: 文本内容；assistant 在 tool_calls 阶段可为 ``None``。
        tool_calls_json: 当 ``role='assistant'`` 且 LLM 决定调用工具时，存放
            序列化为 JSON 字符串的 ``list[ToolCall]``；否则为 ``None``。
        tool_results_json: 当 ``role='tool'`` 时，存放本轮所有 tool 的执行结果
            （含 ``tool_call_id`` 与 ``result``）；否则为 ``None``。
        created_at: 创建时间。
        session: 反向引用所属 :class:`RagSession`，``lazy='raise'``。
    """

    __tablename__ = "rag_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("rag_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_calls_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_results_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    session: Mapped[RagSession] = relationship(
        "RagSession",
        back_populates="messages",
        lazy="raise",
    )
