import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { useDatasetStore } from './datasetStore';
import type { Dataset } from '@/types/dataset';
import type { IndexRecord } from '@/types/indexRecord';

const datasetA: Dataset = {
  id: 1,
  owner_id: 100,
  name: 'liver-10k',
  status: 'ready',
  cell_count: 10000,
  vector_dim: 50,
  vector_source: 'X_pca',
  meta_columns: ['cell_type', 'tissue'],
  created_at: '2026-01-01T00:00:00Z',
};

const datasetB: Dataset = {
  ...datasetA,
  id: 2,
  name: 'lung-5k',
  cell_count: 5000,
};

const indexForA: IndexRecord = {
  id: 11,
  dataset_id: 1,
  backend: 'hnswlib',
  metric: 'l2',
  params: { M: 16, ef_construction: 200 },
  index_path: '/tmp/idx-11',
  build_time_seconds: 2.5,
  memory_mb: 32,
  status: 'ready',
  created_at: '2026-01-02T00:00:00Z',
};

/**
 * datasetStore 行为验证：当前选中数据集 / 索引切换的联动语义。
 *
 * 重点用例：切换数据集时，若当前 index 不属于新数据集应自动清空，避免脏选中。
 */
describe('useDatasetStore', () => {
  beforeEach(() => {
    localStorage.clear();
    useDatasetStore.setState({ currentDataset: null, currentIndex: null });
  });

  afterEach(() => {
    localStorage.clear();
    useDatasetStore.setState({ currentDataset: null, currentIndex: null });
  });

  it('初始状态均为 null', () => {
    const state = useDatasetStore.getState();
    expect(state.currentDataset).toBeNull();
    expect(state.currentIndex).toBeNull();
  });

  it('setCurrentDataset 写入当前数据集', () => {
    useDatasetStore.getState().setCurrentDataset(datasetA);

    expect(useDatasetStore.getState().currentDataset).toEqual(datasetA);
    expect(useDatasetStore.getState().currentIndex).toBeNull();
  });

  it('setCurrentIndex 写入当前索引', () => {
    useDatasetStore.getState().setCurrentDataset(datasetA);
    useDatasetStore.getState().setCurrentIndex(indexForA);

    expect(useDatasetStore.getState().currentIndex).toEqual(indexForA);
  });

  it('切换到同 dataset_id 的数据集时保留当前 index', () => {
    useDatasetStore.getState().setCurrentDataset(datasetA);
    useDatasetStore.getState().setCurrentIndex(indexForA);

    const refreshedA: Dataset = { ...datasetA, name: 'liver-10k-renamed' };
    useDatasetStore.getState().setCurrentDataset(refreshedA);

    const state = useDatasetStore.getState();
    expect(state.currentDataset?.name).toBe('liver-10k-renamed');
    expect(state.currentIndex).toEqual(indexForA);
  });

  it('切换到不同 dataset_id 时清空 currentIndex 避免脏数据', () => {
    useDatasetStore.getState().setCurrentDataset(datasetA);
    useDatasetStore.getState().setCurrentIndex(indexForA);

    useDatasetStore.getState().setCurrentDataset(datasetB);

    const state = useDatasetStore.getState();
    expect(state.currentDataset).toEqual(datasetB);
    expect(state.currentIndex).toBeNull();
  });

  it('setCurrentDataset(null) 同时清空 currentIndex', () => {
    useDatasetStore.getState().setCurrentDataset(datasetA);
    useDatasetStore.getState().setCurrentIndex(indexForA);

    useDatasetStore.getState().setCurrentDataset(null);

    const state = useDatasetStore.getState();
    expect(state.currentDataset).toBeNull();
    expect(state.currentIndex).toBeNull();
  });

  it('clear 一次性重置全部状态', () => {
    useDatasetStore.getState().setCurrentDataset(datasetA);
    useDatasetStore.getState().setCurrentIndex(indexForA);

    useDatasetStore.getState().clear();

    const state = useDatasetStore.getState();
    expect(state.currentDataset).toBeNull();
    expect(state.currentIndex).toBeNull();
  });

  it('persist middleware 把选中状态写入 localStorage', () => {
    useDatasetStore.getState().setCurrentDataset(datasetA);
    useDatasetStore.getState().setCurrentIndex(indexForA);

    const raw = localStorage.getItem('ann_search_dataset');
    expect(raw).not.toBeNull();
    const parsed = JSON.parse(raw as string) as { state: { currentDataset: Dataset | null } };
    expect(parsed.state.currentDataset?.id).toBe(datasetA.id);
  });
});
