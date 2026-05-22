import { httpClient } from './client';
import type { DistanceMetric, IndexInfo, IndexType } from '@/types/search';

export interface CreateIndexRequest {
  datasetId: number;
  name: string;
  type: IndexType;
  metric: DistanceMetric;
  params?: Record<string, number | string>;
}

// 索引管理 API
export const indexesApi = {
  list: async (datasetId?: number): Promise<IndexInfo[]> => {
    const { data } = await httpClient.get<IndexInfo[]>('/indexes', {
      params: datasetId ? { datasetId } : undefined,
    });
    return data;
  },

  get: async (id: number): Promise<IndexInfo> => {
    const { data } = await httpClient.get<IndexInfo>(`/indexes/${id}`);
    return data;
  },

  create: async (payload: CreateIndexRequest): Promise<IndexInfo> => {
    const { data } = await httpClient.post<IndexInfo>('/indexes', payload);
    return data;
  },

  remove: async (id: number): Promise<void> => {
    await httpClient.delete(`/indexes/${id}`);
  },
};
