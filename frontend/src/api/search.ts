import { httpClient } from './client';
import type { SearchRequest, SearchResponse } from '@/types/search';

// 检索 API
export const searchApi = {
  query: async (payload: SearchRequest): Promise<SearchResponse> => {
    const { data } = await httpClient.post<SearchResponse>('/search', payload);
    return data;
  },
};
