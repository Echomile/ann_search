// RAG 自然语言查询 API 封装（v1.2 D4 LLM Function Calling Agent）
import { httpClient } from './client';
import type {
  RagChatRequest,
  RagChatResponse,
  RagSession,
  RagSessionDetail,
} from '@/types/rag';

export const ragApi = {
  /**
   * 多轮 LLM Function Calling Agent 聊天。
   *
   * @param payload query 必填；session_id 为空时新建；dataset_id 是 LLM 的上下文提示
   * @returns 包含最终回答 + 工具调用链路 + 引用列表 + session_id 的响应
   */
  chatQuery: async (payload: RagChatRequest): Promise<RagChatResponse> => {
    const { data } = await httpClient.post<RagChatResponse>('/rag/query', payload);
    return data;
  },

  /** 列出当前用户的所有 RAG 会话，按 updated_at 倒序 */
  listSessions: async (): Promise<RagSession[]> => {
    const { data } = await httpClient.get<RagSession[]>('/rag/sessions');
    return data;
  },

  /** 拉取指定会话的完整消息列表（含 user/assistant/tool 三类） */
  getSession: async (sessionId: number): Promise<RagSessionDetail> => {
    const { data } = await httpClient.get<RagSessionDetail>(`/rag/sessions/${sessionId}`);
    return data;
  },

  /** 删除指定会话（级联清空消息） */
  deleteSession: async (sessionId: number): Promise<void> => {
    await httpClient.delete(`/rag/sessions/${sessionId}`);
  },
};
