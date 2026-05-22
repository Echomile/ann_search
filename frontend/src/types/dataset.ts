// 数据集相关类型

export interface Dataset {
  id: number;
  name: string;
  description?: string;
  filename: string;
  numCells: number;
  numGenes: number;
  dim: number;
  status: 'pending' | 'processing' | 'ready' | 'failed';
  ownerId?: number;
  createdAt: string;
  updatedAt?: string;
}

export interface CreateDatasetRequest {
  name: string;
  description?: string;
}

export interface CellMetadata {
  cellId: string;
  cellType?: string;
  tissue?: string;
  donor?: string;
  [key: string]: string | number | undefined;
}
