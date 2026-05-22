import { httpClient } from './client';
import type {
  IndexCreateRequest,
  IndexCreateResponse,
  IndexRecord,
  IndexStatus,
} from '@/types/indexRecord';

// 索引管理 API
export const indexesApi = {
  listByDataset: async (datasetId: number): Promise<IndexRecord[]> => {
    const { data } = await httpClient.get<IndexRecord[]>(`/datasets/${datasetId}/indexes`);
    return data;
  },

  create: async (datasetId: number, payload: IndexCreateRequest): Promise<IndexCreateResponse> => {
    const { data } = await httpClient.post<IndexCreateResponse>(
      `/datasets/${datasetId}/indexes`,
      payload,
    );
    return data;
  },

  get: async (id: number): Promise<IndexRecord> => {
    const { data } = await httpClient.get<IndexRecord>(`/indexes/${id}`);
    return data;
  },

  status: async (id: number): Promise<IndexStatus> => {
    const { data } = await httpClient.get<IndexStatus>(`/indexes/${id}/status`);
    return data;
  },

  remove: async (id: number): Promise<void> => {
    await httpClient.delete(`/indexes/${id}`);
  },
};
