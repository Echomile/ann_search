"""基于 hnswlib 的 ANN 后端实现。"""

from __future__ import annotations

from collections import deque
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

    def _get_layer_neighbors(self, label: int, layer: int) -> list[int]:
        """获取节点 ``label`` 在指定层的邻居列表。

        实现策略（按优先级 fallback）：
            1. **真实 HNSW 邻接表**：若运行时 ``hnswlib.Index`` 暴露
               ``get_neighbors_list(layer=...)``（hnswlib 主干 >=0.8 路线图中的特性）
               则按 ``label`` 查表返回真实邻接。
            2. **kNN 近邻近似**：若 API 不可用（典型如发行版 0.8.0），用底层向量
               跑一次 ``knn_query(self_vector, k=M+1)`` 取除自身外的 Top-M 个候选，
               近似 layer-0 邻接关系；该回退仅供 D2 邻居图可视化使用，
               与真实 HNSW 内部边集存在差异但拓扑量级一致。

        Args:
            label: 节点 label（与 :meth:`build` 时 ``add_items`` 的 id 对齐）。
            layer: HNSW 层；非 0 层在 fallback 模式下统一退化为 layer-0 近邻。

        Returns:
            list[int]: 邻居 label 列表（不含 ``label`` 自身）。

        Raises:
            RuntimeError: 索引尚未构建或加载。
        """
        if self._index is None:
            raise RuntimeError("索引尚未构建或加载")

        get_neighbors = getattr(self._index, "get_neighbors_list", None)
        if callable(get_neighbors):
            adjacency = get_neighbors(layer=int(layer))
            if label < 0 or label >= len(adjacency):
                return []
            return [int(n) for n in adjacency[label] if int(n) != int(label)]

        # Fallback: 用 knn_query 近似 layer-0 邻居（高层在 fallback 下与 0 层同语义）
        try:
            self_vec = np.asarray(self._index.get_items([int(label)]), dtype=np.float32)
        except Exception as exc:  # noqa: BLE001
            raise IndexError(f"label {label} 不在索引中") from exc
        if self_vec.size == 0:
            raise IndexError(f"label {label} 不在索引中")
        # 取 M+1 个：去掉自身后约等于真实 layer-0 邻居规模
        k = min(self._m + 1, self._num_elements)
        prev_ef = self._ef_search
        # 提升 ef 保证邻居查询稳定；查询完恢复原值
        self._index.set_ef(max(prev_ef, k * 2))
        try:
            labels, _ = self._index.knn_query(self_vec, k=k)
        finally:
            self._index.set_ef(prev_ef)
        neighbors = [int(x) for x in np.asarray(labels)[0] if int(x) != int(label)]
        return neighbors

    def get_local_subgraph(
        self,
        entry_label: int,
        depth: int = 2,
        layer: int = 0,
        max_nodes: int = 200,
    ) -> dict[str, Any]:
        """从 ``entry_label`` 出发做 BFS, 返回 depth 跳内的局部子图。

        BFS 规则：
            - 从 ``entry_label`` 出发逐层扩展，记录已访问节点 + 深度；
            - 仅当 ``depth_visited[src] < depth`` 时把 ``(src, dst)`` 加入边集，
              避免外圈 ring 节点之间的边把可视化变成一团毛球；
            - 触发 ``max_nodes`` 上限后停止扩张，并把 ``truncated`` 置为 ``True``。

        Args:
            entry_label: 起点节点 label（即 hnswlib 内部 index，与 ``build`` 时
                ``add_items`` 的 ids 对齐）。
            depth: BFS 深度，常见取值 1/2/3。
            layer: HNSW 层，0 = 底层全连接，越高层越稀疏。
            max_nodes: 安全上限，避免高 depth 爆炸。

        Returns:
            dict[str, Any]: 形如
                ``{"nodes": [{"id": int, "depth": int}, ...],
                "edges": [{"src": int, "dst": int}, ...],
                "entry": entry_label, "layer": layer, "depth": depth,
                "truncated": bool}``。

        Raises:
            RuntimeError: 索引尚未构建。
            IndexError: ``entry_label`` 不在索引中。
            ValueError: ``depth`` / ``max_nodes`` 非法。
        """
        if self._index is None:
            raise RuntimeError("索引尚未构建或加载")
        if depth < 1:
            raise ValueError(f"depth 必须 >= 1: {depth}")
        if max_nodes < 1:
            raise ValueError(f"max_nodes 必须 >= 1: {max_nodes}")
        entry = int(entry_label)
        if entry < 0 or entry >= self._num_elements:
            raise IndexError(f"entry_label {entry} 不在索引中")

        # BFS：visited 同时承担「已加入 nodes」与「记录深度」两个职责
        visited: dict[int, int] = {entry: 0}
        edge_set: set[tuple[int, int]] = set()
        queue: deque[int] = deque([entry])
        truncated = False

        while queue:
            src = queue.popleft()
            src_depth = visited[src]
            if src_depth >= depth:
                # 外圈节点只作为终点接收边，不再向外扩张
                continue
            try:
                neighbors = self._get_layer_neighbors(src, layer)
            except IndexError:
                # 某些 label 可能在 fallback 路径下取不到向量，跳过即可
                continue
            for dst in neighbors:
                if dst not in visited:
                    if len(visited) >= max_nodes:
                        truncated = True
                        break
                    visited[dst] = src_depth + 1
                    queue.append(dst)
                # 边只在 src_depth < depth 时加入：保留 BFS 树 + 同深度横向边
                edge = (src, dst) if src <= dst else (dst, src)
                edge_set.add(edge)
            if truncated:
                break

        nodes = [{"id": label, "depth": d} for label, d in visited.items()]
        edges = [{"src": s, "dst": d} for s, d in edge_set]
        return {
            "nodes": nodes,
            "edges": edges,
            "entry": entry,
            "layer": int(layer),
            "depth": int(depth),
            "truncated": truncated,
        }
