import { httpClient } from './client';
import type { CreateDatasetRequest, Dataset } from '@/types/dataset';

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

  create: async (payload: CreateDatasetRequest, file: File): Promise<Dataset> => {
    const form = new FormData();
    form.append('name', payload.name);
    if (payload.description) form.append('description', payload.description);
    form.append('file', file);
    const { data } = await httpClient.post<Dataset>('/datasets', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    return data;
  },

  remove: async (id: number): Promise<void> => {
    await httpClient.delete(`/datasets/${id}`);
  },
};
