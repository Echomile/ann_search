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
