"""暴力检索后端，用于 Recall 评测的 ground truth 基线。

P2 优化：当 ``numba`` 可用时，``l2`` 度量下的距离计算切换到 ``@njit(parallel=True,
fastmath=True, cache=True)`` 装饰的并行实现；首次调用 JIT 编译 ~1s，之后命中磁盘
缓存可秒级启动。其他度量（``cosine`` / ``ip``）继续使用 BLAS 加速的 numpy 路径。

线程安全说明：
    numba 默认的 ``workqueue`` threading layer 在多 caller 线程同时调用 ``parallel=True``
    函数时会触发崩溃（"Concurrent access has been detected"）。本模块通过
    :data:`_NUMBA_LOCK` 在 ``njit(parallel=True)`` 内核入口加一把全局非阻塞锁：
    第一个进入的 caller 享受 numba 并行加速，并发 caller 直接降级到 numpy/BLAS 路径，
    既保证 single-thread 场景下的最佳吞吐，又避免 multi-thread caller（如 F1 worker）
    崩溃。
"""

from __future__ import annotations

import threading
from typing import Any

import numpy as np

from app.services.ann.base import IndexBackend

try:
    from numba import njit, prange

    _NUMBA = True
except ImportError:  # pragma: no cover - 仅在 numba 缺失环境触发
    _NUMBA = False

_NUMBA_LOCK = threading.Lock()

if _NUMBA:

    @njit(parallel=True, fastmath=True, cache=True)  # type: ignore[misc]
    def _l2_sq_dists_numba(vectors: np.ndarray, query: np.ndarray) -> np.ndarray:
        """numba 并行计算单条 query 到 ``vectors`` 的逐行平方欧氏距离。

        与 :meth:`BruteBackend._pairwise_distances` 在 ``l2`` 分支返回的语义一致：
        都是 ``||a - b||^2``（未开方）。开方留给上层根据 metric 决定。

        Args:
            vectors: 形状 ``(N, D)`` 的底库向量，``float32``。
            query: 形状 ``(D,)`` 的查询向量，``float32``。

        Returns:
            np.ndarray: 形状 ``(N,)`` 的平方距离，``float32``。
        """
        n, d = vectors.shape
        out = np.empty(n, dtype=np.float32)
        for i in prange(n):
            s = 0.0
            for j in range(d):
                diff = vectors[i, j] - query[j]
                s += diff * diff
            out[i] = s
        return out
else:  # pragma: no cover - 仅在 numba 缺失环境触发
    _l2_sq_dists_numba = None  # type: ignore[assignment]


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
        use_numba: 是否允许走 numba 加速路径，仅在 ``l2`` 度量且
            :data:`_NUMBA` 为 ``True`` 时生效。便于基准测试通过显式关闭
            得到 numpy / numba 对比数据。
    """

    def __init__(self, dim: int, metric: str = "l2", use_numba: bool = True) -> None:
        if metric not in {"l2", "ip", "cosine"}:
            raise ValueError(f"BruteBackend 不支持的 metric: {metric}")
        self.dim = int(dim)
        self.metric = metric
        self.use_numba = bool(use_numba)
        self._vectors: np.ndarray | None = None

    @property
    def name(self) -> str:
        return "brute"

    @property
    def numba_active(self) -> bool:
        """当前 search 是否会真正走 numba 加速路径。"""
        return bool(_NUMBA and self.use_numba and self.metric == "l2")

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

    def _search_l2_numba(
        self, query: np.ndarray, top_k: int
    ) -> tuple[np.ndarray, np.ndarray]:
        """numba 加速版的 ``l2`` 距离 top-k 检索（按 query 循环外、内部 prange 并行）。

        每条 query 进入 :func:`_l2_sq_dists_numba` 并行计算到全部底库的平方距离，
        再用 ``np.argpartition`` / ``np.argsort`` 截取前 ``top_k`` 个；返回的
        ``distances`` 与 numpy 路径保持相同语义（未开方），上层代码无需感知。

        Args:
            query: 形状 ``(M, D)`` 的查询向量，已通过 :meth:`search` 校正
                dtype / contiguous / 归一化。
            top_k: 取前 k 个；调用方需确保为正整数。

        Returns:
            tuple[np.ndarray, np.ndarray]: ``(indices(M, k), distances(M, k))``。
        """
        assert self._vectors is not None
        assert _l2_sq_dists_numba is not None
        m = int(query.shape[0])
        n_base = int(self._vectors.shape[0])
        k = min(int(top_k), n_base)
        kth = max(min(k - 1, n_base - 1), 0)

        all_idx = np.empty((m, k), dtype=np.int64)
        all_dist = np.empty((m, k), dtype=np.float32)
        vectors = np.ascontiguousarray(self._vectors, dtype=np.float32)
        for i in range(m):
            dists = _l2_sq_dists_numba(vectors, query[i])
            idx_part = np.argpartition(dists, kth=kth)[:k]
            order = np.argsort(dists[idx_part])
            all_idx[i] = idx_part[order]
            all_dist[i] = dists[idx_part[order]]
        return all_idx, all_dist

    def _search_numpy(self, q: np.ndarray, top_k: int) -> tuple[np.ndarray, np.ndarray]:
        """numpy/BLAS 路径的 top-k 检索（原始实现）。"""
        distances = self._pairwise_distances(q)
        n_base = distances.shape[1]
        k = min(int(top_k), n_base)
        kth = max(min(k - 1, n_base - 1), 0)
        idx_part = np.argpartition(distances, kth=kth, axis=1)[:, :k]
        row_idx = np.arange(distances.shape[0])[:, None]
        top_dist = distances[row_idx, idx_part]
        order = np.argsort(top_dist, axis=1)
        return idx_part[row_idx, order], top_dist[row_idx, order]

    def search(self, query: np.ndarray, top_k: int) -> tuple[np.ndarray, np.ndarray]:
        if self._vectors is None:
            raise RuntimeError("索引尚未构建")
        q = np.ascontiguousarray(np.atleast_2d(query), dtype=np.float32)
        if self.metric == "cosine":
            qn = np.linalg.norm(q, axis=1, keepdims=True)
            qn[qn == 0] = 1.0
            q = q / qn

        # P2: numba parallel=True 内核非线程安全（workqueue layer），通过非阻塞锁
        # 保证单线程独享；并发 caller 自动降级到 numpy/BLAS 路径，避免崩溃。
        if self.numba_active and _NUMBA_LOCK.acquire(blocking=False):
            try:
                return self._search_l2_numba(q, int(top_k))
            finally:
                _NUMBA_LOCK.release()
        return self._search_numpy(q, int(top_k))

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
