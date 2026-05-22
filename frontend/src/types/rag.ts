// RAG 模块相关类型：字段命名与后端 Pydantic schema 严格一致（snake_case）

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
  metadata: Record<string, unknown>;
  source_dataset_id?: number | null;
}

/** RAG 查询请求体 */
export interface RagQueryRequest {
  dataset_id: number;
  index_id?: number | null;
  query: string;
  top_k?: number;
}

/** RAG 查询响应：解析结果 + ANN 命中 + LLM 总结回答 + 耗时 */
export interface RagResponse {
  parsed: ParsedQuery;
  hits: RagHit[];
  answer: string;
  query_time_ms: number;
}
