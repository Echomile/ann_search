import { httpClient } from './client';
import type {
  BenchmarkRequest,
  BenchmarkResult,
  BenchmarkSummary,
  BenchmarkTaskHandle,
  SearchStats,
  SweepPoint,
  SweepRequest,
  SweepRun,
} from '@/types/evaluation';

// 索引评测 + 检索日志统计 + v1.2 参数扫描 API
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

  // v1.2 C3: 触发一次 recall-QPS 帕累托扫描
  triggerSweep: async (payload: SweepRequest): Promise<SweepRun> => {
    const { data } = await httpClient.post<SweepRun>('/evaluation/sweep', payload);
    return data;
  },

  getSweep: async (sweepId: number): Promise<SweepRun> => {
    const { data } = await httpClient.get<SweepRun>(`/evaluation/sweep/${sweepId}`);
    return data;
  },

  getPareto: async (sweepId: number): Promise<SweepPoint[]> => {
    const { data } = await httpClient.get<SweepPoint[]>(`/evaluation/sweep/${sweepId}/pareto`);
    return data;
  },
};
