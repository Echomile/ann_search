// RAG 对话页面（v1.2 D4）: LLM Function Calling Agent 多轮聊天
//
// 布局：
//   - 左侧 Sider：会话列表 + 新建会话按钮；
//   - 中央：ChatGPT 风格气泡（用户右侧蓝色，AI 左侧灰色）；
//   - AI 调用工具时显示 “正在调用 list_datasets...” 状态条；
//   - 每条 AI 回答下方有「工具链路」+「引用」折叠面板；
//   - 底部：单行 / 多行输入框（Enter 发送，Shift+Enter 换行）。

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
  Layout,
  List,
  Popconfirm,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
  message,
} from 'antd';
import {
  DeleteOutlined,
  MessageOutlined,
  PlusOutlined,
  RobotOutlined,
  SendOutlined,
  ToolOutlined,
  UserOutlined,
} from '@ant-design/icons';
import { datasetsApi } from '@/api/datasets';
import { ragApi } from '@/api/rag';
import type { Dataset } from '@/types/dataset';
import type {
  RagChatResponse,
  RagMessage,
  RagSession,
  ToolCall,
  ToolTraceItem,
} from '@/types/rag';
import { useDatasetStore } from '@/store/datasetStore';
import { formatDuration } from '@/utils/format';
import { extractError } from '@/utils/error';

const { Title, Paragraph, Text } = Typography;
const { Sider, Content } = Layout;

/** 一个 “消息泡” 的内部表示：兼容刚回包的临时 entry 与从 history 拉取的 RagMessage。 */
interface ChatBubble {
  key: string;
  role: 'user' | 'assistant' | 'tool';
  content: string;
  /** 若为 assistant 且本轮含 tool_calls，则提供链路供折叠面板展示 */
  toolTrace?: ToolTraceItem[];
  /** 引用 cell_id 列表，仅在 assistant 最终回答时携带 */
  citations?: Array<{ cell_id: string; dataset_id: number | null }>;
  loading?: boolean;
  error?: string;
}

const userBubbleStyle: CSSProperties = {
  background: '#1677ff',
  color: '#fff',
  padding: '10px 14px',
  borderRadius: 12,
  maxWidth: '78%',
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
  boxShadow: '0 1px 2px rgba(0, 0, 0, 0.06)',
};

const aiBubbleStyle: CSSProperties = {
  background: '#f5f5f5',
  padding: '12px 16px',
  borderRadius: 12,
  flex: 1,
  maxWidth: 'calc(100% - 56px)',
  boxShadow: '0 1px 2px rgba(0, 0, 0, 0.04)',
};

/** 解析后端 RagMessage 数组为前端 ChatBubble 列表（合并 assistant.tool_calls + tool 结果）。 */
const bubblesFromMessages = (messages: RagMessage[]): ChatBubble[] => {
  const out: ChatBubble[] = [];
  for (let i = 0; i < messages.length; i++) {
    const m = messages[i];
    if (m.role === 'user') {
      out.push({ key: `m-${m.id}`, role: 'user', content: m.content ?? '' });
    } else if (m.role === 'assistant') {
      // assistant 可能 a) 仅文本 stop 回答；b) tool_calls 决策（content 多为空）。
      // 我们只对 stop 阶段渲染气泡；tool_calls 阶段并入下一条 assistant 的 toolTrace。
      if (m.tool_calls && m.tool_calls.length > 0) {
        // skip - 由下面的 tool message 与最终 assistant 共同贡献
        continue;
      }
      // 收集本轮 tool_trace：往前扫直至上一个 user 消息
      const trace: ToolTraceItem[] = [];
      for (let j = i - 1; j >= 0; j--) {
        const prev = messages[j];
        if (prev.role === 'user') break;
        if (prev.role === 'tool') {
          for (const r of prev.tool_results ?? []) {
            const result = (r.result ?? {}) as Record<string, unknown>;
            const name = (r.name as string) ?? '';
            const ok = !('error' in result && result['error']);
            let summary = 'ok';
            if (name === 'list_datasets') {
              const ds = (result['datasets'] as unknown[]) ?? [];
              summary = `datasets=${ds.length}`;
            } else if (name === 'search_by_cell_id' || name === 'search_by_vector') {
              const hits = (result['hits'] as unknown[]) ?? [];
              summary = `hits=${hits.length}`;
            } else if (name === 'filter_cells') {
              summary = `matched=${result['matched_count'] ?? 0}`;
            } else if (typeof result['error'] === 'string') {
              summary = `error: ${result['error']}`;
            }
            trace.unshift({
              name,
              arguments: {},
              summary,
              ok,
            });
          }
        }
      }
      out.push({
        key: `m-${m.id}`,
        role: 'assistant',
        content: m.content ?? '',
        toolTrace: trace.length > 0 ? trace : undefined,
      });
    }
    // tool 消息不直接显示气泡，已合并入 assistant.toolTrace
  }
  return out;
};

