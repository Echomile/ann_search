"""索引评测路由。"""

from __future__ import annotations

import json
import os
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.core.logging import get_logger
from app.models.index_record import IndexRecord
from app.models.sweep import SweepPoint, SweepRun
from app.schemas.evaluation import (
    BenchmarkRequest,
    BenchmarkResult,
    BenchmarkTaskHandle,
)
from app.schemas.sweep import SweepPointRead, SweepRunCreate, SweepRunRead
from app.services.evaluation import param_sweep

logger = get_logger(__name__)

router = APIRouter(prefix="/evaluation", tags=["evaluation"])


def _benchmarks_dir() -> str:
    """返回评测结果落盘目录，必要时创建。"""
    path = os.path.join(settings.INDEX_DIR, "benchmarks")
    os.makedirs(path, exist_ok=True)
    return path


def benchmark_result_path(index_id: int) -> str:
    """返回指定索引最近一次评测结果文件路径。"""
    return os.path.join(_benchmarks_dir(), f"{int(index_id)}.json")


@router.post(
    "/run",
    response_model=BenchmarkTaskHandle,
    status_code=status.HTTP_202_ACCEPTED,
    summary="发起索引基准评测",
    description=(
        "对指定索引执行 Recall、QPS、延迟分位数等评测，结果异步落盘。"
        " 若 ARQ 队列不可用则降级为前台同步执行，便于本地开发与测试。"
    ),
)
async def run_benchmark(
    payload: BenchmarkRequest,
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
) -> BenchmarkTaskHandle:
    """入队索引评测任务。"""
    _ = current_user
    record = await db.get(IndexRecord, payload.index_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"索引不存在: {payload.index_id}"
        )
    if record.status != "ready":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"索引尚未 ready: {record.status}"
        )

    arq = getattr(request.app.state, "arq", None)
    if arq is not None:
        try:
            job = await arq.enqueue_job(
                "benchmark_index_task",
                index_id=payload.index_id,
                num_queries=payload.num_queries,
                top_k_list=list(payload.top_k_list),
                concurrency_list=list(payload.concurrency_list),
            )
            task_id = job.job_id if job is not None else f"local-{uuid4().hex}"
            return BenchmarkTaskHandle(task_id=task_id, index_id=payload.index_id, status="queued")
        except Exception as exc:  # noqa: BLE001
            logger.warning("ARQ 入队失败，降级前台执行: %s", exc)

    from app.tasks.benchmark_task import benchmark_index_task

    result = await benchmark_index_task(
        {"redis": None},
        index_id=payload.index_id,
        num_queries=payload.num_queries,
        top_k_list=list(payload.top_k_list),
        concurrency_list=list(payload.concurrency_list),
    )
    return BenchmarkTaskHandle(
        task_id=str(result.get("task_id", f"local-{uuid4().hex}")),
        index_id=payload.index_id,
        status="completed",
    )


@router.get(
    "/{index_id}/latest",
    response_model=BenchmarkResult,
    summary="索引最近一次评测结果",
    description="返回指定索引最近一次评测结果，文件不存在时返回 404。",
)
async def get_latest_benchmark(index_id: int, current_user: CurrentUser) -> BenchmarkResult:
    """读取最近一次评测结果。"""
    _ = current_user
    path = benchmark_result_path(index_id)
    if not os.path.isfile(path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"索引 {index_id} 尚无评测结果",
        )
    with open(path, encoding="utf-8") as fp:
        data: dict[str, Any] = json.load(fp)
    return BenchmarkResult(**data)


@router.get(
    "/results",
    summary="评测结果列表",
    description="返回历史评测结果摘要列表，按索引 ID 聚合。",
)
async def list_results(dataset_id: int | None = None) -> list[dict[str, Any]]:
    """评测结果列表。"""
    out: list[dict[str, Any]] = []
    benchmarks_dir = _benchmarks_dir()
    for name in sorted(os.listdir(benchmarks_dir)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(benchmarks_dir, name), encoding="utf-8") as fp:
                data = json.load(fp)
        except Exception as exc:  # noqa: BLE001
            logger.warning("读取评测结果失败 file=%s err=%s", name, exc)
            continue
        if dataset_id is not None and data.get("dataset_id") != dataset_id:
            continue
        out.append(
            {
                "index_id": data.get("index_id"),
                "dataset_id": data.get("dataset_id"),
                "backend": data.get("backend"),
                "recalls": data.get("recalls", {}),
                "finished_at": data.get("finished_at"),
            }
        )
    return out


# ---------------------------------------------------------------------------
# v1.2 扩展功能 C3: 参数扫描 (recall-QPS 帕累托曲线)
# ---------------------------------------------------------------------------


