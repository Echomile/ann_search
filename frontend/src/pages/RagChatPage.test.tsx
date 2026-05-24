/**
 * RagChatPage 组件单测（v1.2 M3.D4 Agent UI 重构 polish）。
 *
 * 覆盖：
 *  1. 初始渲染：标题 / TextArea / 发送按钮 / Empty 提示；
 *  2. 输入并发送后调用 ragApi.chatQuery（query + session_id=null + dataset_id +
 *     max_iterations 默认 5）；
 *  3. mock 回复携带 citations 时渲染 AI 答案气泡 + 引用 Collapse；
 *  4. 多轮对话保留历史 + tool_trace 渲染（工具链路 Collapse + tool 名称 tag）。
 *
 * antd Select 通过 ConfigProvider virtual={false} 关闭虚拟列表，jsdom 下
 * dataset 下拉可正确渲染。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ConfigProvider } from 'antd';

vi.mock('@/api/rag', () => ({
  ragApi: {
    chatQuery: vi.fn(),
    listSessions: vi.fn(),
    getSession: vi.fn(),
    deleteSession: vi.fn(),
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

import RagChatPage from './RagChatPage';
import { ragApi } from '@/api/rag';
import { datasetsApi } from '@/api/datasets';
import { useDatasetStore } from '@/store/datasetStore';
import type { RagChatResponse } from '@/types/rag';
import type { Dataset } from '@/types/dataset';

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

const buildResp = (overrides: Partial<RagChatResponse> = {}): RagChatResponse => ({
  session_id: 1,
  answer: 'OK',
  tool_trace: [],
  citations: [],
  iterations: 1,
  finish_reason: 'stop',
  query_time_ms: 100,
  ...overrides,
});

const renderPage = () =>
  render(
    <ConfigProvider virtual={false}>
      <RagChatPage />
    </ConfigProvider>,
  );

/** 在 TextArea 输入并点击「发送」按钮 */
const sendInput = (text: string) => {
  const textarea = screen.getByPlaceholderText(/自然语言提问/);
  fireEvent.change(textarea, { target: { value: text } });
  fireEvent.click(screen.getByRole('button', { name: /发送/ }));
};

