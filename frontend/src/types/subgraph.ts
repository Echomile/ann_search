// HNSW 局部邻居子图相关类型（v1.2 D2 扩展功能）
// 字段命名与后端 SubgraphResponse / SubgraphNode / SubgraphEdge 严格一致

export interface SubgraphNode {
  label: number;
  cell_id: string;
  depth: number;
  is_entry: boolean;
  is_topk: boolean;
  cell_type: string | null;
}

export interface SubgraphEdge {
  src: number;
  dst: number;
}

export interface SubgraphResponse {
  nodes: SubgraphNode[];
  edges: SubgraphEdge[];
  entry_label: number;
  entry_cell_id: string;
  layer: number;
  depth: number;
  truncated: boolean;
  backend: string;
}

export interface SubgraphQuery {
  cell_id: string;
  depth: number;
  layer: number;
  max_nodes: number;
}
