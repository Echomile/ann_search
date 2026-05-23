import type { AxiosProgressEvent } from 'axios';
import { httpClient } from './client';
import type {
  Dataset,
  DatasetDeleteResponse,
  DatasetStatus,
  DatasetUploadResponse,
  UmapResponse,
} from '@/types/dataset';

interface UploadOptions {
  onUploadProgress?: (event: AxiosProgressEvent) => void;
  signal?: AbortSignal;
}

// 孤儿数据集清理响应（与后端 OrphanCleanupResponse 对齐）
export interface OrphanCleanupResponse {
  deleted_ids: number[];
  count: number;
}

// 数据集管理 API
export const datasetsApi = {
  list: async (): Promise<Dataset[]> => {
    const { data } = await httpClient.get<Dataset[]>('/datasets');
    return data;
  },

  get: async (id: number): Promise<Dataset> => {
    const { data } = await httpClient.get<Dataset>(`/datasets/${id}`);
    return data;
  },

  upload: async (
    name: string,
    file: File,
    options?: UploadOptions,
  ): Promise<DatasetUploadResponse> => {
    const form = new FormData();
    form.append('name', name);
    form.append('file', file);
    const { data } = await httpClient.post<DatasetUploadResponse>('/datasets/upload', form, {
      onUploadProgress: options?.onUploadProgress,
      signal: options?.signal,
      timeout: 0,
    });
    return data;
  },

  remove: async (id: number): Promise<DatasetDeleteResponse> => {
    const { data } = await httpClient.delete<DatasetDeleteResponse>(`/datasets/${id}`);
    return data;
  },

  status: async (id: number): Promise<DatasetStatus> => {
    const { data } = await httpClient.get<DatasetStatus>(`/datasets/${id}/status`);
    return data;
  },

  cleanupOrphan: async (): Promise<OrphanCleanupResponse> => {
    const { data } = await httpClient.delete<OrphanCleanupResponse>('/datasets/orphan');
    return data;
  },

  umap: async (id: number): Promise<UmapResponse> => {
    const { data } = await httpClient.get<UmapResponse>(`/datasets/${id}/umap`);
    return data;
  },
};
