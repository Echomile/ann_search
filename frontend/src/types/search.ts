// 检索相关类型，命名与后端 Pydantic schema 严格一致

export type SearchFilters = Record<string, string | number | boolean | (string | number)[]>;

export interface SearchByIdRequest {
  dataset_id: number;
  cell_id: string;
  top_k: number;
  filters?: SearchFilters | null;
  index_id?: number | null;
}

export interface SearchByVectorRequest {
  dataset_id: number;
  vector: number[];
  top_k: number;
  filters?: SearchFilters | null;
  index_id?: number | null;
}

export interface MultiDatasetSearchRequest {
  dataset_ids: number[];
  index_ids?: number[] | null;
  cell_id?: string | null;
  source_dataset_id?: number | null;
  vector?: number[] | null;
  top_k: number;
  filters?: SearchFilters | null;
}

export interface SearchHit {
  rank: number;
  cell_id: string;
  distance: number;
  meta: Record<string, string | number | boolean | null> | null;
  source_dataset_id: number | null;
}

export interface SearchResponse {
  dataset_id: number | null;
  top_k: number;
  latency_ms: number;
  index_backend: string | null;
  metric: string | null;
  total_candidates: number | null;
  hits: SearchHit[];
}

// 批量检索（F1）：N 个查询并发，单数据集，复用 Redis 检索缓存
export interface BatchQueryItem {
  cell_id?: string | null;
  vector?: number[] | null;
}

export interface BatchSearchRequest {
  dataset_id: number;
  index_id?: number | null;
  queries: BatchQueryItem[];
  top_k: number;
  filters?: SearchFilters | null;
}

export interface BatchSearchHitGroup {
  query_index: number;
  query_cell_id: string | null;
  hits: SearchHit[];
  latency_ms: number;
  cache_hit: boolean;
}

export interface BatchSearchResponse {
  dataset_id: number;
  top_k: number;
  total_queries: number;
  total_latency_ms: number;
  index_backend: string | null;
  metric: string | null;
  groups: BatchSearchHitGroup[];
}

// 多后端 ensemble 检索（F7）：同一数据集 2~5 个 index，z-score 归一化合并
export interface EnsembleSearchRequest {
  dataset_id: number;
  index_ids: number[];
  query: BatchQueryItem;
  top_k: number;
  filters?: SearchFilters | null;
}

export interface EnsembleHit {
  rank: number;
  cell_id: string;
  score: number;
  voted_by: number[];
  meta: Record<string, string | number | boolean | null> | null;
}

export interface EnsembleSearchResponse {
  dataset_id: number;
  top_k: number;
  latency_ms: number;
  hits: EnsembleHit[];
  // 后端 schema 用 ``dict[str, float]``：key 为 ``str(index_id)``
  per_index_latency_ms: Record<string, number>;
}

// v1.2 D1 · 带运行时参数调整的检索（参数仪表盘用）
export interface SearchWithParamsRequest {
  dataset_id: number;
  index_id?: number | null;
  cell_id?: string | null;
  vector?: number[] | null;
  top_k: number;
  runtime_params?: Record<string, number | string | boolean>;
  filters?: SearchFilters | null;
}

export interface SearchResponseWithParams extends SearchResponse {
  effective_params: Record<string, number | string | boolean>;
  ignored_params: string[];
}
