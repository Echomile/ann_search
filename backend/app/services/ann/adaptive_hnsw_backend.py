"""自适应 ef_search 的 HNSW 后端。

在标准 :class:`HnswlibBackend` 之上叠加查询期自适应策略：

- 起始 ``ef_search`` 设为较小的 ``min_ef``（默认 ``32``）。
- **首轮**通过 **相对距离间隔**（``relative gap``）做 *早停*：
  ``(dist[k] - dist[k-1]) / (dist[k-1] - dist[0])`` 反映 top-k 边界
  与内部分散度的比值，跳变越明显说明 top-k 越确定。
- **后续轮**直接比较与上一轮 top-k 的 **集合重合度** ``overlap@k``：
  若 ``overlap >= overlap_threshold`` 视为"召回收敛"，停止扩张 ``ef``。
- 上限 ``max_ef`` 默认 ``512``，超过即停止。
- 批量查询时按 query 粒度独立判定，已稳定的 query 提前返回，
  仅对未稳定的子集再次提升 ``ef`` 重查，避免整批拖慢。

返回值的形状、语义与 :class:`HnswlibBackend.search` 完全一致；
最近一次 ``search`` 的元数据（每个 query 的最终 ``ef`` 与重试次数）
通过 :pyattr:`last_search_meta` 暴露，便于基准脚本采集。
"""

from __future__ import annotations

from typing import Any

import numpy as np

from app.core.logging import get_logger
from app.services.ann.hnswlib_backend import HnswlibBackend

logger = get_logger(__name__)


