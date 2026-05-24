"""稀疏感知 ANN 后端: 直接在 CSR 格式的高维稀疏向量上做精确最近邻。

设计动机：
    单细胞 RNA-seq 表达矩阵天然稀疏（每个 cell 中约 90%+ 基因为 0）。常规
    pipeline 用 PCA 把基因维度降到 30~50 的稠密空间再做 ANN 检索，但 PCA
    会丢失稀有基因的强表达信号。本后端跳过 PCA，直接在原始 HVG（top-N
    high-variable genes，典型 5000）稀疏矩阵上做 brute-force 检索，作为
    单细胞 ANN 的稀疏-感知卖点；与 :class:`BruteBackend` 互为对照。

存储格式：
    底库矩阵以 scipy CSR 格式持久化为 ``.npz`` 文件（``scipy.sparse.save_npz``）。
    内存中 :class:`scipy.sparse.csr_matrix` 的占用 = ``data.nbytes +
    indices.nbytes + indptr.nbytes``。

距离计算：
    核心算子是稀疏-稠密点积 ``self._sparse_vectors @ query.T``，scipy 内部
    自动走 BLAS。其上派生三种度量的距离：

    - ``l2``: ``||a-b||^2 = ||a||^2 + ||b||^2 - 2 a·b``；底库范数 ``||a||^2``
      在 :meth:`build` / :meth:`load` 时一次性预计算并缓存。
    - ``cosine``: 行 L2 归一化后用 ``1 - a·b`` (a, b 已为单位向量)。
    - ``ip``: 取负内积，与其他后端的 "越小越近" 语义对齐。
"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy import sparse as sp

from app.services.ann.base import IndexBackend


def _sparse_row_l2_normalize(mat: sp.csr_matrix) -> sp.csr_matrix:
    """对 CSR 矩阵按行做 L2 归一化（纯 scipy 实现，保持稀疏性）。

    用对角缩放矩阵 ``diag(1 / ||row||_2)`` 左乘 ``mat`` 即可，全程不展开成
    稠密；零行（``||row||_2 == 0``）的缩放因子置为 ``1.0`` 防止除零。

    Args:
        mat: 任意形状的 CSR 矩阵，``dtype`` 通常为 ``float32``。

    Returns:
        scipy.sparse.csr_matrix: 行归一化后的 CSR 矩阵，``dtype`` 与输入一致。
    """
    sq_norms = np.asarray(mat.multiply(mat).sum(axis=1)).ravel()
    inv = np.where(sq_norms > 0, 1.0 / np.sqrt(sq_norms), 1.0).astype(mat.dtype, copy=False)
    return (sp.diags(inv) @ mat).tocsr()


def _row_sq_norms(mat: sp.csr_matrix) -> np.ndarray:
    """计算 CSR 矩阵每行的 L2 平方范数，``shape=(N,)``，``dtype=float32``。"""
    return np.asarray(mat.multiply(mat).sum(axis=1)).ravel().astype(np.float32, copy=False)


class SparseBruteBackend(IndexBackend):
    """基于 :class:`scipy.sparse.csr_matrix` 的精确最近邻后端。

    适用场景：
        - 高维稀疏向量（dim=1000~10000，稀疏度 80%+）；
        - 单细胞 HVG 基因表达矩阵直接做近邻检索，无需 PCA 降维；
        - 中小规模底库（N<=200k 时延迟仍可接受）；超大规模需要倒排/PQ 等
          稀疏专用索引（如 SPLADE / pyserini），不在本类目标范围内。

    支持度量：``l2``、``cosine``、``ip``；语义与 :class:`BruteBackend` 一致。

    Attributes:
        dim: 向量维度 (HVG 数量)。
        metric: 距离度量，``l2|cosine|ip``。
    """

    def __init__(self, dim: int, metric: str = "l2") -> None:
        """初始化稀疏后端。

        Args:
            dim: 向量维度。
            metric: 距离度量，仅支持 ``l2|cosine|ip``。

        Raises:
            ValueError: ``metric`` 不被支持。
        """
        if metric not in {"l2", "ip", "cosine"}:
            raise ValueError(f"SparseBruteBackend 不支持的 metric: {metric}")
        self.dim = int(dim)
        self.metric = metric
        self._sparse_vectors: sp.csr_matrix | None = None
        # 缓存底库每行的 ||a||^2，l2 距离公式专用
        self._sq_norms: np.ndarray | None = None

    @property
    def name(self) -> str:
        return "sparse-brute"

    def build(self, vectors: np.ndarray | sp.spmatrix, **_: Any) -> None:
        """构建索引。

        Args:
            vectors: 底库矩阵；接受 :class:`scipy.sparse.spmatrix`（任意子类，
                内部会 ``tocsr``）或 :class:`numpy.ndarray`（自动转 CSR）。
                形状 ``(N, dim)``。
            **_: 兼容 :class:`IndexBackend` 接口的关键字参数占位。

        Raises:
            ValueError: ``vectors`` 形状非法（列数不等于 ``self.dim``）。
        """
        if sp.issparse(vectors):
            csr = vectors.tocsr()
        else:
            arr = np.ascontiguousarray(vectors)
            if arr.ndim != 2:
                raise ValueError(f"vectors 应为 2D 矩阵，实际 ndim={arr.ndim}")
            csr = sp.csr_matrix(arr)

        if csr.shape[1] != self.dim:
            raise ValueError(f"vectors 形状应为 (N, {self.dim})，实际 {csr.shape}")

        if csr.dtype != np.float32:
            csr = csr.astype(np.float32, copy=False)

        if self.metric == "cosine":
            csr = _sparse_row_l2_normalize(csr)

        self._sparse_vectors = csr
        # 对 cosine 归一化后的矩阵预计算同样 work：每行 ||a||^2 == 1
        self._sq_norms = _row_sq_norms(csr) if self.metric == "l2" else None

    def search(self, query: np.ndarray, top_k: int) -> tuple[np.ndarray, np.ndarray]:
        """检索 Top-K 近邻。

        ``query`` 视为稠密向量（即使原始数据稀疏，单条 query 通常已转 dense）；
        若需要 sparse query，可先 ``.toarray()`` 再传入。距离公式利用稀疏-
        稠密点积 + 缓存的底库范数高效展开。

        Args:
            query: 查询向量，形状 ``(M, dim)`` 或 ``(dim,)``，``float32`` 优先。
            top_k: 返回的近邻数量。

        Returns:
            tuple[np.ndarray, np.ndarray]: ``(indices(M, k), distances(M, k))``，
                ``indices`` 为 ``int64``，``distances`` 为 ``float32``，按距离
                升序排列。

        Raises:
            RuntimeError: 索引尚未构建（``build`` 或 ``load`` 未调用）。
        """
        if self._sparse_vectors is None:
            raise RuntimeError("索引尚未构建")

        q = np.ascontiguousarray(np.atleast_2d(query), dtype=np.float32)
        if q.shape[1] != self.dim:
            raise ValueError(f"query 维度应为 {self.dim}，实际 {q.shape[1]}")

        if self.metric == "cosine":
            qn = np.linalg.norm(q, axis=1, keepdims=True)
            qn[qn == 0] = 1.0
            q = q / qn

        # sparse(N,D) @ dense(D,M) -> dense(N,M)。np.asarray 兜底防御
        # 老版 scipy 可能返回 np.matrix。
        ip = np.asarray(self._sparse_vectors @ q.T)  # (N, M) float32

        if self.metric == "l2":
            assert self._sq_norms is not None
            sq_q = np.sum(q * q, axis=1)  # (M,)
            # 广播：(N,1) + (1,M) - 2*(N,M) -> (N,M)
            distances = self._sq_norms[:, None] + sq_q[None, :] - 2.0 * ip
            distances = np.maximum(distances, 0.0).T  # 转 (M, N)
        elif self.metric == "cosine":
            distances = (1.0 - ip).T  # (M, N)
        else:  # ip
            distances = (-ip).T  # (M, N)

        distances = distances.astype(np.float32, copy=False)

        m, n_base = distances.shape
        k = min(int(top_k), n_base)
        kth = max(min(k - 1, n_base - 1), 0)
        idx_part = np.argpartition(distances, kth=kth, axis=1)[:, :k]
        row_idx = np.arange(m)[:, None]
        top_dist = distances[row_idx, idx_part]
        order = np.argsort(top_dist, axis=1)
        return (
            idx_part[row_idx, order].astype(np.int64, copy=False),
            top_dist[row_idx, order].astype(np.float32, copy=False),
        )

    def save(self, path: str) -> None:
        """将 CSR 索引持久化为 ``.npz`` 文件。

        Args:
            path: 文件路径；若不以 ``.npz`` 结尾，:func:`scipy.sparse.save_npz`
                会自动追加该后缀（注意调用方需感知）。

        Raises:
            RuntimeError: 索引尚未构建。
        """
        if self._sparse_vectors is None:
            raise RuntimeError("索引尚未构建")
        sp.save_npz(path, self._sparse_vectors)

    def load(self, path: str) -> None:
        """从 ``.npz`` 文件加载 CSR 索引。

        Args:
            path: 文件路径。
        """
        loaded = sp.load_npz(path)
        csr = loaded.tocsr() if not isinstance(loaded, sp.csr_matrix) else loaded
        if csr.dtype != np.float32:
            csr = csr.astype(np.float32, copy=False)
        if csr.shape[1] != self.dim:
            raise ValueError(f"加载的矩阵列数 {csr.shape[1]} 与 self.dim={self.dim} 不一致")
        self._sparse_vectors = csr
        self._sq_norms = _row_sq_norms(csr) if self.metric == "l2" else None

    def memory_mb(self) -> float:
        """估算 CSR 索引的内存占用 (MB)。

        包含三个 buffer：``data``（非零值）、``indices``（列索引）、``indptr``
        （行起止偏移）。不计入 ``_sq_norms`` 缓存（``float32 * N`` 通常 ≪ data）。

        Returns:
            float: MB 数；索引未构建时返回 ``0.0``。
        """
        if self._sparse_vectors is None:
            return 0.0
        s = self._sparse_vectors
        nbytes = s.data.nbytes + s.indices.nbytes + s.indptr.nbytes
        return float(nbytes / (1024 * 1024))
