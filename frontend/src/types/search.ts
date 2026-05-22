// 检索相关类型

export type IndexType = 'flat' | 'hnsw' | 'ivf' | 'ivfpq' | 'lsh';
export type DistanceMetric = 'l2' | 'cosine' | 'ip';

export interface IndexInfo {
  id: number;
  datasetId: number;
  name: string;
  type: IndexType;
  metric: DistanceMetric;
  params: Record<string, number | string>;
  buildTimeMs?: number;
  sizeBytes?: number;
  status: 'building' | 'ready' | 'failed';
  createdAt: string;
}

export interface SearchRequest {
  indexId: number;
  topK: number;
  queryCellId?: string;
  queryVector?: number[];
  filters?: Record<string, string | string[]>;
}

export interface SearchHit {
  cellId: string;
  distance: number;
  rank: number;
  metadata?: Record<string, string | number>;
}

export interface SearchResponse {
  hits: SearchHit[];
  elapsedMs: number;
  indexId: number;
}

export interface EvaluationMetric {
  indexType: IndexType;
  recall: number;
  meanLatencyMs: number;
  p95LatencyMs: number;
  qps: number;
  buildTimeMs?: number;
}
