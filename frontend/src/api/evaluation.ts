import { httpClient } from './client';
import type {
  BenchmarkRequest,
  BenchmarkResult,
  BenchmarkSummary,
  BenchmarkTaskHandle,
  SearchStats,
} from '@/types/evaluation';

// 索引评测 + 检索日志统计 API
export const evaluationApi = {
  run: async (payload: BenchmarkRequest): Promise<BenchmarkTaskHandle> => {
    const { data } = await httpClient.post<BenchmarkTaskHandle>('/evaluation/run', payload);
    return data;
  },

  latest: async (indexId: number): Promise<BenchmarkResult> => {
    const { data } = await httpClient.get<BenchmarkResult>(`/evaluation/${indexId}/latest`);
    return data;
  },

  list: async (datasetId?: number): Promise<BenchmarkSummary[]> => {
    const { data } = await httpClient.get<BenchmarkSummary[]>('/evaluation/results', {
      params: datasetId !== undefined ? { dataset_id: datasetId } : undefined,
    });
    return data;
  },

  searchStats: async (datasetId?: number): Promise<SearchStats> => {
    const { data } = await httpClient.get<SearchStats>('/stats/search', {
      params: datasetId !== undefined ? { dataset_id: datasetId } : undefined,
    });
    return data;
  },
};
