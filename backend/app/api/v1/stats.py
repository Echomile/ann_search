"""检索日志统计路由。

向前端 ``EvaluationPage`` 提供历史检索日志的聚合视图，包括按数据集分组的
总查询数 / 平均延迟 / P95 延迟，以及最近 24 小时按整点聚合的时间序列。

此外提供 ``GET /stats/search-logs/export``：以 CSV / JSON 形式流式导出原始
检索日志，供运维分析与学术再处理。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Query
from starlette.responses import StreamingResponse

from app.api.deps import CurrentUser, DbSession
from app.schemas.stats import SearchStatsResponse
from app.services.stats import EXPORT_MAX_LIMIT, compute_search_stats, export_search_logs

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get(
    "/search",
    response_model=SearchStatsResponse,
    summary="检索日志统计",
    description=(
        "汇总当前用户的 :class:`SearchLog`：按 dataset 分组统计总查询数 / 平均延迟 / "
        "P95 延迟；同时返回最近 24h 每小时的查询数与平均延迟（缺数据补 0）。"
        " 仅统计 ``user_id == current_user.id`` 的记录。"
    ),
)
async def search_stats(
    current_user: CurrentUser,
    db: DbSession,
    dataset_id: int | None = None,
) -> SearchStatsResponse:
    """返回当前用户的检索日志统计指标。

    Args:
        dataset_id: 可选 query 参数，按数据集过滤；``None`` 时统计全部数据集。
    """
    payload = await compute_search_stats(db, user_id=current_user.id, dataset_id=dataset_id)
    return SearchStatsResponse(**payload)


@router.get(
    "/search-logs/export",
    summary="导出检索日志（CSV / JSON）",
    description=(
        "导出检索日志，支持 CSV / JSON 两种格式。\n\n"
        "- 非管理员仅能导出自己的 ``user_id``；管理员可导出全部用户日志\n"
        "- 默认上限 10000 条，超出会在 JSON 响应中标记 ``truncated=true``\n"
        "- 时间范围 ``since`` / ``until`` 为 ISO 8601；省略则不过滤\n"
        "- CSV 字段顺序固定：``id,user_id,dataset_id,index_id,query_type,top_k,"
        "latency_ms,cache_hit,backend,created_at``\n"
        "- 用于运维分析 / 学术研究 / 离线再处理"
    ),
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {
                "text/csv": {},
                "application/json": {},
            },
            "description": "流式响应：CSV 文本或 JSON 对象。",
        }
    },
)
async def export_search_logs_endpoint(
    current_user: CurrentUser,
    db: DbSession,
    fmt: Literal["csv", "json"] = Query("csv", alias="format", description="输出格式"),
    since: datetime | None = Query(None, description="起始时间（ISO 8601）"),
    until: datetime | None = Query(None, description="截止时间（ISO 8601）"),
    dataset_id: int | None = Query(None, description="按数据集过滤"),
    limit: int = Query(
        EXPORT_MAX_LIMIT,
        ge=1,
        le=EXPORT_MAX_LIMIT,
        description=f"返回上限，最大 {EXPORT_MAX_LIMIT}",
    ),
) -> StreamingResponse:
    """流式导出当前可见范围内的检索日志。"""
    is_admin = current_user.role == "admin"
    iterator = export_search_logs(
        db,
        fmt=fmt,
        since=since,
        until=until,
        dataset_id=dataset_id,
        limit=limit,
        user_id=current_user.id,
        is_admin=is_admin,
    )
    if fmt == "csv":
        media_type = "text/csv; charset=utf-8"
        filename = "search_logs.csv"
    else:
        media_type = "application/json"
        filename = "search_logs.json"
    return StreamingResponse(
        iterator,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
