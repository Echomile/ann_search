import { httpClient } from './client';
import type {
  MultiDatasetSearchRequest,
  SearchByIdRequest,
  SearchByVectorRequest,
  SearchResponse,
} from '@/types/search';

// 相似检索 API
export const searchApi = {
  byId: async (payload: SearchByIdRequest): Promise<SearchResponse> => {
    const { data } = await httpClient.post<SearchResponse>('/search/by-id', payload);
    return data;
  },

  byVector: async (payload: SearchByVectorRequest): Promise<SearchResponse> => {
    const { data } = await httpClient.post<SearchResponse>('/search/by-vector', payload);
    return data;
  },

  multiDataset: async (payload: MultiDatasetSearchRequest): Promise<SearchResponse> => {
    const { data } = await httpClient.post<SearchResponse>('/search/multi-dataset', payload);
    return data;
  },
};
