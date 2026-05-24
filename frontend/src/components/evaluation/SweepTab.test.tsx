/**
 * SweepTab 组件单测（v1.2 M1.C3+D1 polish）。
 *
 * 覆盖：
 *  1. 初始渲染 + Empty 提示；
 *  2. 触发 sweep 调用 evaluationApi.triggerSweep；
 *  3. sweep 完成后 Plotly 散点 trace 数量正确（5 backend + 帕累托线）；
 *  4. 点击散点回调更新选中点详情；
 *  5. 改 cell_id 输入触发 debounce → searchApi.withParams（ef_search 写入 runtime_params）；
 *  6. previewBackend=brute 时 runtime_params 为空 + ignored_params 警告渲染。
 *
 * PlotlyChart 通过 vi.mock + vi.hoisted holder 替身：暴露最新 onClick 与 data
 * 让测试可主动 invoke onClick 模拟散点击中事件。
 *
 * Antd 5 在 jsdom 下默认 virtual list 因容器尺寸 0 不渲染 option，统一用
 * ``<ConfigProvider virtual={false}>`` 包裹渲染。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import type { ReactElement } from 'react';

// vi.hoisted 提供跨 mock 工厂的共享存储（避免闭包提升问题）
const { plotlyHolder } = vi.hoisted(() => ({
  plotlyHolder: {
    lastOnClick: null as ((e: unknown) => void) | null,
    lastData: null as unknown[] | null,
  },
}));

vi.mock('@/components/PlotlyChart', () => ({
  __esModule: true,
  default: (props: {
    data: unknown[];
    onClick?: (e: unknown) => void;
    height?: number;
    layout?: unknown;
    loading?: boolean;
  }) => {
    plotlyHolder.lastOnClick = props.onClick ?? null;
    const traces = Array.isArray(props.data) ? props.data : [];
    plotlyHolder.lastData = traces;
    return <div data-testid="plotly-chart" data-traces-count={traces.length} />;
  },
}));

vi.mock('@/api/evaluation', () => ({
  evaluationApi: {
    triggerSweep: vi.fn(),
    list: vi.fn(),
    latest: vi.fn(),
    run: vi.fn(),
    searchStats: vi.fn(),
    getSweep: vi.fn(),
    getPareto: vi.fn(),
  },
}));
vi.mock('@/api/search', () => ({
  searchApi: {
    withParams: vi.fn(),
    byId: vi.fn(),
    byVector: vi.fn(),
    byVectorStream: vi.fn(),
    multiDataset: vi.fn(),
    batch: vi.fn(),
    ensemble: vi.fn(),
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
  alignmentApi: {
    align: vi.fn(),
    list: vi.fn(),
    get: vi.fn(),
    remove: vi.fn(),
  },
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

import SweepTab from './SweepTab';
import { evaluationApi } from '@/api/evaluation';
import { searchApi } from '@/api/search';
import { datasetsApi } from '@/api/datasets';
import { indexesApi } from '@/api/indexes';
import type { SweepRun } from '@/types/evaluation';
import type { SearchResponseWithParams } from '@/types/search';
import type { Dataset } from '@/types/dataset';
import type { IndexRecord } from '@/types/indexRecord';

const fakeDataset: Dataset = {
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

const fakeIndexes: IndexRecord[] = [
  {
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
  },
  {
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
  },
];

const buildSweep = (): SweepRun => ({
  id: 99,
  dataset_id: 1,
  created_by: 1,
  status: 'done',
  top_k: 10,
  query_count: 200,
  started_at: '2026-01-01T00:00:00Z',
  finished_at: '2026-01-01T00:01:00Z',
  error: null,
  created_at: '2026-01-01T00:00:00Z',
  pareto_count: 4,
  points: [
    {
      id: 100,
      backend: 'hnswlib',
      params_json: { ef_search: 64 },
      recall: 0.95,
      qps: 1000,
      p50_ms: 0.5,
      p95_ms: 1,
      p99_ms: 1.5,
      mem_mb: 10,
      on_pareto: true,
      created_at: '2026-01-01T00:00:00Z',
    },
    {
      id: 101,
      backend: 'hnswlib',
      params_json: { ef_search: 128 },
      recall: 0.98,
      qps: 800,
      p50_ms: 0.6,
      p95_ms: 1.2,
      p99_ms: 1.8,
      mem_mb: 10,
      on_pareto: true,
      created_at: '2026-01-01T00:00:00Z',
    },
    {
      id: 102,
      backend: 'faiss-hnsw',
      params_json: { ef_search: 64 },
      recall: 0.94,
      qps: 900,
      p50_ms: 0.6,
      p95_ms: 1.1,
      p99_ms: 1.7,
      mem_mb: 11,
      on_pareto: false,
      created_at: '2026-01-01T00:00:00Z',
    },
    {
      id: 103,
      backend: 'faiss-ivfpq',
      params_json: { nprobe: 16 },
      recall: 0.9,
      qps: 1500,
      p50_ms: 0.3,
      p95_ms: 0.8,
      p99_ms: 1.2,
      mem_mb: 5,
      on_pareto: true,
      created_at: '2026-01-01T00:00:00Z',
    },
    {
      id: 104,
      backend: 'adaptive-hnsw',
      params_json: { ef_search: 64 },
      recall: 0.96,
      qps: 950,
      p50_ms: 0.55,
      p95_ms: 1.1,
      p99_ms: 1.6,
      mem_mb: 12,
      on_pareto: false,
      created_at: '2026-01-01T00:00:00Z',
    },
    {
      id: 105,
      backend: 'brute',
      params_json: {},
      recall: 1.0,
      qps: 50,
      p50_ms: 10,
      p95_ms: 15,
      p99_ms: 18,
      mem_mb: 8,
      on_pareto: true,
      created_at: '2026-01-01T00:00:00Z',
    },
  ],
});

const buildSearchResp = (
  overrides: Partial<SearchResponseWithParams> = {},
): SearchResponseWithParams => ({
  dataset_id: 1,
  top_k: 10,
  latency_ms: 12.3,
  index_backend: 'hnswlib',
  metric: 'l2',
  total_candidates: 100,
  hits: [
    {
      rank: 1,
      cell_id: 'c001',
      distance: 0.0,
      meta: { cell_type: 'hepatocyte' },
      source_dataset_id: 1,
    },
  ],
  effective_params: { ef_search: 64 },
  ignored_params: [],
  ...overrides,
});

/** 包 ConfigProvider 关闭 virtual list，避免 jsdom 容器尺寸 0 导致下拉项不渲染 */
const renderTab = (ui: ReactElement) =>
  render(<ConfigProvider virtual={false}>{ui}</ConfigProvider>);

