import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Form,
  Input,
  InputNumber,
  Row,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { MinusCircleOutlined, PlusOutlined, SearchOutlined } from '@ant-design/icons';
import { datasetsApi } from '@/api/datasets';
import { indexesApi } from '@/api/indexes';
import { searchApi } from '@/api/search';
import type { StreamDoneEvent } from '@/api/search';
import type { Dataset } from '@/types/dataset';
import type { IndexRecord } from '@/types/indexRecord';
import type {
  EnsembleHit,
  EnsembleSearchResponse,
  SearchFilters,
  SearchHit,
  SearchResponse,
} from '@/types/search';
import { useDatasetStore } from '@/store/datasetStore';
import { formatDuration } from '@/utils/format';
import { renderMetadataTags } from '@/utils/metadata';
import { extractError } from '@/utils/error';

const { Title, Paragraph, Text } = Typography;

type TabKey = 'by-id' | 'by-vector' | 'multi-dataset' | 'by-vector-stream' | 'ensemble';

interface FilterRow {
  key: string;
  value: string;
}

interface ByIdValues {
  dataset_id: number;
  index_id?: number | null;
  cell_id: string;
  top_k: number;
  filters?: FilterRow[];
}

interface ByVectorValues {
  dataset_id: number;
  index_id?: number | null;
  vector_text: string;
  top_k: number;
  filters?: FilterRow[];
}

interface MultiValues {
  dataset_ids: number[];
  source_dataset_id: number;
  cell_id: string;
  top_k: number;
  filters?: FilterRow[];
}

interface StreamValues {
  dataset_id: number;
  index_id?: number | null;
  vector_text: string;
  top_k: number;
  filters?: FilterRow[];
}

// Ensemble：``cell_id`` 与 ``vector_text`` 至少填一个；同时填以 cell_id 优先（前端拦截二选一）
interface EnsembleValues {
  dataset_id: number;
  index_ids: number[];
  cell_id?: string;
  vector_text?: string;
  top_k: number;
  filters?: FilterRow[];
}

const ENSEMBLE_MIN_INDEXES = 2;
const ENSEMBLE_MAX_INDEXES = 5;

// 将动态过滤项数组转为后端 filters dict；同一 key 多值聚合为数组
const buildFilters = (rows?: FilterRow[]): SearchFilters | undefined => {
  if (!rows || rows.length === 0) return undefined;
  const out: Record<string, string | string[]> = {};
  for (const row of rows) {
    if (!row?.key || row.value === undefined || row.value === '') continue;
    const k = row.key.trim();
    const v = row.value.trim();
    if (!k || !v) continue;
    if (k in out) {
      const prev = out[k];
      out[k] = Array.isArray(prev) ? [...prev, v] : [prev as string, v];
    } else {
      out[k] = v;
    }
  }
  return Object.keys(out).length > 0 ? out : undefined;
};

const parseVector = (text: string): number[] => {
  const cleaned = text.replace(/[[\]\s]+/g, ' ').trim();
  if (!cleaned) return [];
  return cleaned
    .split(/[,;\s]+/)
    .filter(Boolean)
    .map((token) => Number(token));
};

// metadata 折叠：重要字段优先 + "+N 更多" Popover，避免 56 列 Tag 撑满行
const renderMeta = (meta: SearchHit['meta']) => renderMetadataTags(meta);

