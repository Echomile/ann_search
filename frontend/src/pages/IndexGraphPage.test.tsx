/**
 * IndexGraphPage 组件单测（v1.2 M2.D2 polish）。
 *
 * 覆盖：
 *  1. 初始渲染：表单标题 + 数据集/索引/cell_id 三个 Form.Item + Empty 占位；
 *  2. 路由含 :id 时，hnswlib 索引自动 preset 并能触发 getSubgraph；
 *  3. fetchIndexesFor 过滤掉非 HNSW 后端，brute 索引不出现在下拉中；
 *  4. subgraph 拉取成功后 Plotly trace 数量 = 1 edge trace + N depth-group traces。
 *
 * PlotlyChart 通过 vi.mock 替身暴露 data；ConfigProvider virtual={false} 关
 * 闭虚拟列表，确保 jsdom 下 Select 下拉的 option 能渲染出来。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

const { plotlyHolder } = vi.hoisted(() => ({
  plotlyHolder: { lastData: null as unknown[] | null },
}));

/**
 * Mock antd：在 Form.useForm 中把创建的 form 实例暴露到
 * ``(window as { __lastForm }).__lastForm``，绕过 antd Select tags +
 * maxCount=1 在 jsdom 下 cell_id 无法 commit 的限制，测试中直接
 * ``form.setFieldsValue({ cell_id: 'c001' })`` 注入值。
 *
 * antd Form 本身是 forwardRef 组件，不能当普通函数重新包装；这里直接
 * mutate ``actual.Form.useForm`` 静态方法，return actual 即可（vitest 测试
 * 进程内 mutate 不会泄漏到生产代码）。
 */
vi.mock('antd', async () => {
  const actual: typeof import('antd') = await vi.importActual('antd');
  const originalUseForm = actual.Form.useForm;
  (actual.Form as { useForm: typeof originalUseForm }).useForm = ((
    init?: Parameters<typeof originalUseForm>[0],
  ) => {
    const result = originalUseForm(init);
    if (typeof window !== 'undefined') {
      (window as unknown as { __lastForm: unknown }).__lastForm = result[0];
    }
    return result;
  }) as typeof originalUseForm;
  return actual;
});

import { ConfigProvider } from 'antd';

vi.mock('@/components/PlotlyChart', () => ({
  __esModule: true,
  default: (props: { data: unknown[]; height?: number; layout?: unknown }) => {
    const traces = Array.isArray(props.data) ? props.data : [];
    plotlyHolder.lastData = traces;
    return <div data-testid="plotly-chart" data-traces-count={traces.length} />;
  },
}));

vi.mock('@/api/datasets', () => ({
  datasetsApi: {
    list: vi.fn(),
    get: vi.fn(),
    upload: vi.fn(),
    remove: vi.fn(),
    status: vi.fn(),
    uploadProgress: vi.fn(),
    cleanupOrphan: vi.fn(),
    umap: vi.fn(),
  },
  alignmentApi: { align: vi.fn(), list: vi.fn(), get: vi.fn(), remove: vi.fn() },
}));
vi.mock('@/api/indexes', () => ({
  indexesApi: {
    listByDataset: vi.fn(),
    create: vi.fn(),
    get: vi.fn(),
    status: vi.fn(),
    remove: vi.fn(),
    getSubgraph: vi.fn(),
  },
}));

import IndexGraphPage from './IndexGraphPage';
import { datasetsApi } from '@/api/datasets';
import { indexesApi } from '@/api/indexes';
import type { Dataset } from '@/types/dataset';
import type { IndexRecord } from '@/types/indexRecord';
import type { SubgraphResponse } from '@/types/subgraph';

const ds: Dataset = {
  id: 1,
  owner_id: 1,
  name: 'liver-10k',
  status: 'ready',
  cell_count: 10000,
  vector_dim: 50,
  vector_source: 'X_pca',
  meta_columns: ['cell_type'],
  created_at: '2026-01-01T00:00:00Z',
};

const hnswIdx: IndexRecord = {
  id: 11,
  dataset_id: 1,
  backend: 'hnswlib',
  metric: 'l2',
  params: { M: 16 },
  index_path: '/tmp/a',
  build_time_seconds: 1,
  memory_mb: 2,
  status: 'ready',
  created_at: '2026-01-01T00:00:00Z',
};

const bruteIdx: IndexRecord = {
  id: 12,
  dataset_id: 1,
  backend: 'brute',
  metric: 'l2',
  params: {},
  index_path: '/tmp/b',
  build_time_seconds: 1,
  memory_mb: 2,
  status: 'ready',
  created_at: '2026-01-01T00:00:00Z',
};

const subgraph: SubgraphResponse = {
  nodes: [
    { label: 1, cell_id: 'c001', depth: 0, is_entry: true, is_topk: true, cell_type: 'A' },
    { label: 2, cell_id: 'c002', depth: 1, is_entry: false, is_topk: false, cell_type: 'B' },
    { label: 3, cell_id: 'c003', depth: 1, is_entry: false, is_topk: false, cell_type: 'B' },
    { label: 4, cell_id: 'c004', depth: 2, is_entry: false, is_topk: false, cell_type: 'C' },
  ],
  edges: [
    { src: 1, dst: 2 },
    { src: 1, dst: 3 },
    { src: 2, dst: 4 },
  ],
  entry_label: 1,
  entry_cell_id: 'c001',
  layer: 0,
  depth: 2,
  truncated: false,
  backend: 'hnswlib',
};

const renderPage = (path = '/indexes/0/graph') =>
  render(
    <ConfigProvider virtual={false}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/indexes/:id/graph" element={<IndexGraphPage />} />
        </Routes>
      </MemoryRouter>
    </ConfigProvider>,
  );