def _sort_points_for_output(points: list[SweepPoint]) -> list[SweepPoint]:
    """按 ``recall`` 升序排序数据点，``recall`` 相同时按 ``qps`` 升序排序。"""
    return sorted(points, key=lambda p: (float(p.recall), float(p.qps)))


def _serialize_run(run: SweepRun, points: list[SweepPoint]) -> SweepRunRead:
    """将 ORM 对象组装成 :class:`SweepRunRead` 响应。

    Args:
        run: 已 detach 或仍在 session 内的 :class:`SweepRun`。
        points: 关联数据点列表，调用方负责按需过滤（如仅 pareto）。

    Returns:
        SweepRunRead: 嵌入 ``points`` 与 ``pareto_count`` 的响应对象。
    """
    sorted_points = _sort_points_for_output(points)
    point_reads = [SweepPointRead.model_validate(p) for p in sorted_points]
    pareto_count = sum(1 for p in point_reads if p.on_pareto)
    return SweepRunRead(
        id=int(run.id),
        dataset_id=int(run.dataset_id),
        created_by=run.created_by,
        status=str(run.status),
        top_k=int(run.top_k),
        query_count=int(run.query_count),
        started_at=run.started_at,
        finished_at=run.finished_at,
        error=run.error,
        created_at=run.created_at,
        points=point_reads,
        pareto_count=pareto_count,
    )


@router.post(
    "/sweep",
    response_model=SweepRunRead,
    status_code=status.HTTP_200_OK,
    summary="参数扫描 (recall-QPS 帕累托曲线)",
    description=(
        "触发一次同步参数扫描任务（小规模 <30s 内完成）。\n\n"
        "对一个数据集 × 多 backend × 多查询期参数（``ef_search`` / ``nprobe``）\n"
        "执行评测，产出 ``(recall, qps, p50_ms, p95_ms, mem_mb)`` 数据点，并标记\n"
        "``(recall, qps)`` 双目标下的帕累托前沿，用于前端绘制 ANN-Benchmarks 风格曲线。\n\n"
        "请求体: :class:`SweepRunCreate`；返回: :class:`SweepRunRead`，``status='done'``。"
    ),
)
async def trigger_sweep(
    payload: SweepRunCreate,
    db: DbSession,
    current_user: CurrentUser,
) -> SweepRunRead:
    """触发参数扫描并同步返回完整结果。"""
    try:
        run_id = await param_sweep(
            session=db,
            dataset_id=payload.dataset_id,
            backends=list(payload.backends),
            top_k=int(payload.top_k),
            query_count=int(payload.query_count),
            ef_search_grid=list(payload.ef_search_grid) if payload.ef_search_grid else None,
            nprobe_grid=list(payload.nprobe_grid) if payload.nprobe_grid else None,
            user_id=int(current_user.id),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("param_sweep 失败")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"参数扫描失败: {exc}",
        ) from exc

    stmt = select(SweepRun).where(SweepRun.id == run_id).options(selectinload(SweepRun.points))
    result = await db.execute(stmt)
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"扫描任务 {run_id} 落库后查询失败",
        )
    return _serialize_run(run, list(run.points))


@router.get(
    "/sweep/{sweep_id}",
    response_model=SweepRunRead,
    summary="获取参数扫描结果",
    description=(
        "拉取指定参数扫描的全部数据点（按 ``recall`` 升序）。\n\n"
        "返回: :class:`SweepRunRead`，包含 ``points`` 与 ``pareto_count`` 等字段。"
    ),
)
async def get_sweep(
    sweep_id: int,
    db: DbSession,
    current_user: CurrentUser,
) -> SweepRunRead:
    """读取扫描任务详情（含全部数据点）。"""
    _ = current_user
    stmt = select(SweepRun).where(SweepRun.id == sweep_id).options(selectinload(SweepRun.points))
    result = await db.execute(stmt)
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"扫描任务不存在: {sweep_id}",
        )
    return _serialize_run(run, list(run.points))


@router.get(
    "/sweep/{sweep_id}/pareto",
    response_model=SweepRunRead,
    summary="获取参数扫描的帕累托前沿",
    description=(
        "仅返回 ``on_pareto=true`` 的数据点（按 ``recall`` 升序）。\n\n"
        "适合前端直接在 recall-QPS 平面上画 pareto 曲线。"
    ),
)
async def get_pareto(
    sweep_id: int,
    db: DbSession,
    current_user: CurrentUser,
) -> SweepRunRead:
    """读取扫描任务的帕累托前沿点子集。"""
    _ = current_user
    stmt = select(SweepRun).where(SweepRun.id == sweep_id).options(selectinload(SweepRun.points))
    result = await db.execute(stmt)
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"扫描任务不存在: {sweep_id}",
        )
    pareto_points = [p for p in run.points if bool(p.on_pareto)]
    return _serialize_run(run, pareto_points)
