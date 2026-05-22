import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import type { Dataset } from '@/types/dataset';

interface DatasetState {
  currentDataset: Dataset | null;
  setCurrentDataset: (dataset: Dataset | null) => void;
  clear: () => void;
}

// 当前选中数据集状态：在切换页面时保持选择
export const useDatasetStore = create<DatasetState>()(
  persist(
    (set) => ({
      currentDataset: null,
      setCurrentDataset: (dataset) => set({ currentDataset: dataset }),
      clear: () => set({ currentDataset: null }),
    }),
    {
      name: 'ann_search_dataset',
      storage: createJSONStorage(() => localStorage),
    },
  ),
);
