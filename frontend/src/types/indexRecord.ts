// ANN 索引记录相关类型，命名与后端 schema 保持一致

export type IndexBackend =
  | 'hnswlib'
  | 'faiss-hnsw'
  | 'faiss-ivfpq'
  | 'brute'
  | 'adaptive-hnsw'
  | 'sparse-brute';
export type DistanceMetric = 'l2' | 'cosine' | 'ip';
export type IndexStatusName = 'building' | 'ready' | 'failed';

export type IndexParams = Record<string, number | string>;

export interface IndexRecord {
  id: number;
  dataset_id: number;
  backend: IndexBackend;
  metric: DistanceMetric;
  params: IndexParams | null;
  index_path: string | null;
  build_time_seconds: number | null;
  memory_mb: number | null;
  status: IndexStatusName;
  created_at: string;
}

export interface IndexStatus {
  id: number;
  status: IndexStatusName;
  backend: IndexBackend;
  build_time_seconds: number | null;
  memory_mb: number | null;
}

export interface IndexCreateRequest {
  backend: IndexBackend;
  metric: DistanceMetric;
  params?: IndexParams;
}

export interface IndexCreateResponse {
  index: IndexRecord;
  task_id: string;
}
