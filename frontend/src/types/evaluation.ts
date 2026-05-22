// 索引评测相关类型，命名与后端 schema 一致

export interface BenchmarkRequest {
  index_id: number;
  num_queries: number;
  top_k_list: number[];
  concurrency_list: number[];
}

export interface LatencyStats {
  concurrency: number;
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
  qps: number;
  mean_ms: number;
  total_queries: number;
}

export interface BenchmarkResult {
  index_id: number;
  dataset_id: number | null;
  backend: string;
  metric: string | null;
  build_time_seconds: number | null;
  memory_mb: number | null;
  num_queries: number;
  recalls: Record<string, number>;
  latencies: LatencyStats[];
  finished_at: string | null;
}

export interface BenchmarkTaskHandle {
  task_id: string;
  index_id: number;
  status: string;
}

export interface BenchmarkSummary {
  index_id: number;
  dataset_id: number | null;
  backend: string;
  recalls: Record<string, number>;
  finished_at: string | null;
}
