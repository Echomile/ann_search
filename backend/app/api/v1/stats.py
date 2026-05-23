"""检索日志统计路由。

向前端 ``EvaluationPage`` 提供历史检索日志的聚合视图，包括按数据集分组的
总查询数 / 平均延迟 / P95 延迟，以及最近 24 小时按整点聚合的时间序列。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.schemas.stats import SearchStatsResponse
from app.services.stats import compute_search_stats

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
