// 数据集相关类型：字段命名与后端 Pydantic schema 严格一致（snake_case）

export type DatasetStatusName = 'uploading' | 'preprocessing' | 'ready' | 'failed';

export interface Dataset {
  id: number;
  owner_id: number;
  name: string;
  status: DatasetStatusName;
  cell_count: number | null;
  vector_dim: number | null;
  vector_source: string | null;
  meta_columns: string[] | null;
  created_at: string;
}

export interface DatasetStatus {
  dataset_id: number;
  status: DatasetStatusName;
  cell_count: number | null;
  vector_dim: number | null;
  vector_source: string | null;
  meta_columns: string[] | null;
}

export interface DatasetUploadResponse {
  dataset: Dataset;
  task_id: string;
}

export interface DatasetDeleteResponse {
  deleted: boolean;
  dataset_id: number;
}

export interface UmapResponse {
  dataset_id: number;
  has_umap: boolean;
  coords: number[][] | null;
  cell_ids: string[] | null;
  sampled: boolean;
  total_cells: number;
}

// 后端写盘进度（与 schemas/dataset.py::UploadProgressResponse 对齐）
export interface UploadProgressResponse {
  dataset_id: number;
  status: DatasetStatusName;
  bytes_received: number | null;
  total_bytes: number | null;
  percent: number | null;
}
