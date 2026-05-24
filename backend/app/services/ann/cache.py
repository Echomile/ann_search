"""进程内 ANN 索引缓存。

API 进程在检索时会反复加载同一个索引文件，体积较大且加载耗时显著。
:class:`IndexCache` 提供一个简单的 LRU 缓存，按 ``index_id`` 常驻已加载的
:class:`IndexBackend` 实例，避免每次请求都从磁盘反序列化。

线程安全说明：FastAPI 默认在单进程内通过 asyncio 串行处理协程，
缓存内部用 :class:`asyncio.Lock` 保护并发加载，避免相同 ``index_id``
的重复落盘读取。
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.dataset import Dataset
from app.models.index_record import IndexRecord
from app.services.ann.base import IndexBackend
from app.services.ann.factory import create_backend

logger = get_logger(__name__)


class IndexCache:
    """简易 LRU 索引缓存。

    Attributes:
        capacity: 缓存最大容量，超出后淘汰最久未访问条目。
    """

    _instance: IndexCache | None = None

    def __init__(self, capacity: int = 4) -> None:
        """初始化缓存。

        Args:
            capacity: 缓存容量，默认 ``4``。
        """
        self.capacity = int(capacity)
        self._cache: OrderedDict[int, IndexBackend] = OrderedDict()
        self._lock = asyncio.Lock()
        # 命中率统计：hits 已加载命中、misses 触发加载、evictions LRU 淘汰、loads 累计加载次数
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._loads = 0

    @classmethod
    def instance(cls, capacity: int = 4) -> IndexCache:
        """获取单例。

        Args:
            capacity: 首次创建时使用的容量；已存在时忽略。

        Returns:
            IndexCache: 进程内唯一实例。
        """
        if cls._instance is None:
            cls._instance = cls(capacity=capacity)
        return cls._instance

    def peek(self, index_id: int) -> IndexBackend | None:
        """仅查内存命中：命中返回已加载后端并刷新 LRU，未命中返回 ``None``。

        本方法**不会**触发数据库查询或磁盘加载，是同步调用，供 :func:`get_index_backend`
        在没有 ``db`` 上下文的场景下做 "命中即用，未命中走 fallback" 的快速路径。

        Args:
            index_id: 索引 ID。

        Returns:
            IndexBackend | None: 命中返回实例并累加 ``hits``；未命中返回 ``None``
            但**不**累加 ``misses``（避免污染指标——只有真正发起加载的路径才算 miss）。
        """
        backend = self._cache.get(index_id)
        if backend is None:
            return None
        self._cache.move_to_end(index_id)
        self._hits += 1
        return backend

    async def get_or_load(self, index_id: int, db: AsyncSession) -> IndexBackend:
        """获取或加载指定索引。

        Args:
            index_id: :class:`IndexRecord` 主键。
            db: 异步数据库会话，用于查询索引元数据。

        Returns:
            IndexBackend: 已加载的后端实例。

        Raises:
            LookupError: 索引记录不存在。
            RuntimeError: 索引状态非 ``ready`` 或缺少 ``index_path``。
        """
        async with self._lock:
            if index_id in self._cache:
                self._cache.move_to_end(index_id)
                self._hits += 1
                return self._cache[index_id]

            self._misses += 1
            record = await db.get(IndexRecord, index_id)
            if record is None:
                raise LookupError(f"索引不存在: id={index_id}")
            if record.status != "ready" or not record.index_path:
                raise RuntimeError(f"索引尚未就绪: id={index_id} status={record.status}")
            dataset = await db.get(Dataset, record.dataset_id)
            if dataset is None or not dataset.vector_dim:
                raise RuntimeError(f"数据集元信息缺失: dataset_id={record.dataset_id}")

            backend = create_backend(record.backend, dataset.vector_dim, record.metric)
            logger.info(
                "加载索引到缓存 index_id=%s backend=%s path=%s",
                index_id,
                record.backend,
                record.index_path,
            )
            backend.load(record.index_path)
            self._loads += 1

            self._cache[index_id] = backend
            if len(self._cache) > self.capacity:
                evicted_id, _ = self._cache.popitem(last=False)
                self._evictions += 1
                logger.info("缓存已满，淘汰 index_id=%s", evicted_id)
            return backend

    def evict(self, index_id: int) -> None:
        """从缓存中移除指定索引。

        Args:
            index_id: 索引 ID。不存在时静默忽略。
        """
        if self._cache.pop(index_id, None) is not None:
            self._evictions += 1
            logger.info("手动驱逐缓存 index_id=%s", index_id)

    def clear(self) -> None:
        """清空整个缓存（含计数器重置）。"""
        self._cache.clear()
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._loads = 0

    def stats(self) -> dict[str, float | int | list[int]]:
        """返回缓存命中率与内部计数。

        Returns:
            dict: 包含 ``capacity / size / hits / misses / loads / evictions /
                hit_ratio / cached_index_ids`` 字段，可直接序列化为 JSON 暴露给监控接口。
        """
        total = self._hits + self._misses
        hit_ratio = (self._hits / total) if total > 0 else 0.0
        return {
            "capacity": self.capacity,
            "size": len(self._cache),
            "hits": self._hits,
            "misses": self._misses,
            "loads": self._loads,
            "evictions": self._evictions,
            "hit_ratio": round(hit_ratio, 4),
            "cached_index_ids": list(self._cache.keys()),
        }


def get_index_cache() -> IndexCache:
    """便捷函数：返回 :class:`IndexCache` 单例。"""
    return IndexCache.instance()