class AdaptiveHnswBackend(HnswlibBackend):
    """带自适应 ``ef_search`` 策略的 HNSW 后端。

    继承 :class:`HnswlibBackend` 复用 ``build / save / load / memory_mb``
    等接口，仅重写 :meth:`search` 与 :pyattr:`name`。

    Attributes:
        min_ef: 自适应过程的初始 ``ef_search``。
        max_ef: ``ef_search`` 的上限。
        gap_threshold: 首轮的早停阈值。当 ``(dist[k] - dist[k-1]) /
            (dist[k-1] - dist[0] + eps) >= gap_threshold`` 时认为
            top-k 边界清晰，可提前返回。
        overlap_threshold: 后续轮的收敛阈值。当当前轮与上一轮 top-k
            集合的 Jaccard-like 重合度 ``|prev ∩ cur| / k`` 不低于
            该阈值时认为召回收敛。
        oversample: 检索时多取的候选数，用于计算 ``dist[k]``。
        last_search_meta: 最近一次 ``search`` 的元数据字典。
    """

    def __init__(
        self,
        dim: int,
        metric: str = "l2",
        *,
        min_ef: int = 32,
        max_ef: int = 512,
        gap_threshold: float = 0.05,
        overlap_threshold: float = 0.90,
        oversample: int = 8,
    ) -> None:
        """初始化自适应后端。

        Args:
            dim: 向量维度。
            metric: 距离度量，取值 ``l2 | cosine | ip``。
            min_ef: 起始 ``ef_search``。
            max_ef: ``ef_search`` 的上限。
            gap_threshold: 首轮的相对距离间隔早停阈值。
            overlap_threshold: 后续轮的 top-k 集合重合度收敛阈值，``[0, 1]``。
            oversample: 多取的候选数。

        Raises:
            ValueError: 参数非法。
        """
        super().__init__(dim=dim, metric=metric)
        if min_ef <= 0 or max_ef < min_ef:
            raise ValueError(f"非法 ef 区间: min_ef={min_ef}, max_ef={max_ef}")
        if gap_threshold < 0:
            raise ValueError(f"gap_threshold 必须非负: {gap_threshold}")
        if not 0.0 <= overlap_threshold <= 1.0:
            raise ValueError(f"overlap_threshold 应在 [0, 1]: {overlap_threshold}")
        if oversample < 1:
            raise ValueError(f"oversample 必须 >= 1: {oversample}")
        self.min_ef = int(min_ef)
        self.max_ef = int(max_ef)
        self.gap_threshold = float(gap_threshold)
        self.overlap_threshold = float(overlap_threshold)
        self.oversample = int(oversample)
        self.last_search_meta: dict[str, Any] = {}

    @property
    def name(self) -> str:
        return "adaptive-hnsw"

    def build(self, vectors: np.ndarray, **params: Any) -> None:
        """构建索引并将 ``ef_search`` 初始化为 :pyattr:`min_ef`。"""
        params.setdefault("ef_search", self.min_ef)
        super().build(vectors, **params)

    def search(self, query: np.ndarray, top_k: int) -> tuple[np.ndarray, np.ndarray]:
        """自适应检索。

        Args:
            query: 形状 ``(M, D)`` 或 ``(D,)`` 的查询向量。
            top_k: 返回近邻数量。

        Returns:
            tuple[np.ndarray, np.ndarray]: ``(indices, distances)``，
                形状均为 ``(M, top_k)``。
        """
        if self._index is None:
            raise RuntimeError("索引尚未构建或加载")

        q = np.ascontiguousarray(np.atleast_2d(query), dtype=np.float32)
        n = q.shape[0]
        k = int(top_k)
        k_query = min(k + self.oversample, self._num_elements)

        final_labels = np.full((n, k), -1, dtype=np.int64)
        final_dist = np.full((n, k), np.inf, dtype=np.float32)
        final_ef = np.zeros(n, dtype=np.int32)
        final_retries = np.zeros(n, dtype=np.int32)
        prev_top_k = np.full((n, k), -1, dtype=np.int64)

        pending = np.arange(n)
        ef = self.min_ef
        retry = 0
        eps = 1e-9

        while pending.size > 0:
            self._index.set_ef(int(max(ef, k_query)))
            labels, dists = self._index.knn_query(q[pending], k=k_query)
            labels = np.asarray(labels, dtype=np.int64)
            dists = np.asarray(dists, dtype=np.float32)

            if retry == 0:
                if k_query > k:
                    inner_span = dists[:, k - 1] - dists[:, 0] + eps
                    gap = (dists[:, k] - dists[:, k - 1]) / inner_span
                    stable = gap >= self.gap_threshold
                else:
                    stable = np.ones(pending.size, dtype=bool)
            else:
                overlap = self._overlap_against(prev_top_k, labels[:, :k], pending)
                stable = overlap >= self.overlap_threshold

            is_last_round = ef >= self.max_ef
            done_mask = stable | is_last_round

            done_idx = pending[done_mask]
            if done_idx.size > 0:
                final_labels[done_idx] = labels[done_mask, :k]
                final_dist[done_idx] = dists[done_mask, :k]
                final_ef[done_idx] = ef
                final_retries[done_idx] = retry

            if is_last_round:
                break

            unstable_mask = ~done_mask
            unstable_idx = pending[unstable_mask]
            prev_top_k[unstable_idx] = labels[unstable_mask, :k]

            pending = unstable_idx
            ef = min(ef * 2, self.max_ef)
            retry += 1

        self._ef_search = int(self.min_ef)
        self._index.set_ef(self._ef_search)

        mean_ef = float(final_ef.mean()) if n > 0 else 0.0
        max_retries = int(final_retries.max()) if n > 0 else 0
        self.last_search_meta = {
            "mean_ef": mean_ef,
            "max_ef_used": int(final_ef.max()) if n > 0 else 0,
            "max_retries": max_retries,
            "ef_per_query": final_ef.tolist(),
            "retries_per_query": final_retries.tolist(),
        }
        if max_retries > 0:
            logger.debug(
                "adaptive-hnsw search: n=%d mean_ef=%.1f max_retries=%d",
                n,
                mean_ef,
                max_retries,
            )
        return final_labels, final_dist

    @staticmethod
    def _overlap_against(
        prev_top_k: np.ndarray, cur_top_k: np.ndarray, pending: np.ndarray
    ) -> np.ndarray:
        """按 query 计算与上一轮 top-k 的集合重合度。

        Args:
            prev_top_k: 形状 ``(N, k)`` 的上一轮 top-k（按 query 全局索引）。
            cur_top_k: 形状 ``(P, k)`` 的当前轮 top-k（按 ``pending`` 子集顺序）。
            pending: 待评估的 query 全局索引数组，长度 ``P``。

        Returns:
            np.ndarray: 形状 ``(P,)`` 的 ``[0, 1]`` 重合度比例。
        """
        if cur_top_k.size == 0:
            return np.zeros(0, dtype=np.float32)
        k = cur_top_k.shape[1]
        overlap = np.empty(pending.size, dtype=np.float32)
        for i, qi in enumerate(pending):
            prev = prev_top_k[qi]
            if prev[0] < 0:
                overlap[i] = 0.0
                continue
            overlap[i] = np.intersect1d(prev, cur_top_k[i], assume_unique=False).size / k
        return overlap
