"""检索日志统计服务。

读取 :class:`app.models.search_log.SearchLog` 并按数据集分组与按小时分桶聚合，
为 ``GET /api/v1/stats/search`` 提供数据。

聚合策略：
    - 总览：``total_queries``、平均延迟、P95 延迟（``np.percentile``）；
    - 按数据集：外连接 :class:`app.models.dataset.Dataset` 拿名称，``latency_ms`` 为
      ``NULL`` 的记录仅计入 ``total_queries``，不参与延迟统计；
    - 最近 24h：在 Python 端按 ``created_at`` 截断到整点，并补齐缺失的 24 个桶。
      未直接使用 ``func.date_trunc`` 以兼容 SQLite 测试库。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dataset import Dataset
from app.models.search_log import SearchLog


def _percentile(values: list[float], pct: float) -> float:
    """计算延迟列表的指定百分位数；空列表返回 ``0.0``。"""
    if not values:
        return 0.0
    return float(np.percentile(np.asarray(values, dtype=np.float64), pct))


def _mean(values: list[float]) -> float:
    """计算平均值；空列表返回 ``0.0``。"""
    if not values:
        return 0.0
    return float(np.mean(np.asarray(values, dtype=np.float64)))


def _format_hour_iso(dt: datetime) -> str:
    """将 UTC 时间格式化为 ``YYYY-MM-DDTHH:00:00Z`` 形式的 ISO 8601 字符串。"""
    iso = dt.astimezone(UTC).replace(minute=0, second=0, microsecond=0).isoformat()
    return iso.replace("+00:00", "Z")


def _ensure_utc(dt: datetime) -> datetime:
    """把可能 naive 的 datetime 视为 UTC，统一返回 timezone-aware 对象。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


async def compute_search_stats(
    db: AsyncSession,
    user_id: int,
    dataset_id: int | None = None,
) -> dict[str, Any]:
    """汇总指定用户的检索日志统计指标。

    Args:
        dataset_id: 可选，按数据集过滤。``None`` 表示统计全部数据集。
        db: 异步数据库会话。
        user_id: 目标用户 ID；仅统计 ``SearchLog.user_id == user_id`` 的记录。

    Returns:
        dict[str, Any]: 与 :class:`app.schemas.stats.SearchStatsResponse` 字段一致的
        原始字典，可直接用于构造响应模型。``hourly_24h`` 长度恒为 24，
        ``by_dataset`` 按 ``dataset_id`` 升序排列。
    """
    stmt = (
        select(
            SearchLog.dataset_id,
            SearchLog.latency_ms,
            SearchLog.created_at,
            Dataset.name.label("dataset_name"),
        )
        .outerjoin(Dataset, Dataset.id == SearchLog.dataset_id)
        .where(SearchLog.user_id == user_id)
    )
    if dataset_id is not None:
        stmt = stmt.where(SearchLog.dataset_id == dataset_id)
    rows = (await db.execute(stmt)).all()

    all_latencies: list[float] = []
    by_ds_rows: dict[int, list[tuple[float | None, datetime, str | None]]] = defaultdict(list)
    for row in rows:
        ds_id = int(row.dataset_id)
        latency = float(row.latency_ms) if row.latency_ms is not None else None
        created_at = _ensure_utc(row.created_at) if row.created_at is not None else None
        ds_name = row.dataset_name
        if latency is not None:
            all_latencies.append(latency)
        if created_at is not None:
            by_ds_rows[ds_id].append((latency, created_at, ds_name))

    by_dataset: list[dict[str, Any]] = []
    for ds_id in sorted(by_ds_rows.keys()):
        entries = by_ds_rows[ds_id]
        ds_latencies = [lat for lat, _, _ in entries if lat is not None]
        ds_name = next((name for _, _, name in entries if name is not None), None)
        by_dataset.append(
            {
                "dataset_id": ds_id,
                "dataset_name": ds_name,
                "total_queries": len(entries),
                "avg_latency_ms": _mean(ds_latencies),
                "p95_latency_ms": _percentile(ds_latencies, 95),
            }
        )

    # 滚动 24 小时窗口：以当前时刻为锚点，向前 24 个 1 小时桶，最后一个桶 [now-1h, now]
    # 这样跨整点不会丢失数据，更符合"最近 24 小时"语义。
    now = datetime.now(tz=UTC).replace(microsecond=0)
    start_time = now - timedelta(hours=24)
    hourly_counts: list[int] = [0] * 24
    hourly_latencies: list[list[float]] = [[] for _ in range(24)]
    for entries in by_ds_rows.values():
        for latency, created_at, _ in entries:
            if created_at < start_time or created_at > now:
                continue
            offset_s = (created_at - start_time).total_seconds()
            idx = min(23, int(offset_s // 3600))
            hourly_counts[idx] += 1
            if latency is not None:
                hourly_latencies[idx].append(latency)

    hourly_24h: list[dict[str, Any]] = []
    for idx in range(24):
        bucket_start = start_time + timedelta(hours=idx)
        hourly_24h.append(
            {
                "hour_iso": _format_hour_iso(bucket_start),
                "queries": hourly_counts[idx],
                "avg_latency_ms": _mean(hourly_latencies[idx]),
            }
        )

    return {
        "total_queries": len(rows),
        "overall_avg_latency_ms": _mean(all_latencies),
        "overall_p95_latency_ms": _percentile(all_latencies, 95),
        "by_dataset": by_dataset,
        "hourly_24h": hourly_24h,
    }
