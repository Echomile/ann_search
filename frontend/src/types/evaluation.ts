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

// 检索日志统计相关类型，严格 snake_case 对齐后端 /api/v1/stats/search

export interface DatasetStat {
  dataset_id: number;
  dataset_name: string | null;
  total_queries: number;
  avg_latency_ms: number;
  p95_latency_ms: number;
}

export interface HourlyBucket {
  hour_iso: string;
  queries: number;
  avg_latency_ms: number;
}

export interface SearchStats {
  total_queries: number;
  overall_avg_latency_ms: number;
  overall_p95_latency_ms: number;
  by_dataset: DatasetStat[];
  hourly_24h: HourlyBucket[];
}

// v1.2 C3 · recall-QPS 帕累托扫描相关类型

export interface SweepRequest {
  dataset_id: number;
  backends: string[];
  top_k?: number;
  query_count?: number;
  ef_search_grid?: number[] | null;
  nprobe_grid?: number[] | null;
}

export interface SweepPoint {
  id: number;
  backend: string;
  params_json: Record<string, number | string | boolean>;
  recall: number;
  qps: number;
  p50_ms: number;
  p95_ms: number;
  p99_ms: number | null;
  mem_mb: number;
  on_pareto: boolean;
  created_at: string;
}

export interface SweepRun {
  id: number;
  dataset_id: number;
  created_by: number | null;
  status: 'pending' | 'running' | 'done' | 'failed';
  top_k: number;
  query_count: number;
  started_at: string;
  finished_at: string | null;
  error: string | null;
  created_at: string;
  points: SweepPoint[];
  pareto_count: number;
}
