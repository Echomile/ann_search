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
                return self._cache[index_id]

            record = await db.get(IndexRecord, index_id)
            if record is None:
                raise LookupError(f"索引不存在: id={index_id}")
            if record.status != "ready" or not record.index_path:
                raise RuntimeError(
                    f"索引尚未就绪: id={index_id} status={record.status}"
                )
            dataset = await db.get(Dataset, record.dataset_id)
            if dataset is None or not dataset.vector_dim:
                raise RuntimeError(
                    f"数据集元信息缺失: dataset_id={record.dataset_id}"
                )

            backend = create_backend(record.backend, dataset.vector_dim, record.metric)
            logger.info(
                "加载索引到缓存 index_id=%s backend=%s path=%s",
                index_id,
                record.backend,
                record.index_path,
            )
            backend.load(record.index_path)

            self._cache[index_id] = backend
            if len(self._cache) > self.capacity:
                evicted_id, _ = self._cache.popitem(last=False)
                logger.info("缓存已满，淘汰 index_id=%s", evicted_id)
            return backend

    def evict(self, index_id: int) -> None:
        """从缓存中移除指定索引。

        Args:
            index_id: 索引 ID。不存在时静默忽略。
        """
        if self._cache.pop(index_id, None) is not None:
            logger.info("手动驱逐缓存 index_id=%s", index_id)

    def clear(self) -> None:
        """清空整个缓存。"""
        self._cache.clear()


def get_index_cache() -> IndexCache:
    """便捷函数：返回 :class:`IndexCache` 单例。"""
    return IndexCache.instance()