/** 在指定 Form.Item label 内取 antd Select selector 节点 */
const querySelectorInItem = (label: string): HTMLElement => {
  const labelEl = screen.getByText(label);
  const item = labelEl.closest('.ant-form-item') as HTMLElement;
  return item.querySelector('.ant-select-selector') as HTMLElement;
};

/** 通过 antd 内部 class 名定位 portal 中的 option 文本节点，再点击 */
const clickOptionByText = async (text: RegExp | string) => {
  await waitFor(() => {
    const items = document.querySelectorAll('.ant-select-item-option-content');
    expect(items.length).toBeGreaterThan(0);
  });
  const items = Array.from(document.querySelectorAll('.ant-select-item-option-content'));
  const target = items.find((el) => {
    const t = el.textContent ?? '';
    return typeof text === 'string' ? t === text : text.test(t);
  });
  if (!target) {
    throw new Error(
      `option not found, available: ${items.map((i) => i.textContent).join(' | ')}`,
    );
  }
  fireEvent.click(target);
};

/** 打开「扫描 backend」多选并选中指定 option（idempotent：已选中则跳过 click，避免 deselect）。
 *
 * Form initialValues 已注入 SWEEPABLE_BACKENDS 全集，需要先检查目标 option 是否已经
 * 被选中（`.ant-select-item-option-selected`）；只有未选中时才点击切换状态。
 */
const selectBackendOption = async (backendLabel: string) => {
  const selector = querySelectorInItem('扫描 backend');
  fireEvent.mouseDown(selector);
  await waitFor(() => {
    const items = document.querySelectorAll('.ant-select-item-option');
    expect(items.length).toBeGreaterThan(0);
  });
  const items = Array.from(document.querySelectorAll('.ant-select-item-option'));
  const target = items.find((el) => {
    const content = el.querySelector('.ant-select-item-option-content');
    const text = content?.textContent ?? '';
    return new RegExp(`^${backendLabel}\\b`).test(text);
  });
  if (!target) {
    const labels = items.map((i) => i.textContent).join(' | ');
    throw new Error(`backend option "${backendLabel}" not found, available: ${labels}`);
  }
  if (!target.classList.contains('ant-select-item-option-selected')) {
    fireEvent.click(target);
  }
};

