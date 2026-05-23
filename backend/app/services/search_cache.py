"""检索结果 Redis 缓存（F2 redis_cache）。

设计：
    - key = SHA256("v1|{index_id}|{top_k}|{cell_id_or_vector_hash}|{filters_json}")
    - value = JSON 序列化的检索响应
    - TTL = ``settings.SEARCH_CACHE_TTL_SECONDS``（默认 300s）
    - Redis 不可用时**优雅降级**为透传，不影响主链路

进程级 hit/miss 计数：
    - 调用方可通过 :func:`get_cache_metrics` 读取
    - 暴露给 :file:`backend/app/api/v1/indexes.py` ``GET /indexes/cache/stats`` 拼合返回
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Awaitable, Callable
from typing import Any

import numpy as np
import redis.asyncio as aioredis

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# 进程级计数器（多 worker 时各自统计；监控聚合由前端拼装）
_metrics_lock = threading.Lock()
_hits = 0
_misses = 0
_errors = 0

_redis_client: aioredis.Redis | None = None
_client_init_lock = threading.Lock()


def _get_client() -> aioredis.Redis | None:
    """获取共享 Redis 客户端；首次失败后续静默禁用。"""
    global _redis_client  # noqa: PLW0603
    if _redis_client is not None:
        return _redis_client
    with _client_init_lock:
        if _redis_client is not None:
            return _redis_client
        try:
            _redis_client = aioredis.from_url(
                settings.REDIS_URL, encoding="utf-8", decode_responses=True
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis 客户端初始化失败，搜索缓存禁用：%s", exc)
            _redis_client = None
    return _redis_client


def _hash_vector(vec: np.ndarray | list[float]) -> str:
    """对向量做稳定哈希（取前 8 维 + 长度，避免序列化整个向量）。"""
    arr = np.asarray(vec, dtype=np.float32).flatten()
    digest = hashlib.sha1(arr.tobytes()).hexdigest()[:16]
    return f"v{arr.size}:{digest}"


def make_cache_key(
    *,
    index_id: int | None,
    top_k: int,
    query: str | np.ndarray | list[float],
    filters: dict[str, Any] | None,
) -> str:
    """生成稳定缓存 key。

    Args:
        index_id: 索引 ID；None 时用 ``auto``。
        top_k: 返回数量。
        query: cell_id 字符串或查询向量。
        filters: 元数据过滤字典，会按 key 排序后序列化以保证顺序无关。
    """
    q_part = f"cid:{query}" if isinstance(query, str) else _hash_vector(query)
    filters_json = json.dumps(filters or {}, sort_keys=True, ensure_ascii=False)
    raw = f"v1|{index_id or 'auto'}|{top_k}|{q_part}|{filters_json}"
    return "search:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def get_cached(key: str) -> dict[str, Any] | None:
    """从 Redis 读取缓存结果，未命中或异常时返回 None。"""
    global _hits, _misses, _errors  # noqa: PLW0603
    client = _get_client()
    if client is None:
        return None
    try:
        raw = await client.get(key)
        if raw is None:
            with _metrics_lock:
                _misses += 1
            return None
        with _metrics_lock:
            _hits += 1
        return json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        with _metrics_lock:
            _errors += 1
        logger.warning("搜索缓存读取异常 key=%s err=%s", key, exc)
        return None


async def set_cached(key: str, value: dict[str, Any], ttl: int | None = None) -> None:
    """写入缓存；TTL 缺省读 ``settings.SEARCH_CACHE_TTL_SECONDS``。"""
    global _errors  # noqa: PLW0603
    client = _get_client()
    if client is None:
        return
    try:
        payload = json.dumps(value, default=str, ensure_ascii=False)
        await client.set(key, payload, ex=int(ttl or settings.SEARCH_CACHE_TTL_SECONDS))
    except Exception as exc:  # noqa: BLE001
        with _metrics_lock:
            _errors += 1
        logger.warning("搜索缓存写入异常 key=%s err=%s", key, exc)


async def cached_or_compute(
    key: str,
    compute: Callable[[], Awaitable[dict[str, Any]]],
    ttl: int | None = None,
) -> dict[str, Any]:
    """先查缓存，未命中调 ``compute()`` 并回写。

    Args:
        key: :func:`make_cache_key` 生成。
        compute: 实际计算的 async callable，返回检索响应字典。
        ttl: 可选 TTL 覆盖。
    """
    cached = await get_cached(key)
    if cached is not None:
        cached["cache_hit"] = True  # 强制覆盖（避免落盘值污染）
        return cached
    result = await compute()
    # 先写盘（不含 cache_hit），再给当次响应打标
    await set_cached(key, result, ttl=ttl)
    result["cache_hit"] = False
    return result


def get_cache_metrics() -> dict[str, int | float]:
    """返回当前进程 hit/miss/error 计数与命中率。"""
    with _metrics_lock:
        h, m, e = _hits, _misses, _errors
    total = h + m
    return {
        "search_cache_hits": h,
        "search_cache_misses": m,
        "search_cache_errors": e,
        "search_cache_hit_ratio": round(h / total, 4) if total > 0 else 0.0,
    }


def reset_cache_metrics() -> None:
    """重置计数器（测试用）。"""
    global _hits, _misses, _errors  # noqa: PLW0603
    with _metrics_lock:
        _hits = 0
        _misses = 0
        _errors = 0
