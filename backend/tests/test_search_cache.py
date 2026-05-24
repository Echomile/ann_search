"""F2 search_cache 单测。

不依赖真实 Redis：通过 monkeypatch 把 ``_get_client`` 替换为 in-memory dict-backed fake。
``FakeRedis`` 共享自 :mod:`tests.conftest`。
"""

from __future__ import annotations

import asyncio
from typing import Any

import numpy as np
import pytest

from app.services import search_cache
from tests.conftest import FakeRedis


@pytest.fixture(autouse=True)
def reset_metrics() -> None:
    """每个用例独立计数。"""
    search_cache.reset_cache_metrics()
    search_cache._redis_client = None  # noqa: SLF001  确保从 fresh fake 启动


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    """注入 in-memory :class:`FakeRedis` 替换 ``_get_client``。"""
    fake = FakeRedis()
    monkeypatch.setattr(search_cache, "_get_client", lambda: fake)
    return fake


def test_make_cache_key_stable_for_str() -> None:
    """cell_id 字符串 + 同 filters 应生成同 key。"""
    a = search_cache.make_cache_key(index_id=1, top_k=10, query="cell_x", filters={"a": 1})
    b = search_cache.make_cache_key(index_id=1, top_k=10, query="cell_x", filters={"a": 1})
    assert a == b
    c = search_cache.make_cache_key(index_id=1, top_k=10, query="cell_y", filters={"a": 1})
    assert a != c


def test_make_cache_key_filter_order_invariant() -> None:
    """filters 字段顺序不影响 key。"""
    a = search_cache.make_cache_key(index_id=1, top_k=10, query="q", filters={"a": 1, "b": 2})
    b = search_cache.make_cache_key(index_id=1, top_k=10, query="q", filters={"b": 2, "a": 1})
    assert a == b


def test_make_cache_key_vector_query() -> None:
    """向量 query 用稳定哈希，相同向量 -> 同 key。"""
    v = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    a = search_cache.make_cache_key(index_id=1, top_k=10, query=v, filters=None)
    b = search_cache.make_cache_key(index_id=1, top_k=10, query=v.tolist(), filters=None)
    assert a == b


def test_cached_or_compute_miss_then_hit(fake_redis: FakeRedis) -> None:
    """首次 miss 触发 compute，二次 hit 直接返回。"""

    calls = {"n": 0}

    async def compute() -> dict[str, Any]:
        calls["n"] += 1
        return {"hits": [{"rank": 1, "cell_id": "c1", "distance": 0.5}]}

    async def main() -> None:
        key = search_cache.make_cache_key(index_id=1, top_k=1, query="c0", filters=None)
        r1 = await search_cache.cached_or_compute(key, compute)
        r2 = await search_cache.cached_or_compute(key, compute)
        assert calls["n"] == 1  # 第二次没再 compute
        assert r1["cache_hit"] is False
        assert r2["cache_hit"] is True
        metrics = search_cache.get_cache_metrics()
        assert metrics["search_cache_hits"] == 1
        assert metrics["search_cache_misses"] == 1
        assert metrics["search_cache_hit_ratio"] == 0.5

    asyncio.run(main())


def test_redis_unavailable_falls_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redis 不可用时降级为透传，metrics 仅记 miss。"""
    monkeypatch.setattr(search_cache, "_get_client", lambda: None)

    async def compute() -> dict[str, Any]:
        return {"hits": []}

    async def main() -> None:
        key = search_cache.make_cache_key(index_id=2, top_k=5, query="x", filters=None)
        r = await search_cache.cached_or_compute(key, compute)
        # 无 client 时 get_cached 直接 return None，不增加 miss 计数
        assert r["cache_hit"] is False

    asyncio.run(main())
