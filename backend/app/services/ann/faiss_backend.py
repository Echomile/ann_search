"""基于 FAISS 的 ANN 后端实现。

提供两个具体后端：
    - :class:`FaissHnswBackend`：基于 ``IndexHNSWFlat``，适合中小规模高召回检索。
    - :class:`FaissIvfPqBackend`：基于 ``IndexIVFPQ``，适合大规模、内存受限场景。

``cosine`` 度量通过对向量做 L2 归一化 + ``METRIC_INNER_PRODUCT`` 模拟，
归一化在 :meth:`build` 与 :meth:`search` 内部自动完成。
"""

from __future__ import annotations

from typing import Any

import numpy as np

from app.services.ann.base import IndexBackend


def _faiss_metric(metric: str) -> int:
    """将字符串度量转换为 FAISS 常量。

    Args:
        metric: 取值 ``l2|cosine|ip``。``cosine`` 视为内积（需向量预归一化）。

    Returns:
        int: ``faiss.METRIC_L2`` 或 ``faiss.METRIC_INNER_PRODUCT``。

    Raises:
        ValueError: 未知度量。
    """
    import faiss

    if metric == "l2":
        return faiss.METRIC_L2
    if metric in ("ip", "cosine"):
        return faiss.METRIC_INNER_PRODUCT
    raise ValueError(f"FAISS 不支持的 metric: {metric}")


def _maybe_normalize(vectors: np.ndarray, metric: str) -> np.ndarray:
    """在 ``cosine`` 度量下对向量做就地 L2 归一化（返回连续 float32 副本）。"""
    vecs = np.ascontiguousarray(vectors, dtype=np.float32)
    if metric == "cosine":
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        vecs = vecs / norms
    return vecs


class FaissHnswBackend(IndexBackend):
    """基于 ``faiss.IndexHNSWFlat`` 的图索引后端。"""

    def __init__(self, dim: int, metric: str = "l2") -> None:
        if metric not in {"l2", "ip", "cosine"}:
            raise ValueError(f"FaissHnswBackend 不支持的 metric: {metric}")
        self.dim = int(dim)
        self.metric = metric
        self._index: Any | None = None

    @property
    def name(self) -> str:
        return "faiss-hnsw"

    def build(
        self,
        vectors: np.ndarray,
        M: int = 32,
        ef_construction: int = 200,
        ef_search: int = 64,
        **_: Any,
    ) -> None:
        """构建 HNSWFlat 索引。

        Args:
            vectors: 训练向量，``(N, dim)`` 的 ``float32`` 矩阵。
            M: 每个节点的邻居数（图度数）。
            ef_construction: 构建期候选集大小。
            ef_search: 查询期候选集大小。
        """
        import faiss

        if vectors.ndim != 2 or vectors.shape[1] != self.dim:
            raise ValueError(f"vectors 形状应为 (N, {self.dim})，实际 {vectors.shape}")
        vecs = _maybe_normalize(vectors, self.metric)
        index = faiss.IndexHNSWFlat(self.dim, int(M), _faiss_metric(self.metric))
        index.hnsw.efConstruction = int(ef_construction)
        index.hnsw.efSearch = int(ef_search)
        index.add(vecs)
        self._index = index

    def search(self, query: np.ndarray, top_k: int) -> tuple[np.ndarray, np.ndarray]:
        if self._index is None:
            raise RuntimeError("索引尚未构建或加载")
        q = _maybe_normalize(np.atleast_2d(query), self.metric)
        distances, indices = self._index.search(q, int(top_k))
        return np.asarray(indices), np.asarray(distances)

    def save(self, path: str) -> None:
        import faiss

        if self._index is None:
            raise RuntimeError("索引尚未构建")
        faiss.write_index(self._index, path)

    def load(self, path: str) -> None:
        import faiss

        self._index = faiss.read_index(path)

    def memory_mb(self) -> float:
        """按 ``N * dim * 4B`` 估算（不含图开销，仅近似）。"""
        if self._index is None:
            return 0.0
        return float(self._index.ntotal * self.dim * 4 / (1024 * 1024))


class FaissIvfPqBackend(IndexBackend):
    """基于 ``faiss.IndexIVFPQ`` 的量化索引后端。"""

    def __init__(self, dim: int, metric: str = "l2") -> None:
        if metric not in {"l2", "ip", "cosine"}:
            raise ValueError(f"FaissIvfPqBackend 不支持的 metric: {metric}")
        self.dim = int(dim)
        self.metric = metric
        self._index: Any | None = None
        self._m: int = 8
        self._nbits: int = 8
        self._nprobe: int = 16

    @property
    def name(self) -> str:
        return "faiss-ivfpq"

    def build(
        self,
        vectors: np.ndarray,
        nlist: int = 1024,
        m: int = 8,
        nbits: int = 8,
        nprobe: int = 16,
        **_: Any,
    ) -> None:
        """构建 IVF-PQ 索引。

        Args:
            vectors: 训练向量，``(N, dim)`` 的 ``float32`` 矩阵。
            nlist: 倒排表数（粗量化中心数）。需满足 ``N >= nlist``，否则训练失败。
            m: PQ 子量化器数量，需整除 ``dim``。
            nbits: 每个子量化器的位数，常用 ``8`` 表示 256 码字。
            nprobe: 查询时探查的倒排表数量，越大召回越高、越慢。

        Raises:
            ValueError: ``vectors`` 形状非法或 ``dim`` 不能被 ``m`` 整除。
        """
        import faiss

        if vectors.ndim != 2 or vectors.shape[1] != self.dim:
            raise ValueError(f"vectors 形状应为 (N, {self.dim})，实际 {vectors.shape}")
        if self.dim % m != 0:
            raise ValueError(f"dim={self.dim} 必须能被 m={m} 整除")
        vecs = _maybe_normalize(vectors, self.metric)
        metric_type = _faiss_metric(self.metric)
        if metric_type == faiss.METRIC_L2:
            quantizer = faiss.IndexFlatL2(self.dim)
        else:
            quantizer = faiss.IndexFlatIP(self.dim)
        index = faiss.IndexIVFPQ(quantizer, self.dim, int(nlist), int(m), int(nbits))
        index.metric_type = metric_type
        index.train(vecs)
        index.add(vecs)
        index.nprobe = int(nprobe)
        self._index = index
        self._m = int(m)
        self._nbits = int(nbits)
        self._nprobe = int(nprobe)

    def search(self, query: np.ndarray, top_k: int) -> tuple[np.ndarray, np.ndarray]:
        if self._index is None:
            raise RuntimeError("索引尚未构建或加载")
        q = _maybe_normalize(np.atleast_2d(query), self.metric)
        distances, indices = self._index.search(q, int(top_k))
        return np.asarray(indices), np.asarray(distances)

    def save(self, path: str) -> None:
        import faiss

        if self._index is None:
            raise RuntimeError("索引尚未构建")
        faiss.write_index(self._index, path)

    def load(self, path: str) -> None:
        import faiss

        self._index = faiss.read_index(path)
        self._nprobe = int(getattr(self._index, "nprobe", self._nprobe))

    def memory_mb(self) -> float:
        """按 ``N * (m * nbits / 8)`` 估算 PQ 编码体积。"""
        if self._index is None:
            return 0.0
        per_code_bytes = self._m * self._nbits / 8
        return float(self._index.ntotal * per_code_bytes / (1024 * 1024))