const RagChatPage = () => {
  const currentDataset = useDatasetStore((s) => s.currentDataset);

  // 数据集 / 上下文
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [datasetId, setDatasetId] = useState<number | undefined>(currentDataset?.id);
  const [maxIterations, setMaxIterations] = useState<number>(5);

  // 会话
  const [sessions, setSessions] = useState<RagSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<number | null>(null);

  // 对话气泡
  const [bubbles, setBubbles] = useState<ChatBubble[]>([]);

  // 输入态
  const [input, setInput] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // ---------- 拉取数据集 ----------
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

  // ---------- 拉取会话列表 ----------
  const loadSessions = useCallback(async () => {
    try {
      const list = await ragApi.listSessions();
      setSessions(list);
    } catch (err) {
      message.error(extractError(err));
    }
  }, []);

  // ---------- 拉取单个会话历史 ----------
  const loadSession = useCallback(async (sessionId: number) => {
    try {
      const detail = await ragApi.getSession(sessionId);
      setBubbles(bubblesFromMessages(detail.messages));
      setActiveSessionId(sessionId);
    } catch (err) {
      message.error(extractError(err));
    }
  }, []);

  useEffect(() => {
    void loadDatasets();
    void loadSessions();
  }, [loadDatasets, loadSessions]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [bubbles]);

  const datasetOptions = useMemo(
    () => datasets.map((d) => ({ label: `${d.name} (#${d.id})`, value: d.id })),
    [datasets],
  );

  // ---------- 新建会话 ----------
  const handleNewSession = () => {
    setActiveSessionId(null);
    setBubbles([]);
    setInput('');
  };

  // ---------- 删除会话 ----------
  const handleDeleteSession = async (sessionId: number) => {
    try {
      await ragApi.deleteSession(sessionId);
      message.success('会话已删除');
      if (activeSessionId === sessionId) {
        handleNewSession();
      }
      await loadSessions();
    } catch (err) {
      message.error(extractError(err));
    }
  };

  // ---------- 发送 ----------
  const handleSubmit = async () => {
    const trimmed = input.trim();
    if (!trimmed) {
      message.warning('请输入查询内容');
      return;
    }
    const tempUserKey = `u-${Date.now()}`;
    const tempAiKey = `a-${Date.now()}`;
    setBubbles((prev) => [
      ...prev,
      { key: tempUserKey, role: 'user', content: trimmed },
      { key: tempAiKey, role: 'assistant', content: '', loading: true },
    ]);
    setInput('');
    setSubmitting(true);

    try {
      const resp: RagChatResponse = await ragApi.chatQuery({
        query: trimmed,
        session_id: activeSessionId,
        dataset_id: datasetId ?? null,
        max_iterations: maxIterations,
      });
      setBubbles((prev) =>
        prev.map((b) =>
          b.key === tempAiKey
            ? {
                ...b,
                loading: false,
                content: resp.answer,
                toolTrace: resp.tool_trace,
                citations: resp.citations,
              }
            : b,
        ),
      );
      setActiveSessionId(resp.session_id);
      // 异步刷新会话列表（包含 message_count 更新）
      void loadSessions();
      if (resp.finish_reason === 'max_iterations') {
        message.warning('已达到最大工具调用轮数，请简化问题或拆分多轮提问');
      } else {
        message.success(`回答已生成（${formatDuration(resp.query_time_ms)}）`);
      }
    } catch (err) {
      const msg = extractError(err);
      message.error(msg);
      setBubbles((prev) =>
        prev.map((b) => (b.key === tempAiKey ? { ...b, loading: false, error: msg } : b)),
      );
    } finally {
      setSubmitting(false);
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void handleSubmit();
    }
  };

  // ---------- 渲染 AI 泡 ----------
  const renderAiBubble = (bubble: ChatBubble) => {
    if (bubble.loading) {
      return (
        <div style={aiBubbleStyle}>
          <Space>
            <Spin size="small" />
            <Text type="secondary">LLM 正在思考并调度工具...</Text>
          </Space>
        </div>
      );
    }
    if (bubble.error) {
      return (
        <div style={aiBubbleStyle}>
          <Text type="danger">请求失败：{bubble.error}</Text>
        </div>
      );
    }
    const trace = bubble.toolTrace ?? [];
    const citations = bubble.citations ?? [];

    return (
      <div style={aiBubbleStyle}>
        <Paragraph style={{ marginBottom: 8, whiteSpace: 'pre-wrap' }}>
          {bubble.content || '（无回答）'}
        </Paragraph>

        {trace.length > 0 && (
          <Collapse
            size="small"
            ghost
            items={[
              {
                key: 'tools',
                label: (
                  <Space>
                    <ToolOutlined />
                    <Text strong>工具链路</Text>
                    <Tag color="blue">{trace.length} 步</Tag>
                  </Space>
                ),
                children: (
                  <List
                    size="small"
                    dataSource={trace}
                    renderItem={(t) => (
                      <List.Item style={{ padding: '4px 0' }}>
                        <Space size={6} wrap>
                          <Tag color={t.ok ? 'cyan' : 'red'}>{t.name}</Tag>
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            {t.summary}
                          </Text>
                          {Object.keys(t.arguments ?? {}).length > 0 && (
                            <Text code style={{ fontSize: 12 }}>
                              {JSON.stringify(t.arguments)}
                            </Text>
                          )}
                        </Space>
                      </List.Item>
                    )}
                  />
                ),
              },
            ]}
          />
        )}

        {citations.length > 0 && (
          <Collapse
            size="small"
            ghost
            items={[
              {
                key: 'citations',
                label: (
                  <Space>
                    <Text strong>引用</Text>
                    <Tag color="purple">{citations.length}</Tag>
                  </Space>
                ),
                children: (
                  <Space size={[4, 4]} wrap>
                    {citations.slice(0, 50).map((c, idx) => (
                      <Tag key={`${c.cell_id}-${idx}`} color="geekblue">
                        {c.cell_id}
                        {c.dataset_id ? ` @ ds#${c.dataset_id}` : ''}
                      </Tag>
                    ))}
                    {citations.length > 50 && (
                      <Text type="secondary">…还有 {citations.length - 50} 条</Text>
                    )}
                  </Space>
                ),
              },
            ]}
          />
        )}
      </div>
    );
  };

  return (
    <Layout style={{ background: 'transparent', minHeight: 'calc(100vh - 120px)' }}>
      <Sider
        width={260}
        style={{
          background: '#fff',
          padding: 12,
          borderRadius: 8,
          marginRight: 16,
          height: 'fit-content',
        }}
      >
        <Space direction="vertical" style={{ width: '100%' }}>
          <Button block type="primary" icon={<PlusOutlined />} onClick={handleNewSession}>
            新建会话
          </Button>
          <List
            size="small"
            dataSource={sessions}
            locale={{ emptyText: '暂无会话' }}
            renderItem={(s) => (
              <List.Item
                key={s.id}
                style={{
                  padding: '6px 8px',
                  background: activeSessionId === s.id ? '#e6f4ff' : 'transparent',
                  borderRadius: 6,
                  cursor: 'pointer',
                }}
                onClick={() => void loadSession(s.id)}
                actions={[
                  <Popconfirm
                    key="del"
                    title="删除该会话？"
                    onConfirm={(e) => {
                      e?.stopPropagation();
                      void handleDeleteSession(s.id);
                    }}
                    onCancel={(e) => e?.stopPropagation()}
                  >
                    <Button
                      type="text"
                      size="small"
                      icon={<DeleteOutlined />}
                      onClick={(e) => e.stopPropagation()}
                    />
                  </Popconfirm>,
                ]}
              >
                <List.Item.Meta
                  avatar={<MessageOutlined />}
                  title={
                    <Text ellipsis style={{ width: 140 }}>
                      {s.title}
                    </Text>
                  }
                  description={
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      {s.message_count} 条消息
                    </Text>
                  }
                />
              </List.Item>
            )}
          />
        </Space>
      </Sider>

      <Content>
        <Title level={3}>RAG 自然语言查询</Title>

        <Card size="small" style={{ marginBottom: 12 }}>
          <Paragraph style={{ marginBottom: 0 }}>
            v1.2 D4 LLM Function Calling Agent：LLM 自主决定调用
            <Text code>list_datasets</Text> / <Text code>search_by_cell_id</Text> /{' '}
            <Text code>search_by_vector</Text> / <Text code>filter_cells</Text> /{' '}
            <Text code>summarize_results</Text> 五个工具完成多轮检索。 示例：&ldquo;列出所有数据集&rdquo;
            或 &ldquo;找和 cell_id=AAACATAC-1 相似的 5 个细胞&rdquo;。
          </Paragraph>
        </Card>

        <Card size="small" style={{ marginBottom: 12 }}>
          <Form layout="inline">
            <Form.Item label="上下文数据集" style={{ marginBottom: 0 }}>
              <Select
                allowClear
                value={datasetId}
                onChange={(v) => setDatasetId(v)}
                options={datasetOptions}
                placeholder="可选；LLM 将默认使用此 dataset_id"
                showSearch
                optionFilterProp="label"
                style={{ width: 280 }}
              />
            </Form.Item>
            <Form.Item label="最大轮数" style={{ marginBottom: 0 }}>
              <InputNumber
                min={1}
                max={10}
                value={maxIterations}
                onChange={(v) => setMaxIterations(v ?? 5)}
                style={{ width: 100 }}
              />
            </Form.Item>
          </Form>
        </Card>

        <Card size="small" style={{ marginBottom: 12 }}>
          <div ref={scrollRef} style={{ maxHeight: 560, overflowY: 'auto', padding: '4px 8px' }}>
            {bubbles.length === 0 ? (
              <Empty description="还没有对话，输入问题开始多轮 RAG 查询" />
            ) : (
              <List
                dataSource={bubbles}
                split={false}
                renderItem={(bubble) => (
                  <List.Item
                    key={bubble.key}
                    style={{ display: 'block', padding: '8px 0', border: 0 }}
                  >
                    {bubble.role === 'user' ? (
                      <div
                        style={{
                          display: 'flex',
                          justifyContent: 'flex-end',
                          alignItems: 'flex-start',
                          gap: 8,
                        }}
                      >
                        <div style={userBubbleStyle}>{bubble.content}</div>
                        <Avatar
                          icon={<UserOutlined />}
                          style={{ backgroundColor: '#1677ff', flexShrink: 0 }}
                        />
                      </div>
                    ) : (
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
                        {renderAiBubble(bubble)}
                      </div>
                    )}
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
              placeholder="自然语言提问，Enter 发送，Shift+Enter 换行"
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
      </Content>
    </Layout>
  );
};

export default RagChatPage;
// 帮助 ts-check 不抱怨未使用的 import（部分类型仅用于辅助函数推断）
export type { ToolCall };
