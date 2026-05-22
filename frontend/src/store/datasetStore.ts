import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import type { Dataset } from '@/types/dataset';
import type { IndexRecord } from '@/types/indexRecord';

interface DatasetState {
  currentDataset: Dataset | null;
  currentIndex: IndexRecord | null;
  setCurrentDataset: (dataset: Dataset | null) => void;
  setCurrentIndex: (index: IndexRecord | null) => void;
  clear: () => void;
}

// 当前选中数据集 / 索引状态：在跨页面切换时保持选择
export const useDatasetStore = create<DatasetState>()(
  persist(
    (set) => ({
      currentDataset: null,
      currentIndex: null,
      setCurrentDataset: (dataset) =>
        set((state) => ({
          currentDataset: dataset,
          currentIndex:
            state.currentIndex && dataset && state.currentIndex.dataset_id === dataset.id
              ? state.currentIndex
              : null,
        })),
      setCurrentIndex: (index) => set({ currentIndex: index }),
      clear: () => set({ currentDataset: null, currentIndex: null }),
    }),
    {
      name: 'ann_search_dataset',
      storage: createJSONStorage(() => localStorage),
    },
  ),
);