const SearchPage = () => {
  const currentDataset = useDatasetStore((s) => s.currentDataset);
  const currentIndex = useDatasetStore((s) => s.currentIndex);

  const [activeTab, setActiveTab] = useState<TabKey>('by-id');
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [indexes, setIndexes] = useState<IndexRecord[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<SearchResponse | null>(null);

  // F6 SSE 流式状态
  const [streaming, setStreaming] = useState(false);
  const [streamHits, setStreamHits] = useState<SearchHit[]>([]);
  const [streamDone, setStreamDone] = useState<StreamDoneEvent | null>(null);
  const streamAbortRef = useRef<AbortController | null>(null);

  // F7 ensemble 检索状态
  const [ensembleSubmitting, setEnsembleSubmitting] = useState(false);
  const [ensembleResult, setEnsembleResult] = useState<EnsembleSearchResponse | null>(null);

  const [byIdForm] = Form.useForm<ByIdValues>();
  const [byVectorForm] = Form.useForm<ByVectorValues>();
  const [multiForm] = Form.useForm<MultiValues>();
  const [streamForm] = Form.useForm<StreamValues>();
  const [ensembleForm] = Form.useForm<EnsembleValues>();

  const watchedDatasetId = Form.useWatch('dataset_id', byIdForm);
  const watchedDatasetIdV = Form.useWatch('dataset_id', byVectorForm);
  const watchedDatasetIdS = Form.useWatch('dataset_id', streamForm);
  const watchedDatasetIdE = Form.useWatch('dataset_id', ensembleForm);

  const loadDatasets = useCallback(async () => {
    try {
      const list = await datasetsApi.list();
      const ready = list.filter((d) => d.status === 'ready');
      setDatasets(ready);
    } catch (err) {
      message.error(extractError(err));
    }
  }, []);

  const loadIndexes = useCallback(async (datasetId?: number) => {
    if (!datasetId) {
      setIndexes([]);
      return;
    }
    try {
      const list = await indexesApi.listByDataset(datasetId);
      setIndexes(list.filter((i) => i.status === 'ready'));
    } catch (err) {
      message.error(extractError(err));
    }
  }, []);

  useEffect(() => {
    void loadDatasets();
  }, [loadDatasets]);

  // 根据当前选中数据集，默认填充表单
  useEffect(() => {
    if (!currentDataset) return;
    byIdForm.setFieldsValue({ dataset_id: currentDataset.id });
    byVectorForm.setFieldsValue({ dataset_id: currentDataset.id });
    streamForm.setFieldsValue({ dataset_id: currentDataset.id });
    ensembleForm.setFieldsValue({ dataset_id: currentDataset.id });
    multiForm.setFieldsValue({
      dataset_ids: [currentDataset.id],
      source_dataset_id: currentDataset.id,
    });
    void loadIndexes(currentDataset.id);
  }, [currentDataset, byIdForm, byVectorForm, streamForm, ensembleForm, multiForm, loadIndexes]);

  useEffect(() => {
    if (activeTab === 'by-id' && watchedDatasetId) void loadIndexes(watchedDatasetId);
    if (activeTab === 'by-vector' && watchedDatasetIdV) void loadIndexes(watchedDatasetIdV);
    if (activeTab === 'by-vector-stream' && watchedDatasetIdS) void loadIndexes(watchedDatasetIdS);
    if (activeTab === 'ensemble' && watchedDatasetIdE) void loadIndexes(watchedDatasetIdE);
  }, [
    activeTab,
    watchedDatasetId,
    watchedDatasetIdV,
    watchedDatasetIdS,
    watchedDatasetIdE,
    loadIndexes,
  ]);

  // 组件卸载时中断仍在进行的 SSE 流，避免泄漏 reader
  useEffect(() => {
    return () => {
      streamAbortRef.current?.abort();
    };
  }, []);

  const indexOptions = useMemo(
    () =>
      indexes.map((i) => ({
        label: `#${i.id} · ${i.backend} · ${i.metric}`,
        value: i.id,
      })),
    [indexes],
  );

  const datasetOptions = useMemo(
    () => datasets.map((d) => ({ label: `${d.name} (#${d.id})`, value: d.id })),
    [datasets],
  );

  const handleSubmitById = async () => {
    let v: ByIdValues;
    try {
      v = await byIdForm.validateFields();
    } catch {
      return;
    }
    setSubmitting(true);
    try {
      const resp = await searchApi.byId({
        dataset_id: v.dataset_id,
        cell_id: v.cell_id.trim(),
        top_k: v.top_k,
        index_id: v.index_id ?? null,
        filters: buildFilters(v.filters),
      });
      setResult(resp);
    } catch (err) {
      message.error(extractError(err));
    } finally {
      setSubmitting(false);
    }
  };

  const handleSubmitByVector = async () => {
    let v: ByVectorValues;
    try {
      v = await byVectorForm.validateFields();
    } catch {
      return;
    }
    const vector = parseVector(v.vector_text);
    if (vector.length === 0 || vector.some((n) => Number.isNaN(n))) {
      message.error('请输入合法的浮点数向量（逗号 / 空格 / 换行分隔）');
      return;
    }
    setSubmitting(true);
    try {
      const resp = await searchApi.byVector({
        dataset_id: v.dataset_id,
        vector,
        top_k: v.top_k,
        index_id: v.index_id ?? null,
        filters: buildFilters(v.filters),
      });
      setResult(resp);
    } catch (err) {
      message.error(extractError(err));
    } finally {
      setSubmitting(false);
    }
  };

  const handleSubmitMulti = async () => {
    let v: MultiValues;
    try {
      v = await multiForm.validateFields();
    } catch {
      return;
    }
    if (v.dataset_ids.length < 1) {
      message.error('至少选择一个数据集');
      return;
    }
    setSubmitting(true);
    try {
      const resp = await searchApi.multiDataset({
        dataset_ids: v.dataset_ids,
        source_dataset_id: v.source_dataset_id,
        cell_id: v.cell_id.trim(),
        top_k: v.top_k,
        filters: buildFilters(v.filters),
      });
      setResult(resp);
    } catch (err) {
      message.error(extractError(err));
    } finally {
      setSubmitting(false);
    }
  };

  // F6：开始 SSE 流式检索。逐条 push hit，done 事件回填汇总信息
  const handleStartStream = async () => {
    let v: StreamValues;
    try {
      v = await streamForm.validateFields();
    } catch {
      return;
    }
    const vector = parseVector(v.vector_text);
    if (vector.length === 0 || vector.some((n) => Number.isNaN(n))) {
      message.error('请输入合法的浮点数向量（逗号 / 空格 / 换行分隔）');
      return;
    }
    streamAbortRef.current?.abort();
    const ctrl = new AbortController();
    streamAbortRef.current = ctrl;
    setStreaming(true);
    setStreamHits([]);
    setStreamDone(null);
    try {
      for await (const ev of searchApi.byVectorStream(
        {
          dataset_id: v.dataset_id,
          vector,
          top_k: v.top_k,
          index_id: v.index_id ?? null,
          filters: buildFilters(v.filters),
        },
        { signal: ctrl.signal },
      )) {
        if (ev.type === 'hit') {
          const hit: SearchHit = {
            rank: ev.rank,
            cell_id: ev.cell_id,
            distance: ev.distance,
            meta: ev.meta,
            source_dataset_id: ev.source_dataset_id,
          };
          setStreamHits((prev) => [...prev, hit]);
        } else if (ev.type === 'done') {
          setStreamDone(ev);
        }
      }
    } catch (err) {
      if (!ctrl.signal.aborted) {
        message.error(extractError(err));
      }
    } finally {
      streamAbortRef.current = null;
      setStreaming(false);
    }
  };

  const handleStopStream = () => {
    streamAbortRef.current?.abort();
    streamAbortRef.current = null;
    setStreaming(false);
  };

  // F7：ensemble 检索；前端拦截 index_ids 数量边界与查询输入合法性
  const handleSubmitEnsemble = async () => {
    let v: EnsembleValues;
    try {
      v = await ensembleForm.validateFields();
    } catch {
      return;
    }
    if (
      !v.index_ids ||
      v.index_ids.length < ENSEMBLE_MIN_INDEXES ||
      v.index_ids.length > ENSEMBLE_MAX_INDEXES
    ) {
      message.error(
        `请选择 ${ENSEMBLE_MIN_INDEXES}~${ENSEMBLE_MAX_INDEXES} 个索引，当前 ${v.index_ids?.length ?? 0}`,
      );
      return;
    }
    const cellId = v.cell_id?.trim();
    const vectorText = v.vector_text?.trim();
    if (!cellId && !vectorText) {
      message.error('请填写查询 cell_id 或查询向量（二选一）');
      return;
    }
    let vector: number[] | undefined;
    if (!cellId && vectorText) {
      vector = parseVector(vectorText);
      if (vector.length === 0 || vector.some((n) => Number.isNaN(n))) {
        message.error('请输入合法的浮点数向量（逗号 / 空格 / 换行分隔）');
        return;
      }
    }
    setEnsembleSubmitting(true);
    try {
      const resp = await searchApi.ensemble({
        dataset_id: v.dataset_id,
        index_ids: v.index_ids,
        query: cellId ? { cell_id: cellId } : { vector },
        top_k: v.top_k,
        filters: buildFilters(v.filters),
      });
      setEnsembleResult(resp);
    } catch (err) {
      message.error(extractError(err));
    } finally {
      setEnsembleSubmitting(false);
    }
  };

  const renderFilterList = (formName: 'byId' | 'byVector' | 'multi' | 'stream' | 'ensemble') => (
    <Form.List name="filters">
      {(fields, { add, remove }) => (
        <>
          {fields.map((field) => (
            <Space key={field.key} align="baseline" style={{ display: 'flex', marginBottom: 8 }}>
              <Form.Item
                name={[field.name, 'key']}
                rules={[{ required: true, message: '字段名' }]}
                style={{ marginBottom: 0 }}
              >
                <Input placeholder="例如 cell_type" style={{ width: 180 }} />
              </Form.Item>
              <Text type="secondary">=</Text>
              <Form.Item
                name={[field.name, 'value']}
                rules={[{ required: true, message: '取值' }]}
                style={{ marginBottom: 0 }}
              >
                <Input placeholder="例如 T cell" style={{ width: 220 }} />
              </Form.Item>
              <MinusCircleOutlined onClick={() => remove(field.name)} />
            </Space>
          ))}
          <Button
            type="dashed"
            onClick={() => add({ key: '', value: '' })}
            icon={<PlusOutlined />}
            data-form={formName}
          >
            添加过滤条件
          </Button>
        </>
      )}
    </Form.List>
  );

  const resultColumns: ColumnsType<SearchHit> = [
    { title: 'Rank', dataIndex: 'rank', key: 'rank', width: 70 },
    { title: 'Cell ID', dataIndex: 'cell_id', key: 'cell_id', width: 240 },
    {
      title: 'Distance',
      dataIndex: 'distance',
      key: 'distance',
      width: 130,
      render: (v: number) => v.toFixed(6),
    },
    ...(activeTab === 'multi-dataset'
      ? [
          {
            title: '来源数据集',
            dataIndex: 'source_dataset_id' as const,
            key: 'source_dataset_id',
            width: 130,
            render: (v: number | null) => (v == null ? '-' : `#${v}`),
          },
        ]
      : []),
    { title: 'Metadata', dataIndex: 'meta', key: 'meta', render: renderMeta },
  ];

  const streamColumns: ColumnsType<SearchHit> = [
    { title: 'Rank', dataIndex: 'rank', key: 'rank', width: 70 },
    { title: 'Cell ID', dataIndex: 'cell_id', key: 'cell_id', width: 240 },
    {
      title: 'Distance',
      dataIndex: 'distance',
      key: 'distance',
      width: 130,
      render: (v: number) => v.toFixed(6),
    },
    { title: 'Metadata', dataIndex: 'meta', key: 'meta', render: renderMeta },
  ];

  const ensembleColumns: ColumnsType<EnsembleHit> = [
    { title: 'Rank', dataIndex: 'rank', key: 'rank', width: 70 },
    { title: 'Cell ID', dataIndex: 'cell_id', key: 'cell_id', width: 240 },
    {
      title: 'Score',
      dataIndex: 'score',
      key: 'score',
      width: 130,
      render: (v: number) => v.toFixed(6),
    },
    {
      title: 'Voted by',
      dataIndex: 'voted_by',
      key: 'voted_by',
      width: 220,
      render: (ids: number[]) => (
        <Space size={[4, 4]} wrap>
          {ids.map((id) => (
            <Tag key={id} color="purple">{`#${id}`}</Tag>
          ))}
        </Space>
      ),
    },
    { title: 'Metadata', dataIndex: 'meta', key: 'meta', render: renderMeta },
  ];

  const tabItems = [
    {
      key: 'by-id' as const,
      label: '按细胞 ID',
      children: (
        <Form
          form={byIdForm}
          layout="vertical"
          initialValues={{ top_k: 10, filters: [] }}
          onFinish={handleSubmitById}
        >
          <Row gutter={16}>
            <Col xs={24} md={8}>
              <Form.Item label="数据集" name="dataset_id" rules={[{ required: true }]}>
                <Select
                  options={datasetOptions}
                  placeholder="选择数据集"
                  showSearch
                  optionFilterProp="label"
                />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item label="索引（不选则用最新 ready）" name="index_id">
                <Select options={indexOptions} placeholder="自动选择" allowClear />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item label="Top-K" name="top_k" rules={[{ required: true }]}>
                <InputNumber min={1} max={1000} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item label="查询细胞 ID" name="cell_id" rules={[{ required: true }]}>
            <Input placeholder="例如 AAACATACAACCAC-1" />
          </Form.Item>
          <Form.Item label="过滤器（可选）">{renderFilterList('byId')}</Form.Item>
          <Button type="primary" htmlType="submit" loading={submitting} icon={<SearchOutlined />}>
            发起检索
          </Button>
        </Form>
      ),
    },
    {
      key: 'by-vector' as const,
      label: '按向量',
      children: (
        <Form
          form={byVectorForm}
          layout="vertical"
          initialValues={{ top_k: 10, filters: [] }}
          onFinish={handleSubmitByVector}
        >
          <Row gutter={16}>
            <Col xs={24} md={8}>
              <Form.Item label="数据集" name="dataset_id" rules={[{ required: true }]}>
                <Select
                  options={datasetOptions}
                  placeholder="选择数据集"
                  showSearch
                  optionFilterProp="label"
                />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item label="索引" name="index_id">
                <Select options={indexOptions} placeholder="自动选择" allowClear />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item label="Top-K" name="top_k" rules={[{ required: true }]}>
                <InputNumber min={1} max={1000} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item
            label="查询向量（逗号 / 空格分隔，维度需与数据集一致）"
            name="vector_text"
            rules={[{ required: true, message: '请输入向量' }]}
          >
            <Input.TextArea rows={4} placeholder="0.12, 0.34, -0.05, ..." />
          </Form.Item>
          <Form.Item label="过滤器（可选）">{renderFilterList('byVector')}</Form.Item>
          <Button type="primary" htmlType="submit" loading={submitting} icon={<SearchOutlined />}>
            发起检索
          </Button>
        </Form>
      ),
    },
    {
      key: 'multi-dataset' as const,
      label: '多数据集联合',
      children: (
        <Form
          form={multiForm}
          layout="vertical"
          initialValues={{ top_k: 10, filters: [] }}
          onFinish={handleSubmitMulti}
        >
          <Row gutter={16}>
            <Col xs={24} md={12}>
              <Form.Item label="参与检索的数据集" name="dataset_ids" rules={[{ required: true }]}>
                <Select
                  mode="multiple"
                  options={datasetOptions}
                  placeholder="选择多个数据集"
                  showSearch
                  optionFilterProp="label"
                />
              </Form.Item>
            </Col>
            <Col xs={24} md={6}>
              <Form.Item
                label="cell_id 所属数据集"
                name="source_dataset_id"
                rules={[{ required: true }]}
              >
                <Select options={datasetOptions} placeholder="选择源数据集" />
              </Form.Item>
            </Col>
            <Col xs={24} md={6}>
              <Form.Item label="Top-K" name="top_k" rules={[{ required: true }]}>
                <InputNumber min={1} max={1000} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item label="查询细胞 ID" name="cell_id" rules={[{ required: true }]}>
            <Input placeholder="该 cell_id 须存在于所选源数据集中" />
          </Form.Item>
          <Form.Item label="过滤器（可选）">{renderFilterList('multi')}</Form.Item>
          <Button type="primary" htmlType="submit" loading={submitting} icon={<SearchOutlined />}>
            发起检索
          </Button>
        </Form>
      ),
    },
    {
      key: 'by-vector-stream' as const,
      label: 'SSE 流式',
      children: (
        <Form
          form={streamForm}
          layout="vertical"
          initialValues={{ top_k: 10, filters: [] }}
          onFinish={handleStartStream}
        >
          <Row gutter={16}>
            <Col xs={24} md={8}>
              <Form.Item label="数据集" name="dataset_id" rules={[{ required: true }]}>
                <Select
                  options={datasetOptions}
                  placeholder="选择数据集"
                  showSearch
                  optionFilterProp="label"
                />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item label="索引" name="index_id">
                <Select options={indexOptions} placeholder="自动选择" allowClear />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item label="Top-K" name="top_k" rules={[{ required: true }]}>
                <InputNumber min={1} max={1000} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item
            label="查询向量（逗号 / 空格分隔，维度需与数据集一致）"
            name="vector_text"
            rules={[{ required: true, message: '请输入向量' }]}
          >
            <Input.TextArea rows={4} placeholder="0.12, 0.34, -0.05, ..." />
          </Form.Item>
          <Form.Item label="过滤器（可选）">{renderFilterList('stream')}</Form.Item>
          <Space>
            {streaming ? (
              <Button danger onClick={handleStopStream}>
                停止流式
              </Button>
            ) : (
              <Button
                type="primary"
                htmlType="submit"
                loading={streaming}
                icon={<SearchOutlined />}
              >
                开始流式
              </Button>
            )}
            <Text type="secondary">
              已接收 {streamHits.length} 条
              {streamDone ? ` · 总耗时 ${formatDuration(streamDone.latency_ms)}` : ''}
            </Text>
          </Space>
          <div style={{ marginTop: 16 }}>
            {streamHits.length === 0 && !streaming ? (
              <Empty description="尚未发起流式检索" />
            ) : (
              <>
                {streamDone && (
                  <Descriptions
                    size="small"
                    column={4}
                    style={{ marginBottom: 16 }}
                    items={[
                      {
                        key: 'latency',
                        label: '总耗时',
                        children: formatDuration(streamDone.latency_ms),
                      },
                      {
                        key: 'count',
                        label: '返回数量',
                        children: streamHits.length,
                      },
                      {
                        key: 'backend',
                        label: '后端',
                        children: streamDone.index_backend ?? '-',
                      },
                      {
                        key: 'metric',
                        label: '距离',
                        children: streamDone.metric ?? '-',
                      },
                    ]}
                  />
                )}
                <Table<SearchHit>
                  rowKey={(r) => `${r.cell_id}-${r.rank}`}
                  dataSource={streamHits}
                  columns={streamColumns}
                  pagination={{ pageSize: 20 }}
                />
              </>
            )}
          </div>
        </Form>
      ),
    },
    {
      key: 'ensemble' as const,
      label: 'Ensemble 多后端',
      children: (
        <Form
          form={ensembleForm}
          layout="vertical"
          initialValues={{ top_k: 10, filters: [], index_ids: [] }}
          onFinish={handleSubmitEnsemble}
        >
          <Row gutter={16}>
            <Col xs={24} md={8}>
              <Form.Item label="数据集" name="dataset_id" rules={[{ required: true }]}>
                <Select
                  options={datasetOptions}
                  placeholder="选择数据集"
                  showSearch
                  optionFilterProp="label"
                />
              </Form.Item>
            </Col>
            <Col xs={24} md={10}>
              <Form.Item
                label={`参与 ensemble 的索引（${ENSEMBLE_MIN_INDEXES}~${ENSEMBLE_MAX_INDEXES} 个）`}
                name="index_ids"
                rules={[{ required: true, message: '至少选择 2 个索引' }]}
              >
                <Select
                  mode="multiple"
                  options={indexOptions}
                  placeholder="勾选同数据集下多个 ready 索引"
                  maxTagCount="responsive"
                />
              </Form.Item>
            </Col>
            <Col xs={24} md={6}>
              <Form.Item label="Top-K" name="top_k" rules={[{ required: true }]}>
                <InputNumber min={1} max={1000} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col xs={24} md={12}>
              <Form.Item
                label="查询细胞 ID（与查询向量二选一，cell_id 优先）"
                name="cell_id"
              >
                <Input placeholder="例如 AAACATACAACCAC-1" allowClear />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item
                label="查询向量（逗号 / 空格分隔，维度需与数据集一致）"
                name="vector_text"
              >
                <Input.TextArea rows={2} placeholder="0.12, 0.34, -0.05, ..." />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item label="过滤器（可选）">{renderFilterList('ensemble')}</Form.Item>
          <Button
            type="primary"
            htmlType="submit"
            loading={ensembleSubmitting}
            icon={<SearchOutlined />}
          >
            发起检索
          </Button>
          <div style={{ marginTop: 16 }}>
            {ensembleResult === null ? (
              <Empty description="尚未发起 ensemble 检索" />
            ) : (
              <>
                <Descriptions
                  size="small"
                  column={3}
                  style={{ marginBottom: 16 }}
                  items={[
                    {
                      key: 'latency',
                      label: '耗时',
                      children: formatDuration(ensembleResult.latency_ms),
                    },
                    {
                      key: 'count',
                      label: '返回数量',
                      children: ensembleResult.hits.length,
                    },
                    {
                      key: 'per_index',
                      label: '各索引耗时',
                      children:
                        Object.entries(ensembleResult.per_index_latency_ms)
                          .map(([k, v]) => `#${k}: ${formatDuration(v)}`)
                          .join(' · ') || '-',
                    },
                  ]}
                />
                {ensembleResult.hits.length === 0 ? (
                  <Alert
                    type="warning"
                    showIcon
                    message="ensemble 后无命中结果，请调整过滤条件或扩大 Top-K"
                  />
                ) : (
                  <Table<EnsembleHit>
                    rowKey={(r) => `${r.cell_id}-${r.rank}`}
                    dataSource={ensembleResult.hits}
                    columns={ensembleColumns}
                    pagination={{ pageSize: 20 }}
                  />
                )}
              </>
            )}
          </div>
        </Form>
      ),
    },
  ];

  return (
    <div>
      <Title level={3}>相似细胞检索</Title>
      <Paragraph type="secondary">
        支持按细胞 ID、自定义向量、跨数据集联合、SSE 流式、多后端 ensemble 五种方式发起 Top-K 检索，并可叠加 metadata 过滤。
        {currentIndex && (
          <Text type="secondary" style={{ marginLeft: 12 }}>
            当前默认索引：#{currentIndex.id} ({currentIndex.backend})
          </Text>
        )}
      </Paragraph>

      <Card style={{ marginBottom: 24 }}>
        <Tabs activeKey={activeTab} onChange={(k) => setActiveTab(k as TabKey)} items={tabItems} />
      </Card>

      {(activeTab === 'by-id' || activeTab === 'by-vector' || activeTab === 'multi-dataset') && (
        <Card title="检索结果">
          {result === null ? (
            <Empty description="尚未发起检索" />
          ) : (
            <>
              <Descriptions
                size="small"
                column={4}
                style={{ marginBottom: 16 }}
                items={[
                  { key: 'latency', label: '耗时', children: formatDuration(result.latency_ms) },
                  { key: 'count', label: '返回数量', children: result.hits.length },
                  {
                    key: 'backend',
                    label: '后端',
                    children: result.index_backend ?? '-',
                  },
                  { key: 'metric', label: '距离', children: result.metric ?? '-' },
                ]}
              />
              {result.hits.length === 0 ? (
                <Alert
                  type="warning"
                  showIcon
                  message="过滤后无命中结果，请调整过滤条件或扩大 Top-K"
                />
              ) : (
                <Table<SearchHit>
                  rowKey={(r) => `${r.source_dataset_id ?? 'na'}-${r.cell_id}-${r.rank}`}
                  dataSource={result.hits}
                  columns={resultColumns}
                  pagination={{ pageSize: 20 }}
                />
              )}
            </>
          )}
        </Card>
      )}
    </div>
  );
};

export default SearchPage;