describe('SweepTab', () => {
  beforeEach(() => {
    (datasetsApi.list as unknown as ReturnType<typeof vi.fn>).mockResolvedValue([fakeDataset]);
    (indexesApi.listByDataset as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(
      fakeIndexes,
    );
    (evaluationApi.triggerSweep as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(
      buildSweep(),
    );
    (searchApi.withParams as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(
      buildSearchResp(),
    );
    plotlyHolder.lastOnClick = null;
    plotlyHolder.lastData = null;
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('初始渲染显示触发表单 + Empty 提示无 sweep 结果', async () => {
    renderTab(<SweepTab />);
    expect(screen.getByText('参数扫描配置')).toBeTruthy();
    expect(screen.getByRole('button', { name: /跑参数扫描/ })).toBeTruthy();
    expect(screen.getByText(/尚无扫描结果/)).toBeTruthy();
    await waitFor(() => expect(datasetsApi.list).toHaveBeenCalled());
  });

  it('触发 sweep 后调用 evaluationApi.triggerSweep', async () => {
    renderTab(<SweepTab defaultDatasetId={1} />);
    await waitFor(() => expect(indexesApi.listByDataset).toHaveBeenCalledWith(1));

    // 依赖 antd Select.defaultValue 把全部 5 个 backend 注入 Form；不显式 toggle
    fireEvent.click(screen.getByRole('button', { name: /跑参数扫描/ }));

    await waitFor(() => expect(evaluationApi.triggerSweep).toHaveBeenCalledTimes(1));
    const args = (evaluationApi.triggerSweep as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0][0];
    expect(args.dataset_id).toBe(1);
    expect(args.backends).toEqual(
      expect.arrayContaining(['hnswlib', 'faiss-hnsw', 'adaptive-hnsw', 'faiss-ivfpq', 'brute']),
    );
    expect(args.top_k).toBe(10);
    expect(args.query_count).toBe(200);
  });

  it('sweep 完成后 Plotly trace 数量 = 5 backend + 1 帕累托前沿线 = 6', async () => {
    renderTab(<SweepTab defaultDatasetId={1} />);
    await waitFor(() => expect(indexesApi.listByDataset).toHaveBeenCalledWith(1));

    await selectBackendOption('hnswlib');
    fireEvent.click(screen.getByRole('button', { name: /跑参数扫描/ }));

    await waitFor(() => {
      expect(plotlyHolder.lastData).not.toBeNull();
      // 5 个 backend scatter trace + 1 帕累托前沿连线 = 6
      expect(plotlyHolder.lastData!.length).toBe(6);
    });
  });

  it('点击散点回调更新选中点详情 (mock onClick event)', async () => {
    renderTab(<SweepTab defaultDatasetId={1} />);
    await waitFor(() => expect(indexesApi.listByDataset).toHaveBeenCalledWith(1));

    await selectBackendOption('hnswlib');
    fireEvent.click(screen.getByRole('button', { name: /跑参数扫描/ }));

    await waitFor(() => expect(plotlyHolder.lastOnClick).not.toBeNull());

    act(() => {
      plotlyHolder.lastOnClick!({
        points: [{ customdata: 100 }],
      } as unknown);
    });

    // 选中 id=100 的点：backend=hnswlib, recall=0.95 → 显示 "95.00%"
    await waitFor(() => expect(screen.getByText('95.00%')).toBeTruthy());
  });

  it('改 cell_id 后 debounce 200ms 调用 searchApi.withParams（ef_search=64 写入 runtime_params）', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      renderTab(<SweepTab defaultDatasetId={1} />);
      await vi.advanceTimersByTimeAsync(10);
      await waitFor(() => expect(indexesApi.listByDataset).toHaveBeenCalledWith(1));

      const input = screen.getByPlaceholderText(/cell_id/);
      fireEvent.change(input, { target: { value: 'c001' } });

      await act(async () => {
        await vi.advanceTimersByTimeAsync(220);
      });

      await waitFor(() => expect(searchApi.withParams).toHaveBeenCalled());
      const payload = (searchApi.withParams as unknown as ReturnType<typeof vi.fn>).mock
        .calls[0][0];
      expect(payload.runtime_params).toEqual({ ef_search: 64 });
      expect(payload.cell_id).toBe('c001');
      expect(payload.dataset_id).toBe(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it('previewBackend=brute 时 runtime_params 为空 + ignored_params 显示警告', async () => {
    (searchApi.withParams as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(
      buildSearchResp({ effective_params: {}, ignored_params: ['ef_search'] }),
    );

    renderTab(<SweepTab defaultDatasetId={1} />);
    await waitFor(() => expect(indexesApi.listByDataset).toHaveBeenCalledWith(1));

    // 切换「目标 backend」Select 到 brute（D1 仪表盘卡片）
    const targetSel = querySelectorInItem('目标 backend');
    fireEvent.mouseDown(targetSel);
    await clickOptionByText('brute');

    const input = screen.getByPlaceholderText(/cell_id/);
    fireEvent.change(input, { target: { value: 'c001' } });

    await waitFor(() => expect(searchApi.withParams).toHaveBeenCalled(), { timeout: 3000 });
    const payload = (searchApi.withParams as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0][0];
    expect(payload.runtime_params).toEqual({});

    await waitFor(() => expect(screen.getByText(/被忽略的参数/)).toBeTruthy());
  });
});
