"""暴力检索后端，用于 Recall 评测的 ground truth 基线。"""

from __future__ import annotations

from typing import Any

import numpy as np

from app.services.ann.base import IndexBackend


class BruteBackend(IndexBackend):
    """基于 numpy 矩阵运算的精确最近邻后端。

    支持 ``l2``、``ip``、``cosine`` 三种度量。``l2`` 通过
    ``||a-b||^2 = ||a||^2 + ||b||^2 - 2 a·b`` 的向量化展开计算，
    ``cosine`` 在 :meth:`build` / :meth:`search` 内部完成 L2 归一化，
    ``ip`` 直接使用内积。

    返回的 ``distances`` 语义：
        - ``l2``: 平方欧氏距离（越小越近）。
        - ``cosine``: ``1 - cosine_similarity``（越小越近）。
        - ``ip``: ``-inner_product``（越小越近，统一为 distance 语义）。

    Attributes:
        dim: 向量维度。
        metric: 距离度量。
    """

    def __init__(self, dim: int, metric: str = "l2") -> None:
        if metric not in {"l2", "ip", "cosine"}:
            raise ValueError(f"BruteBackend 不支持的 metric: {metric}")
        self.dim = int(dim)
        self.metric = metric
        self._vectors: np.ndarray | None = None

    @property
    def name(self) -> str:
        return "brute"

    def build(self, vectors: np.ndarray, **_: Any) -> None:
        if vectors.ndim != 2 or vectors.shape[1] != self.dim:
            raise ValueError(f"vectors 形状应为 (N, {self.dim})，实际 {vectors.shape}")
        vecs = np.ascontiguousarray(vectors, dtype=np.float32)
        if self.metric == "cosine":
            norms = np.linalg.norm(vecs, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            vecs = vecs / norms
        self._vectors = vecs

    def _pairwise_distances(self, query: np.ndarray) -> np.ndarray:
        """根据度量计算 ``query`` 与底库的成对 distance（越小越近）。"""
        assert self._vectors is not None
        if self.metric == "l2":
            sq_query = np.sum(query**2, axis=1, keepdims=True)
            sq_base = np.sum(self._vectors**2, axis=1, keepdims=True).T
            distances = sq_query + sq_base - 2.0 * query @ self._vectors.T
            return np.maximum(distances, 0.0)
        if self.metric == "cosine":
            return 1.0 - query @ self._vectors.T
        return -(query @ self._vectors.T)

    def search(self, query: np.ndarray, top_k: int) -> tuple[np.ndarray, np.ndarray]:
        if self._vectors is None:
            raise RuntimeError("索引尚未构建")
        q = np.ascontiguousarray(np.atleast_2d(query), dtype=np.float32)
        if self.metric == "cosine":
            qn = np.linalg.norm(q, axis=1, keepdims=True)
            qn[qn == 0] = 1.0
            q = q / qn
        distances = self._pairwise_distances(q)
        n_base = distances.shape[1]
        k = min(int(top_k), n_base)
        kth = max(min(k - 1, n_base - 1), 0)
        idx_part = np.argpartition(distances, kth=kth, axis=1)[:, :k]
        row_idx = np.arange(distances.shape[0])[:, None]
        top_dist = distances[row_idx, idx_part]
        order = np.argsort(top_dist, axis=1)
        return idx_part[row_idx, order], top_dist[row_idx, order]

    def save(self, path: str) -> None:
        if self._vectors is None:
            raise RuntimeError("索引尚未构建")
        with open(path, "wb") as fh:
            np.save(fh, self._vectors, allow_pickle=False)

    def load(self, path: str) -> None:
        """加载底库向量，启用 ``mmap_mode='r'`` 降低冷启动内存占用。

        ``np.load`` 在 mmap 模式下返回 :class:`numpy.memmap`，按页惰性读取磁盘；
        若落盘 dtype 与 ``float32`` 不一致（例如启用 F5 ``float16`` 压缩），
        会显式 ``astype`` 拷贝一份 float32 到内存（mmap 无法跨 dtype 共享）。
        """
        arr = np.load(path, mmap_mode="r", allow_pickle=False)
        if arr.dtype == np.float32:
            self._vectors = arr
        else:
            self._vectors = np.asarray(arr, dtype=np.float32)

    def memory_mb(self) -> float:
        if self._vectors is None:
            return 0.0
        return float(self._vectors.nbytes / (1024 * 1024))