describe('RagChatPage', () => {
  beforeEach(() => {
    localStorage.clear();
    useDatasetStore.setState({ currentDataset: null, currentIndex: null });
    (datasetsApi.list as unknown as ReturnType<typeof vi.fn>).mockResolvedValue([ds]);
    (ragApi.listSessions as unknown as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (ragApi.chatQuery as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(buildResp());
  });

  afterEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it('初始渲染显示对话气泡区 + 输入框 + 发送按钮', async () => {
    renderPage();
    expect(screen.getByText('RAG 自然语言查询')).toBeTruthy();
    expect(screen.getByPlaceholderText(/自然语言提问/)).toBeTruthy();
    expect(screen.getByRole('button', { name: /发送/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /新建会话/ })).toBeTruthy();
    expect(screen.getByText(/还没有对话/)).toBeTruthy();
    await waitFor(() => {
      expect(datasetsApi.list).toHaveBeenCalled();
      expect(ragApi.listSessions).toHaveBeenCalled();
    });
  });

  it('输入 cell_id 查询发送后调用 ragApi.chatQuery', async () => {
    renderPage();
    await waitFor(() => expect(datasetsApi.list).toHaveBeenCalled());
    // 等组件把 datasetId 默认设为 ready[0].id=1
    await waitFor(() => {
      expect(
        document.querySelector('.ant-select-selection-item[title*="liver-10k"]'),
      ).not.toBeNull();
    });

    sendInput('找和 cell_id=AAACATAC 相似的细胞');

    await waitFor(() => expect(ragApi.chatQuery).toHaveBeenCalledTimes(1));
    const payload = (ragApi.chatQuery as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(payload.query).toBe('找和 cell_id=AAACATAC 相似的细胞');
    expect(payload.session_id).toBeNull();
    expect(payload.dataset_id).toBe(ds.id);
    expect(payload.max_iterations).toBe(5);
  });

  it('mock 回复显示 AI 气泡 + 引用面板', async () => {
    (ragApi.chatQuery as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(
      buildResp({
        answer: '找到 2 个相似细胞',
        citations: [
          { cell_id: 'c1', dataset_id: 1 },
          { cell_id: 'c2', dataset_id: 1 },
        ],
      }),
    );

    renderPage();
    await waitFor(() => expect(datasetsApi.list).toHaveBeenCalled());

    sendInput('找类似细胞');
    await waitFor(() => expect(screen.getByText('找到 2 个相似细胞')).toBeTruthy());

    // 引用 Collapse Header 渲染 + 数量 Tag「2」
    expect(screen.getByText('引用')).toBeTruthy();

    // 展开「引用」panel 查看 cell_id Tag（antd Collapse 默认折叠时 children 不渲染）
    fireEvent.click(screen.getByText('引用'));
    await waitFor(() => {
      const tags = Array.from(document.querySelectorAll('.ant-tag-geekblue'));
      const texts = tags.map((t) => (t.textContent ?? '').replace(/\s+/g, ' ').trim());
      expect(texts).toContain('c1 @ ds#1');
      expect(texts).toContain('c2 @ ds#1');
    });
  });

  it('多轮对话保留历史 + tool_trace 展示', async () => {
    (ragApi.chatQuery as unknown as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce(
        buildResp({
          answer: '共有 3 个数据集',
          tool_trace: [
            { name: 'list_datasets', arguments: {}, summary: 'datasets=3', ok: true },
          ],
        }),
      )
      .mockResolvedValueOnce(
        buildResp({
          session_id: 1,
          answer: '相似度 0.95',
        }),
      );

    renderPage();
    await waitFor(() => expect(datasetsApi.list).toHaveBeenCalled());

    sendInput('list datasets');
    await waitFor(() => expect(screen.getByText('共有 3 个数据集')).toBeTruthy());
    // tool_trace 渲染：Collapse header 显示「工具链路」
    expect(screen.getByText('工具链路')).toBeTruthy();
    // 展开「工具链路」panel 查看 tool 调用详情：通过 .ant-collapse-header 容器点击更稳
    const toolHeader = screen
      .getByText('工具链路')
      .closest('.ant-collapse-header') as HTMLElement;
    fireEvent.click(toolHeader);
    await waitFor(() => {
      // list_datasets 同名文本同时出现在介绍卡 <Text code> 与 tool_trace Tag 中，
      // 这里用 .ant-tag-cyan（ok=true 时的 Tag 颜色）精确定位工具链路的 list_datasets tag
      const cyanTags = Array.from(document.querySelectorAll('.ant-tag-cyan')).map(
        (t) => t.textContent ?? '',
      );
      expect(cyanTags).toContain('list_datasets');
      expect(screen.getByText(/datasets=3/)).toBeTruthy();
    });

    sendInput('再看 c001');
    await waitFor(() => expect(screen.getByText('相似度 0.95')).toBeTruthy());

    // 第二轮：两次 user 输入气泡 + 两次 AI 回答均在 DOM 内
    expect(screen.getByText('list datasets')).toBeTruthy();
    expect(screen.getByText('再看 c001')).toBeTruthy();
    expect(screen.getByText('共有 3 个数据集')).toBeTruthy();
    expect(screen.getByText('相似度 0.95')).toBeTruthy();

    // 第二次发送时 session_id 应携带前一轮返回的 1
    expect(ragApi.chatQuery).toHaveBeenCalledTimes(2);
    const secondPayload = (ragApi.chatQuery as unknown as ReturnType<typeof vi.fn>).mock
      .calls[1][0];
    expect(secondPayload.session_id).toBe(1);
  });
});
