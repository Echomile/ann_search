import { useCallback, useEffect, useMemo, useState } from 'react';
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
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { MinusCircleOutlined, PlusOutlined, SearchOutlined } from '@ant-design/icons';
import { datasetsApi } from '@/api/datasets';
import { indexesApi } from '@/api/indexes';
import { searchApi } from '@/api/search';
import type { Dataset } from '@/types/dataset';
import type { IndexRecord } from '@/types/indexRecord';
import type { SearchFilters, SearchHit, SearchResponse } from '@/types/search';
import { useDatasetStore } from '@/store/datasetStore';
import { formatDuration } from '@/utils/format';
import { renderMetadataTags } from '@/utils/metadata';
import { extractError } from '@/utils/error';

const { Title, Paragraph, Text } = Typography;

type TabKey = 'by-id' | 'by-vector' | 'multi-dataset';

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

  const [byIdForm] = Form.useForm<ByIdValues>();
  const [byVectorForm] = Form.useForm<ByVectorValues>();
  const [multiForm] = Form.useForm<MultiValues>();

  const watchedDatasetId = Form.useWatch('dataset_id', byIdForm);
  const watchedDatasetIdV = Form.useWatch('dataset_id', byVectorForm);

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
    multiForm.setFieldsValue({
      dataset_ids: [currentDataset.id],
      source_dataset_id: currentDataset.id,
    });
    void loadIndexes(currentDataset.id);
  }, [currentDataset, byIdForm, byVectorForm, multiForm, loadIndexes]);

  useEffect(() => {
    if (activeTab === 'by-id' && watchedDatasetId) void loadIndexes(watchedDatasetId);
    if (activeTab === 'by-vector' && watchedDatasetIdV) void loadIndexes(watchedDatasetIdV);
  }, [activeTab, watchedDatasetId, watchedDatasetIdV, loadIndexes]);

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

  const renderFilterList = (formName: 'byId' | 'byVector' | 'multi') => (
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
  ];

  return (
    <div>
      <Title level={3}>相似细胞检索</Title>
      <Paragraph type="secondary">
        支持按细胞 ID、自定义向量、跨数据集联合三种方式发起 Top-K 检索，并可叠加 metadata 过滤。
        {currentIndex && (
          <Text type="secondary" style={{ marginLeft: 12 }}>
            当前默认索引：#{currentIndex.id} ({currentIndex.backend})
          </Text>
        )}
      </Paragraph>

      <Card style={{ marginBottom: 24 }}>
        <Tabs activeKey={activeTab} onChange={(k) => setActiveTab(k as TabKey)} items={tabItems} />
      </Card>

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
    </div>
  );
};

export default SearchPage;
