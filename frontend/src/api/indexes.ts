import { httpClient } from './client';
import type {
  IndexCreateRequest,
  IndexCreateResponse,
  IndexRecord,
  IndexStatus,
} from '@/types/indexRecord';
import type { SubgraphQuery, SubgraphResponse } from '@/types/subgraph';

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

  // D2 扩展功能：拉取 HNSW 索引在 cell 周围的局部邻居子图（用于可视化）
  getSubgraph: async (id: number, query: SubgraphQuery): Promise<SubgraphResponse> => {
    const { data } = await httpClient.get<SubgraphResponse>(`/indexes/${id}/subgraph`, {
      params: {
        cell_id: query.cell_id,
        depth: query.depth,
        layer: query.layer,
        max_nodes: query.max_nodes,
      },
    });
    return data;
  },
};
