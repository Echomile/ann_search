"""检索日志统计服务。

读取 :class:`app.models.search_log.SearchLog` 并按数据集分组与按小时分桶聚合，
为 ``GET /api/v1/stats/search`` 提供数据。

聚合策略：
    - 总览：``total_queries``、平均延迟、P95 延迟（``np.percentile``）；
    - 按数据集：外连接 :class:`app.models.dataset.Dataset` 拿名称，``latency_ms`` 为
      ``NULL`` 的记录仅计入 ``total_queries``，不参与延迟统计；
    - 最近 24h：在 Python 端按 ``created_at`` 截断到整点，并补齐缺失的 24 个桶。
      未直接使用 ``func.date_trunc`` 以兼容 SQLite 测试库。

此外提供 :func:`export_search_logs`：按过滤条件流式输出 CSV / JSON，
支撑 ``GET /api/v1/stats/search-logs/export`` 的运维 / 学术分析下载需求。
"""

from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dataset import Dataset
from app.models.search_log import SearchLog

EXPORT_MAX_LIMIT = 10000
"""``export_search_logs`` 的硬上限，防止全表导出打挂数据库。"""

EXPORT_CSV_FIELDS: tuple[str, ...] = (
    "id",
    "user_id",
    "dataset_id",
    "index_id",
    "query_type",
    "top_k",
    "latency_ms",
    "cache_hit",
    "backend",
    "created_at",
)
"""CSV 导出字段顺序；与 F13 协议一致，前端 / 运维脚本依赖该顺序。

注：``index_id`` / ``query_type`` / ``cache_hit`` / ``backend`` 在当前
:class:`SearchLog` 模型上不存在，导出时统一占位为空字符串（CSV）或 ``null``
（JSON），以兼容未来字段扩展。
"""


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
    # 注意：不在此处截断 microsecond，否则刚写入的事件（created_at 微秒 > 0）会被
    # ``created_at > now`` 误判为未来时间戳而漏统计；hour_iso 的对齐由 _format_hour_iso 负责。
    now = datetime.now(tz=UTC)
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


def _serialize_log_row(log: SearchLog) -> dict[str, Any]:
    """将 :class:`SearchLog` ORM 实例序列化为 JSON-ready dict。

    Args:
        log: ORM 行对象。

    Returns:
        dict: 字段顺序与 :data:`EXPORT_CSV_FIELDS` 一致；不存在于模型的字段值为 ``None``。
    """
    created_at = log.created_at
    if isinstance(created_at, datetime):
        created_at_iso: str | None = _ensure_utc(created_at).isoformat().replace("+00:00", "Z")
    else:
        created_at_iso = None
    return {
        "id": int(log.id),
        "user_id": int(log.user_id),
        "dataset_id": int(log.dataset_id),
        "index_id": None,
        "query_type": None,
        "top_k": int(log.top_k) if log.top_k is not None else None,
        "latency_ms": float(log.latency_ms) if log.latency_ms is not None else None,
        "cache_hit": None,
        "backend": None,
        "created_at": created_at_iso,
    }


def _row_to_csv_cells(row: dict[str, Any]) -> list[str]:
    """把字典按 :data:`EXPORT_CSV_FIELDS` 顺序转成 CSV 单元格列表（``None`` → 空串）。"""
    cells: list[str] = []
    for key in EXPORT_CSV_FIELDS:
        value = row.get(key)
        cells.append("" if value is None else str(value))
    return cells


async def export_search_logs(
    db: AsyncSession,
    *,
    fmt: Literal["csv", "json"],
    since: datetime | None,
    until: datetime | None,
    dataset_id: int | None,
    limit: int,
    user_id: int,
    is_admin: bool,
) -> AsyncIterator[bytes]:
    """以异步生成器形式流式导出检索日志。

    依据 ``fmt`` 输出 CSV 或 JSON：

    - CSV：UTF-8 编码、首行表头、字段顺序见 :data:`EXPORT_CSV_FIELDS`；
    - JSON：``{"items": [...], "total": N, "truncated": bool}``，``items`` 内
      按主键升序，``total`` 为实际写出条数（不超过 ``limit``）；当返回条数等于
      ``limit`` 时 ``truncated=true``，提示调用方可能存在更多数据。

    权限语义：

    - ``is_admin=False`` 时强制注入 ``SearchLog.user_id == user_id`` 条件；
    - ``is_admin=True`` 时不限制 ``user_id``，可查看全部日志。

    Args:
        db: 异步数据库会话。
        fmt: 输出格式，``"csv"`` 或 ``"json"``。
        since: 起始时间（含），``None`` 表示不限。
        until: 截止时间（含），``None`` 表示不限。
        dataset_id: 可选数据集过滤。
        limit: 上限条数；调用方负责裁剪到 :data:`EXPORT_MAX_LIMIT`。
        user_id: 当前请求用户 ID；非管理员场景下用于行级过滤。
        is_admin: 当前用户是否为管理员。

    Yields:
        bytes: 流式响应字节块；CSV 每次 yield 一行（含换行），JSON 分多块输出结构。
    """
    stmt = select(SearchLog).order_by(SearchLog.id).limit(limit)
    if not is_admin:
        stmt = stmt.where(SearchLog.user_id == user_id)
    if dataset_id is not None:
        stmt = stmt.where(SearchLog.dataset_id == dataset_id)
    if since is not None:
        stmt = stmt.where(SearchLog.created_at >= _ensure_utc(since))
    if until is not None:
        stmt = stmt.where(SearchLog.created_at <= _ensure_utc(until))

    if fmt == "csv":
        async for chunk in _stream_csv(db, stmt):
            yield chunk
    else:
        async for chunk in _stream_json(db, stmt, limit=limit):
            yield chunk


def _encode_csv_row(cells: list[str]) -> bytes:
    """把单行 CSV 单元格用标准 ``csv.writer`` 编码为 UTF-8 字节。"""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(cells)
    return buf.getvalue().encode("utf-8")


async def _stream_csv(db: AsyncSession, stmt: Any) -> AsyncIterator[bytes]:
    """流式输出 CSV：先表头，再逐行 yield。

    Args:
        db: 异步数据库会话。
        stmt: 已带过滤条件的 ``select(SearchLog)`` 语句。
    """
    yield _encode_csv_row(list(EXPORT_CSV_FIELDS))
    result = await db.stream_scalars(stmt)
    async for log in result:
        yield _encode_csv_row(_row_to_csv_cells(_serialize_log_row(log)))


async def _stream_json(db: AsyncSession, stmt: Any, *, limit: int) -> AsyncIterator[bytes]:
    """流式输出 JSON：``{"items":[...], "total": N, "truncated": bool}``。

    采用"先开头 + 流式 items + 结尾汇总"的策略，避免在内存中聚合超过 ``limit``
    条记录。字段顺序不影响 JSON 语义，但 ``items`` 始终位于第一位以方便人眼浏览。

    Args:
        db: 异步数据库会话。
        stmt: 已带过滤条件的 ``select(SearchLog)`` 语句。
        limit: 实际生效的上限（用于推断 ``truncated`` 标志）。
    """
    yield b'{"items":['
    result = await db.stream_scalars(stmt)
    count = 0
    first = True
    async for log in result:
        prefix = b"" if first else b","
        yield prefix + json.dumps(_serialize_log_row(log), ensure_ascii=False).encode("utf-8")
        first = False
        count += 1
    truncated = count >= limit
    tail = f'],"total":{count},"truncated":{"true" if truncated else "false"}}}'
    yield tail.encode("utf-8")
