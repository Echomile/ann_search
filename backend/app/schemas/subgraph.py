"""HNSW 局部邻居子图相关 schema（v1.2 D2 加分项）。

用于 :func:`app.api.v1.indexes.get_index_subgraph` 接口的入参/出参契约：
前端拿到这些结构后通过 Plotly 渲染节点 + 边，让用户直观看到 HNSW 小世界图。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SubgraphNode(BaseModel):
    """子图节点。

    Attributes:
        label: hnswlib 内部 index，与 build 时 ``add_items`` 的 ids 对齐。
        cell_id: 业务侧细胞编号，由 API 层通过 ``cell_ids[label]`` 映射得到。
        depth: 距离 entry 的 BFS 深度；entry 自身为 0。
        is_entry: 是否查询起点。
        is_topk: 该节点是否在 entry 的 ANN Top-K 结果中（第一版可全为 False）。
        cell_type: 可选 metadata 字段（前端用于着色 / hover 展示）。
    """

    label: int = Field(..., description="hnswlib 内部 index")
    cell_id: str = Field(..., description="业务细胞编号")
    depth: int = Field(..., ge=0, description="距离 entry 的 BFS 深度")
    is_entry: bool = Field(False, description="是否查询起点")
    is_topk: bool = Field(False, description="是否在 entry 的 ANN Top-K 结果中")
    cell_type: str | None = Field(None, description="可选 cell_type metadata，用于前端着色")


class SubgraphEdge(BaseModel):
    """子图无向边（按 label 表示）。"""

    src: int = Field(..., description="起点 label")
    dst: int = Field(..., description="终点 label")


class SubgraphResponse(BaseModel):
    """``GET /indexes/{id}/subgraph`` 响应。

    Attributes:
        nodes: 节点列表，第一个元素通常为 entry（``is_entry=True``）。
        edges: 边列表，已按 ``(min, max)`` 去重；前端直接连线即可。
        entry_label: 起点节点 label。
        entry_cell_id: 起点对应的 cell_id（便于前端 hover 文案）。
        layer: HNSW 层。
        depth: BFS 深度。
        truncated: 是否因 ``max_nodes`` 截断（前端可据此显示警告）。
        backend: 索引后端标识，如 ``hnswlib`` / ``adaptive-hnsw``。
    """

    nodes: list[SubgraphNode] = Field(default_factory=list, description="子图节点列表")
    edges: list[SubgraphEdge] = Field(default_factory=list, description="子图边列表")
    entry_label: int = Field(..., description="起点节点 label")
    entry_cell_id: str = Field(..., description="起点 cell_id")
    layer: int = Field(..., ge=0, description="HNSW 层")
    depth: int = Field(..., ge=1, description="BFS 深度")
    truncated: bool = Field(False, description="是否因 max_nodes 截断")
    backend: str = Field(..., description="索引后端标识")
