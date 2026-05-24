// 跨数据集语义对齐相关类型（v1.2 D7 扩展功能）
// 字段命名与后端 Pydantic schema 严格一致（snake_case）

export type AlignMethod = 'intersect_only' | 'harmony';

export type AlignedDatasetStatus = 'pending' | 'running' | 'done' | 'failed';

/** 触发对齐请求（POST /datasets/align） */
export interface AlignRequest {
  source_dataset_ids: number[];
  method?: AlignMethod;
  target_dim?: number;
  name?: string | null;
}

/** 对齐数据集详情响应 */
export interface AlignedDataset {
  id: number;
  name: string;
  source_dataset_ids: number[];
  method: string; // 实际生效方法（harmonypy 缺失时会回填为 intersect_only）
  target_dim: number;
  cell_count: number;
  common_genes_count: number;
  status: AlignedDatasetStatus;
  created_by: number | null;
  created_at: string;
  updated_at: string;
}

/** 删除对齐数据集响应 */
export interface AlignedDatasetDeleteResponse {
  deleted: boolean;
  aligned_dataset_id: number;
}
