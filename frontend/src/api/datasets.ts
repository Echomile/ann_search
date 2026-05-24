import type { AxiosProgressEvent } from 'axios';
import { httpClient } from './client';
import type {
  Dataset,
  DatasetDeleteResponse,
  DatasetStatus,
  DatasetUploadResponse,
  UmapResponse,
  UploadProgressResponse,
} from '@/types/dataset';
import type {
  AlignedDataset,
  AlignedDatasetDeleteResponse,
  AlignRequest,
} from '@/types/aligned';

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

  uploadProgress: async (id: number): Promise<UploadProgressResponse> => {
    const { data } = await httpClient.get<UploadProgressResponse>(
      `/datasets/${id}/upload-progress`,
    );
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

// 跨数据集语义对齐 API（v1.2 D7 加分项）
export const alignmentApi = {
  /** 触发同步对齐 */
  align: async (payload: AlignRequest): Promise<AlignedDataset> => {
    const { data } = await httpClient.post<AlignedDataset>('/datasets/align', payload);
    return data;
  },

  /** 列出当前用户的对齐数据集 */
  list: async (): Promise<AlignedDataset[]> => {
    const { data } = await httpClient.get<AlignedDataset[]>('/datasets/aligned');
    return data;
  },

  /** 查询单个对齐数据集详情 */
  get: async (id: number): Promise<AlignedDataset> => {
    const { data } = await httpClient.get<AlignedDataset>(`/datasets/aligned/${id}`);
    return data;
  },

  /** 删除对齐数据集（含磁盘文件） */
  remove: async (id: number): Promise<AlignedDatasetDeleteResponse> => {
    const { data } = await httpClient.delete<AlignedDatasetDeleteResponse>(
      `/datasets/aligned/${id}`,
    );
    return data;
  },
};
