import { httpClient } from './client';
import type { EvaluationMetric } from '@/types/search';

export interface EvaluationRunRequest {
  datasetId: number;
  indexIds: number[];
  topK: number;
  numQueries?: number;
}

// 性能评测 API
export const evaluationApi = {
  run: async (payload: EvaluationRunRequest): Promise<EvaluationMetric[]> => {
    const { data } = await httpClient.post<EvaluationMetric[]>('/evaluation/run', payload);
    return data;
  },

  history: async (datasetId?: number): Promise<EvaluationMetric[]> => {
    const { data } = await httpClient.get<EvaluationMetric[]>('/evaluation/history', {
      params: datasetId ? { datasetId } : undefined,
    });
    return data;
  },
};
