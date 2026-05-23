"""基于 hnswlib 的 ANN 后端实现。"""

from __future__ import annotations

from typing import Any

import numpy as np

from app.services.ann.base import IndexBackend

_METRIC_MAP: dict[str, str] = {
    "l2": "l2",
    "cosine": "cosine",
    "ip": "ip",
}


class HnswlibBackend(IndexBackend):
    """hnswlib HNSW 索引后端。

    封装 ``hnswlib.Index``，实现统一的 :class:`IndexBackend` 接口。
    使用 ``l2 | cosine | ip`` 三种度量；构建参数 ``M / ef_construction / ef_search``
    通过 :meth:`build` 透传。

    Attributes:
        dim: 向量维度。
        metric: 距离度量，取值 ``l2|cosine|ip``。
    """

    def __init__(self, dim: int, metric: str = "l2") -> None:
        """初始化后端。

        Args:
            dim: 向量维度。
            metric: 距离度量，参见 :data:`_METRIC_MAP`。

        Raises:
            ValueError: 当 ``metric`` 不被支持时抛出。
        """
        if metric not in _METRIC_MAP:
            raise ValueError(f"hnswlib 不支持的 metric: {metric}")
        self.dim = int(dim)
        self.metric = metric
        self._index: Any | None = None
        self._num_elements: int = 0
        self._m: int = 16
        self._ef_search: int = 50

    @property
    def name(self) -> str:
        return "hnswlib"

    def build(
        self,
        vectors: np.ndarray,
        M: int = 16,
        ef_construction: int = 200,
        ef_search: int = 50,
        max_elements: int | None = None,
        **_: Any,
    ) -> None:
        """构建 hnswlib 索引。

        Args:
            vectors: 训练向量，``(N, dim)`` 的 ``float32`` 矩阵。
            M: HNSW 每层最大连接数。
            ef_construction: 构建期候选集大小。
            ef_search: 查询期候选集大小，越大召回越高但越慢。
            max_elements: 最大可容纳元素数，默认取 ``len(vectors)``。

        Raises:
            ValueError: ``vectors`` 形状非法。
        """
        import hnswlib

        if vectors.ndim != 2 or vectors.shape[1] != self.dim:
            raise ValueError(f"vectors 形状应为 (N, {self.dim})，实际 {vectors.shape}")
        vectors = np.ascontiguousarray(vectors, dtype=np.float32)
        index = hnswlib.Index(space=_METRIC_MAP[self.metric], dim=self.dim)
        index.init_index(
            max_elements=int(max_elements or vectors.shape[0]),
            ef_construction=int(ef_construction),
            M=int(M),
        )
        index.add_items(vectors, np.arange(vectors.shape[0]))
        index.set_ef(int(ef_search))
        self._index = index
        self._num_elements = int(vectors.shape[0])
        self._m = int(M)
        self._ef_search = int(ef_search)

    def set_ef(self, ef_search: int) -> None:
        """更新查询期 ``ef_search`` 参数。

        Args:
            ef_search: 新的查询期候选集大小。
        """
        self._ef_search = int(ef_search)
        if self._index is not None:
            self._index.set_ef(self._ef_search)

    def search(self, query: np.ndarray, top_k: int) -> tuple[np.ndarray, np.ndarray]:
        if self._index is None:
            raise RuntimeError("索引尚未构建或加载")
        q = np.ascontiguousarray(np.atleast_2d(query), dtype=np.float32)
        labels, distances = self._index.knn_query(q, k=int(top_k))
        return np.asarray(labels), np.asarray(distances)

    def save(self, path: str) -> None:
        if self._index is None:
            raise RuntimeError("索引尚未构建")
        self._index.save_index(path)

    def load(self, path: str, max_elements: int | None = None) -> None:
        """从磁盘加载 hnswlib 索引。

        优先尝试启用 ``allow_replace_deleted=True`` 的新签名以便后续在线删除，
        若运行时为旧版本 hnswlib 抛 :class:`TypeError`，则静默回退到无参签名。
        新版 hnswlib (>=0.8) 内部 ``load_index`` 已默认使用 mmap 友好的加载路径，
        相比早期版本可显著降低冷启动常驻内存。

        Args:
            path: 索引文件路径。
            max_elements: 可选，载入后的最大容量，``None`` 表示沿用文件原值。
        """
        import hnswlib

        index = hnswlib.Index(space=_METRIC_MAP[self.metric], dim=self.dim)
        load_max = int(max_elements) if max_elements is not None else 0
        try:
            index.load_index(path, max_elements=load_max, allow_replace_deleted=True)
        except TypeError:
            if max_elements is None:
                index.load_index(path)
            else:
                index.load_index(path, max_elements=int(max_elements))
        if self._ef_search:
            index.set_ef(int(self._ef_search))
        self._index = index
        self._num_elements = index.get_current_count()

    def memory_mb(self) -> float:
        """按 ``N * dim * 4B + N * M * 8B`` 估算内存占用。"""
        if self._index is None or self._num_elements <= 0:
            return 0.0
        vec_bytes = self._num_elements * self.dim * 4
        graph_bytes = self._num_elements * self._m * 8
        return float((vec_bytes + graph_bytes) / (1024 * 1024))
