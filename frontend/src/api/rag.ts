// RAG 自然语言查询 API 封装
import { httpClient } from './client';
import type { RagQueryRequest, RagResponse } from '@/types/rag';

export const ragApi = {
  /**
   * 发起 RAG 查询：自然语言 -> LLM 解析 -> ANN 检索 -> LLM 总结
   * @param payload dataset_id / query 必填，index_id / top_k 可选
   */
  query: async (payload: RagQueryRequest): Promise<RagResponse> => {
    const { data } = await httpClient.post<RagResponse>('/rag/query', payload);
    return data;
  },
};