/**
 * 直接通过 mock 暴露的 form 实例 setFieldsValue(cell_id)。
 *
 * antd Select tags + maxCount=1 在 jsdom 下 form initialValue '' 会占位，
 * 用户级 fireEvent 路径（input/change/keyDown Enter）均无法在 React 18 中
 * 触发 rc-select 内部 commit；最稳的做法是直接通过 antd form 实例 imperative
 * 写入，等价于用户输入完成后的最终 form state。
 */
const submitCellIdTag = (value: string) => {
  const form = (window as unknown as { __lastForm?: { setFieldsValue: (v: Record<string, unknown>) => void } })
    .__lastForm;
  if (!form) throw new Error('antd Form 实例未注入 window，检查 vi.mock(\'antd\') 是否生效');
  form.setFieldsValue({ cell_id: value });
};

describe('IndexGraphPage', () => {
  beforeEach(() => {
    (datasetsApi.list as unknown as ReturnType<typeof vi.fn>).mockResolvedValue([ds]);
    (indexesApi.listByDataset as unknown as ReturnType<typeof vi.fn>).mockResolvedValue([
      hnswIdx,
    ]);
    (indexesApi.get as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(hnswIdx);
    (indexesApi.getSubgraph as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(subgraph);
    plotlyHolder.lastData = null;
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('初始渲染显示数据集 + 索引选择表单', async () => {
    renderPage();
    expect(screen.getByText('HNSW 邻居图谱')).toBeTruthy();
    expect(screen.getByText('数据集')).toBeTruthy();
    expect(screen.getByText('索引 (仅 HNSW 系)')).toBeTruthy();
    expect(screen.getByText('Entry cell_id')).toBeTruthy();
    expect(screen.getByRole('button', { name: /生成邻居图/ })).toBeTruthy();
    // 未提交前显示 Empty
    expect(screen.getByText(/提交表单生成子图/)).toBeTruthy();
    await waitFor(() => expect(datasetsApi.list).toHaveBeenCalled());
  });

  it('路由 :id=11 (hnswlib) 自动 preset 并提交后调用 getSubgraph', async () => {
    renderPage('/indexes/11/graph');
    await waitFor(() => {
      expect(indexesApi.get).toHaveBeenCalledWith(11);
      expect(indexesApi.listByDataset).toHaveBeenCalledWith(1);
    });

    // 等 form.setFieldsValue 后 dataset_id Select 显示对应名称
    await waitFor(() => {
      const datasetItem = screen.getByText('数据集').closest('.ant-form-item') as HTMLElement;
      const selectionItem = datasetItem.querySelector('.ant-select-selection-item');
      expect(selectionItem?.textContent).toContain(ds.name);
    });

    submitCellIdTag('c001');
    fireEvent.click(screen.getByRole('button', { name: /生成邻居图/ }));

    await waitFor(() => expect(indexesApi.getSubgraph).toHaveBeenCalled(), { timeout: 2000 });
    const [calledId, query] = (indexesApi.getSubgraph as unknown as ReturnType<typeof vi.fn>)
      .mock.calls[0];
    expect(calledId).toBe(11);
    expect(query.cell_id).toBe('c001');
    expect(query.depth).toBe(2); // DEFAULT_FORM.depth
    expect(query.layer).toBe(0);
    expect(query.max_nodes).toBe(200);
  });

  it('brute 后端被 fetchIndexesFor 过滤，不出现在索引下拉中', async () => {
    (indexesApi.listByDataset as unknown as ReturnType<typeof vi.fn>).mockResolvedValue([
      hnswIdx,
      bruteIdx,
    ]);
    renderPage('/indexes/11/graph');
    await waitFor(() => expect(indexesApi.listByDataset).toHaveBeenCalledWith(1));

    // 打开「索引 (仅 HNSW 系)」下拉
    const idxItem = screen
      .getByText('索引 (仅 HNSW 系)')
      .closest('.ant-form-item') as HTMLElement;
    const idxSel = idxItem.querySelector('.ant-select-selector') as HTMLElement;
    fireEvent.mouseDown(idxSel);

    await waitFor(() => {
      const items = document.querySelectorAll('.ant-select-item-option-content');
      expect(items.length).toBeGreaterThan(0);
    });
    const optionTexts = Array.from(
      document.querySelectorAll('.ant-select-item-option-content'),
    ).map((e) => e.textContent ?? '');
    // 仅 hnswlib 索引应出现，brute 应被过滤掉
    expect(optionTexts.some((o) => o.includes('hnswlib'))).toBe(true);
    expect(optionTexts.every((o) => !o.includes('brute'))).toBe(true);
  });

  it('subgraph 拉取成功后 Plotly trace 数量正确（1 edge + 3 depth-group node trace）', async () => {
    renderPage('/indexes/11/graph');
    await waitFor(() => expect(indexesApi.listByDataset).toHaveBeenCalledWith(1));
    await waitFor(() => {
      const datasetItem = screen.getByText('数据集').closest('.ant-form-item') as HTMLElement;
      const selectionItem = datasetItem.querySelector('.ant-select-selection-item');
      expect(selectionItem?.textContent).toContain(ds.name);
    });

    submitCellIdTag('c001');
    fireEvent.click(screen.getByRole('button', { name: /生成邻居图/ }));

    await waitFor(() => expect(indexesApi.getSubgraph).toHaveBeenCalled());

    // subgraph.nodes depth 集合 = {0, 1, 2} → 3 个 node trace
    // 再加 1 个 edge trace → 总 4
    await waitFor(() => {
      expect(plotlyHolder.lastData).not.toBeNull();
      expect(plotlyHolder.lastData!.length).toBe(4);
    });
  });
});
