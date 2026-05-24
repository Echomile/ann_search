"""参数扫描相关 ORM 模型 (v1.2 C3 扩展功能: recall-QPS 帕累托曲线)。

设计目标：
    - :class:`SweepRun` 描述一次扫描任务（按 dataset × backends × params 组合）。
    - :class:`SweepPoint` 描述扫描结果中的单个 ``(backend, params)`` 数据点，
      携带 recall / qps / 延迟分位数 / 内存占用以及 ``on_pareto`` 标记。

与 :class:`app.models.index_record.IndexRecord` 保持相同的关系装载约定：
    - 子表通过 DB 端 ``ON DELETE CASCADE`` + SQLAlchemy ``passive_deletes`` 级联删除；
    - relationship 显式声明 ``lazy='raise'``，强制调用方使用 ``selectinload`` /
      ``joinedload`` 预加载，避免懒加载 N+1。
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    pass


class SweepRun(Base):
    """参数扫描任务表。

    Attributes:
        id: 主键。
        dataset_id: 关联数据集 ID，外键 -> ``datasets.id``，``ON DELETE CASCADE``。
        created_by: 触发扫描的用户 ID，外键 -> ``users.id``，可空（系统任务）。
        status: 状态，取值 ``pending | running | done | failed``。
        top_k: 评测使用的 Top-K。
        query_count: 评测使用的查询样本数。
        started_at: 任务开始时间。
        finished_at: 任务结束时间，未完成时为 ``None``。
        error: 失败时的错误信息，成功时为 ``None``。
        created_at: 记录创建时间。
        updated_at: 记录最近更新时间。
        points: 该次扫描产出的所有数据点；``lazy='raise'`` 强制显式预加载。
    """

    __tablename__ = "sweep_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending"
    )
    top_k: Mapped[int] = mapped_column(Integer, nullable=False, default=10, server_default="10")
    query_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=200, server_default="200"
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    points: Mapped[list["SweepPoint"]] = relationship(
        "SweepPoint",
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
        order_by="SweepPoint.recall.asc()",
    )


class SweepPoint(Base):
    """参数扫描单个数据点。

    Attributes:
        id: 主键。
        sweep_run_id: 所属扫描任务 ID，外键 -> ``sweep_runs.id``，``ON DELETE CASCADE``。
        backend: 评测的 ANN 后端名，取值 ``hnswlib | faiss-hnsw | faiss-ivfpq | brute |
            adaptive-hnsw``。
        params_json: 该数据点对应的查询期参数，``JSON`` 形式，例如 ``{"ef_search": 64}``、
            ``{"nprobe": 16}``；brute 等无参后端为 ``{}``。
        recall: Recall@top_k，范围 ``[0, 1]``。
        qps: 单线程吞吐量 (queries per second)。
        p50_ms: P50 延迟（毫秒）。
        p95_ms: P95 延迟（毫秒）。
        p99_ms: P99 延迟（毫秒），可空。
        mem_mb: 索引内存占用（MB）。
        on_pareto: 是否在 (recall, qps) 双目标的帕累托前沿。
        created_at: 创建时间。
        run: 所属 :class:`SweepRun`，``lazy='raise'`` 强制显式预加载。
    """

    __tablename__ = "sweep_points"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sweep_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sweep_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    backend: Mapped[str] = mapped_column(String(32), nullable=False)
    params_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    recall: Mapped[float] = mapped_column(Float, nullable=False)
    qps: Mapped[float] = mapped_column(Float, nullable=False)
    p50_ms: Mapped[float] = mapped_column(Float, nullable=False)
    p95_ms: Mapped[float] = mapped_column(Float, nullable=False)
    p99_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    mem_mb: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    on_pareto: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    run: Mapped["SweepRun"] = relationship(
        "SweepRun",
        back_populates="points",
        lazy="raise",
    )
