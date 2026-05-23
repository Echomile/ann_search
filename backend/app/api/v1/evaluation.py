"""索引评测路由。"""

from __future__ import annotations

import json
import os
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status

from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.core.logging import get_logger
from app.models.index_record import IndexRecord
from app.schemas.evaluation import (
    BenchmarkRequest,
    BenchmarkResult,
    BenchmarkTaskHandle,
)

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


