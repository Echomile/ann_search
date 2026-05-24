// RAG 模块相关类型：字段命名与后端 Pydantic schema 严格一致（snake_case）

// =============================================================================
// v1.1 兼容类型（旧版 /rag/query 单轮响应仍可使用）
// =============================================================================

/** LLM 解析自然语言查询后产生的结构化条件 */
export interface ParsedQuery {
  cell_id: string | null;
  filters: Record<string, unknown>;
  top_k: number;
  intent: string;
}

/** RAG 检索命中条目（同 search hit 但 metadata 字段名不同） */
export interface RagHit {
  rank: number;
  cell_id: string;
  distance: number;
  metadata?: Record<string, unknown>;
  meta?: Record<string, unknown>;
  source_dataset_id?: number | null;
}

/** RAG 查询请求体（v1.1 旧版，未在前端使用） */
export interface RagQueryRequest {
  dataset_id: number;
  index_id?: number | null;
  query: string;
  top_k?: number;
}

/** RAG 查询响应（v1.1 旧版，未在前端使用） */
export interface RagResponse {
  parsed: ParsedQuery;
  hits: RagHit[];
  answer: string;
  query_time_ms: number;
}

// =============================================================================
// v1.2 D4 Function Calling Agent loop 类型
// =============================================================================

/** LLM 单次 function call 决策 */
export interface ToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
}

/** agent loop 中执行过的工具调用快照（响应可观察性字段） */
export interface ToolTraceItem {
  name: string;
  arguments: Record<string, unknown>;
  summary: string;
  ok: boolean;
}

/** 引用追溯条目 */
export interface RagCitation {
  cell_id: string;
  dataset_id: number | null;
}

/** 多轮聊天请求体 */
export interface RagChatRequest {
  query: string;
  session_id?: number | null;
  dataset_id?: number | null;
  max_iterations?: number;
}

/** 多轮聊天响应 */
export interface RagChatResponse {
  session_id: number;
  answer: string;
  tool_trace: ToolTraceItem[];
  citations: RagCitation[];
  iterations: number;
  finish_reason: 'stop' | 'max_iterations' | string;
  query_time_ms: number;
}

/** 会话列表条目（含 message_count 摘要） */
export interface RagSession {
  id: number;
  user_id: number;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

/** 单条消息详情 */
export interface RagMessage {
  id: number;
  session_id: number;
  role: 'user' | 'assistant' | 'tool' | 'system' | string;
  content: string | null;
  tool_calls: ToolCall[];
  tool_results: Array<{ tool_call_id: string; name?: string; result: Record<string, unknown> }>;
  created_at: string;
}

/** 会话详情含全部消息 */
export interface RagSessionDetail {
  id: number;
  user_id: number;
  title: string;
  created_at: string;
  updated_at: string;
  messages: RagMessage[];
}
