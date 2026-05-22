// RAG 对话页面：自然语言查询 → LLM 解析 → ANN 检索 → LLM 总结
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties, KeyboardEvent } from 'react';
import {
  Avatar,
  Button,
  Card,
  Collapse,
  Empty,
  Form,
  Input,
  InputNumber,
  List,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { RobotOutlined, SendOutlined, UserOutlined } from '@ant-design/icons';
import axios from 'axios';
import { datasetsApi } from '@/api/datasets';
import { ragApi } from '@/api/rag';
import type { Dataset } from '@/types/dataset';
import type { RagHit, RagResponse } from '@/types/rag';
import { useDatasetStore } from '@/store/datasetStore';
import { formatDuration } from '@/utils/format';

const { Title, Paragraph, Text } = Typography;

/** 单条对话条目：包含用户问题与 AI 回答的全部上下文 */
interface ChatEntry {
  id: string;
  query: string;
  loading: boolean;
  response?: RagResponse;
  error?: string;
}

const extractError = (err: unknown): string => {
  if (axios.isAxiosError(err)) {
    const detail = err.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    return err.message;
  }
  if (err instanceof Error) return err.message;
  return '未知错误';
};

const formatMetaValue = (v: unknown): string => {
  if (v === null || v === undefined) return '-';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
};

const renderMetadataTags = (meta: Record<string, unknown> | null | undefined) => {
  const entries = Object.entries(meta ?? {});
  if (entries.length === 0) return <Text type="secondary">-</Text>;
  return (
    <Space size={[4, 4]} wrap>
      {entries.map(([k, v]) => (
        <Tag key={k} color="geekblue">{`${k}: ${formatMetaValue(v)}`}</Tag>
      ))}
    </Space>
  );
};

const hitColumns: ColumnsType<RagHit> = [
  { title: 'Rank', dataIndex: 'rank', key: 'rank', width: 70 },
  { title: 'Cell ID', dataIndex: 'cell_id', key: 'cell_id', width: 220 },
  {
    title: 'Distance',
    dataIndex: 'distance',
    key: 'distance',
    width: 130,
    render: (v: number) => v.toFixed(6),
  },
  {
    title: 'Metadata',
    dataIndex: 'metadata',
    key: 'metadata',
    render: (m: Record<string, unknown>) => renderMetadataTags(m),
  },
];

const userBubbleStyle: CSSProperties = {
  background: '#f5f5f5',
  padding: '10px 14px',
  borderRadius: 12,
  maxWidth: '78%',
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
  boxShadow: '0 1px 2px rgba(0, 0, 0, 0.04)',
};

const aiBubbleStyle: CSSProperties = {
  background: '#e6f4ff',
  padding: '12px 16px',
  borderRadius: 12,
  flex: 1,
  maxWidth: 'calc(100% - 56px)',
  boxShadow: '0 1px 2px rgba(0, 0, 0, 0.04)',
};

const RagChatPage = () => {
  const currentDataset = useDatasetStore((s) => s.currentDataset);

  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [datasetId, setDatasetId] = useState<number | undefined>(currentDataset?.id);
  const [topK, setTopK] = useState<number>(10);
  const [input, setInput] = useState('');
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // 拉取数据集列表，只保留 ready 状态的可查询数据集
  const loadDatasets = useCallback(async () => {
    try {
      const list = await datasetsApi.list();
      const ready = list.filter((d) => d.status === 'ready');
      setDatasets(ready);
      setDatasetId((prev) => {
        if (prev && ready.some((d) => d.id === prev)) return prev;
        if (currentDataset && ready.some((d) => d.id === currentDataset.id)) {
          return currentDataset.id;
        }
        return ready.length > 0 ? ready[0].id : undefined;
      });
    } catch (err) {
      message.error(extractError(err));
    }
  }, [currentDataset]);

  useEffect(() => {
    void loadDatasets();
  }, [loadDatasets]);

  // 每次对话列表变化时自动滚到底部
  useEffect(() => {
    const el = scrollRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [entries]);

  const datasetOptions = useMemo(
    () => datasets.map((d) => ({ label: `${d.name} (#${d.id})`, value: d.id })),
    [datasets],
  );

  const handleSubmit = async () => {
    const trimmed = input.trim();
    if (!trimmed) {
      message.warning('请输入查询内容');
      return;
    }
    if (datasetId === undefined) {
      message.warning('请先选择一个 ready 状态的数据集');
      return;
    }
    const id = `${Date.now()}-${Math.random().toString(16).slice(2, 6)}`;
    setEntries((prev) => [...prev, { id, query: trimmed, loading: true }]);
    setInput('');
    setSubmitting(true);
    try {
      const resp = await ragApi.query({
        dataset_id: datasetId,
        top_k: topK,
        query: trimmed,
      });
      setEntries((prev) =>
        prev.map((e) => (e.id === id ? { ...e, loading: false, response: resp } : e)),
      );
    } catch (err) {
      const msg = extractError(err);
      message.error(msg);
      setEntries((prev) =>
        prev.map((e) => (e.id === id ? { ...e, loading: false, error: msg } : e)),
      );
    } finally {
      setSubmitting(false);
    }
  };

  // Enter 提交，Shift+Enter 换行；输入法组合状态下不拦截
  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void handleSubmit();
    }
  };

  const renderAiBubble = (entry: ChatEntry) => {
    if (entry.loading) {
      return (
        <div style={aiBubbleStyle}>
          <Space>
            <Spin size="small" />
            <Text type="secondary">正在解析查询并执行向量检索...</Text>
          </Space>
        </div>
      );
    }
    if (entry.error) {
      return (
        <div style={aiBubbleStyle}>
          <Text type="danger">请求失败：{entry.error}</Text>
        </div>
      );
    }
    const resp = entry.response;
    if (!resp) return null;
    const filterEntries = Object.entries(resp.parsed.filters ?? {});

    return (
      <div style={aiBubbleStyle}>
        <Paragraph style={{ marginBottom: 8, whiteSpace: 'pre-wrap' }}>
          {resp.answer || '（无回答）'}
        </Paragraph>
        <Text type="secondary" style={{ fontSize: 12 }}>
          意图：{resp.parsed.intent} · 命中：{resp.hits.length} · 耗时：
          {formatDuration(resp.query_time_ms)}
        </Text>

        <Collapse
          size="small"
          ghost
          style={{ marginTop: 8 }}
          items={[
            {
              key: 'parsed',
              label: '查询解析详情',
              children: (
                <Space direction="vertical" size={4} style={{ width: '100%' }}>
                  <div>
                    <Text>意图：</Text>
                    <Tag color="purple">{resp.parsed.intent}</Tag>
                  </div>
                  <div>
                    <Text>cell_id：</Text>
                    {resp.parsed.cell_id ? (
                      <Tag color="blue">{resp.parsed.cell_id}</Tag>
                    ) : (
                      <Text type="secondary">（无）</Text>
                    )}
                  </div>
                  <div>
                    <Text>top_k：</Text>
                    <Tag>{resp.parsed.top_k}</Tag>
                  </div>
                  <div>
                    <Text>过滤条件：</Text>
                    {filterEntries.length === 0 ? (
                      <Text type="secondary">（无）</Text>
                    ) : (
                      <Space size={[4, 4]} wrap>
                        {filterEntries.map(([k, v]) => (
                          <Tag key={k} color="cyan">{`${k}: ${JSON.stringify(v)}`}</Tag>
                        ))}
                      </Space>
                    )}
                  </div>
                </Space>
              ),
            },
          ]}
        />

        {resp.hits.length > 0 && (
          <Table<RagHit>
            rowKey={(r) => `${r.cell_id}-${r.rank}`}
            dataSource={resp.hits}
            columns={hitColumns}
            size="small"
            pagination={false}
            scroll={{ x: 'max-content' }}
            style={{ marginTop: 12, background: '#fff', borderRadius: 8 }}
          />
        )}
      </div>
    );
  };

  return (
    <div>
      <Title level={3}>RAG 自然语言查询</Title>

      <Card size="small" style={{ marginBottom: 16 }}>
        <Paragraph style={{ marginBottom: 0 }}>
          RAG（Retrieval-Augmented Generation）流程：
          <Text strong> 自然语言查询 </Text>→<Text strong> LLM 解析 </Text>→
          <Text strong> ANN 向量检索 </Text>→<Text strong> 自然语言总结</Text>。
          示例：&ldquo;找出 T cell 中和 AAACATAC-1 最相似的 5 个细胞&rdquo;。
        </Paragraph>
      </Card>

      <Card size="small" style={{ marginBottom: 16 }}>
        <Form layout="inline">
          <Form.Item label="数据集" style={{ marginBottom: 0 }}>
            <Select
              value={datasetId}
              onChange={(v) => setDatasetId(v)}
              options={datasetOptions}
              placeholder="选择 ready 状态的数据集"
              showSearch
              optionFilterProp="label"
              style={{ width: 280 }}
            />
          </Form.Item>
          <Form.Item label="Top-K" style={{ marginBottom: 0 }}>
            <InputNumber
              min={1}
              max={1000}
              value={topK}
              onChange={(v) => setTopK(v ?? 10)}
              style={{ width: 120 }}
            />
          </Form.Item>
        </Form>
      </Card>

      <Card size="small" style={{ marginBottom: 16 }}>
        <div ref={scrollRef} style={{ maxHeight: 560, overflowY: 'auto', padding: '4px 8px' }}>
          {entries.length === 0 ? (
            <Empty description="还没有对话，输入问题开始 RAG 查询" />
          ) : (
            <List
              dataSource={entries}
              split={false}
              renderItem={(entry) => (
                <List.Item
                  key={entry.id}
                  style={{ display: 'block', padding: '8px 0', border: 0 }}
                >
                  <div
                    style={{
                      display: 'flex',
                      justifyContent: 'flex-end',
                      alignItems: 'flex-start',
                      gap: 8,
                      marginBottom: 10,
                    }}
                  >
                    <div style={userBubbleStyle}>{entry.query}</div>
                    <Avatar
                      icon={<UserOutlined />}
                      style={{ backgroundColor: '#1677ff', flexShrink: 0 }}
                    />
                  </div>
                  <div
                    style={{
                      display: 'flex',
                      justifyContent: 'flex-start',
                      alignItems: 'flex-start',
                      gap: 8,
                    }}
                  >
                    <Avatar
                      icon={<RobotOutlined />}
                      style={{ backgroundColor: '#52c41a', flexShrink: 0 }}
                    />
                    {renderAiBubble(entry)}
                  </div>
                </List.Item>
              )}
            />
          )}
        </div>
      </Card>

      <Card size="small">
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
          <Input.TextArea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="输入自然语言查询，Enter 发送，Shift+Enter 换行"
            autoSize={{ minRows: 1, maxRows: 4 }}
            disabled={submitting}
            style={{ flex: 1, resize: 'none' }}
          />
          <Button
            type="primary"
            icon={<SendOutlined />}
            loading={submitting}
            onClick={() => void handleSubmit()}
          >
            发送
          </Button>
        </div>
      </Card>
    </div>
  );
};

export default RagChatPage;
