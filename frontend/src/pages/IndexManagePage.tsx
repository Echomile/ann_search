import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Form,
  InputNumber,
  Popconfirm,
  Row,
  Select,
  Skeleton,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { DeleteOutlined, ReloadOutlined } from '@ant-design/icons';
import { Link, useNavigate } from 'react-router-dom';
import { indexesApi } from '@/api/indexes';
import type {
  DistanceMetric,
  IndexBackend,
  IndexParams,
  IndexRecord,
  IndexStatusName,
} from '@/types/indexRecord';
import { useDatasetStore } from '@/store/datasetStore';
import { formatDateTime, formatMemoryMb, formatSeconds, indexStatusColor } from '@/utils/format';
import { extractError } from '@/utils/error';
import { usePolling } from '@/hooks/usePolling';

const { Title, Paragraph, Text } = Typography;

const POLL_INTERVAL_MS = 5000;
const FINAL_STATUSES: IndexStatusName[] = ['ready', 'failed'];

const BACKEND_OPTIONS: { value: IndexBackend; label: string; desc: string }[] = [
  { value: 'hnswlib', label: 'hnswlib', desc: '高召回 / 中等内存，推荐默认' },
  { value: 'faiss-hnsw', label: 'faiss-hnsw', desc: 'FAISS 实现的 HNSW' },
  { value: 'faiss-ivfpq', label: 'faiss-ivfpq', desc: '内存受限场景，需要训练 PQ' },
  { value: 'brute', label: 'brute', desc: '暴力搜索，用于评测基准' },
];

const METRIC_OPTIONS: DistanceMetric[] = ['l2', 'cosine', 'ip'];

interface ParamDef {
  key: string;
  label: string;
  defaultValue: number;
  min: number;
  max: number;
  step?: number;
}

const BACKEND_PARAMS: Record<IndexBackend, ParamDef[]> = {
  hnswlib: [
    { key: 'M', label: 'M（图的最大出度）', defaultValue: 16, min: 4, max: 128 },
    { key: 'ef_construction', label: 'ef_construction', defaultValue: 200, min: 16, max: 2000 },
    { key: 'ef_search', label: 'ef_search', defaultValue: 50, min: 1, max: 2000 },
  ],
  'faiss-hnsw': [
    { key: 'M', label: 'M', defaultValue: 32, min: 4, max: 128 },
    { key: 'ef_construction', label: 'ef_construction', defaultValue: 200, min: 16, max: 2000 },
    { key: 'ef_search', label: 'ef_search', defaultValue: 64, min: 1, max: 2000 },
  ],
  'faiss-ivfpq': [
    { key: 'nlist', label: 'nlist（聚类簇数）', defaultValue: 1024, min: 4, max: 65536 },
    { key: 'm', label: 'm（子向量数）', defaultValue: 8, min: 1, max: 128 },
    { key: 'nbits', label: 'nbits', defaultValue: 8, min: 4, max: 16 },
    { key: 'nprobe', label: 'nprobe', defaultValue: 16, min: 1, max: 4096 },
  ],
  brute: [],
};

interface BuildFormValues {
  backend: IndexBackend;
  metric: DistanceMetric;
  params: Record<string, number>;
}

const IndexManagePage = () => {
  const navigate = useNavigate();
  const currentDataset = useDatasetStore((s) => s.currentDataset);
  const currentIndex = useDatasetStore((s) => s.currentIndex);
  const setCurrentIndex = useDatasetStore((s) => s.setCurrentIndex);

  const [indexes, setIndexes] = useState<IndexRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [form] = Form.useForm<BuildFormValues>();
  const backend = Form.useWatch('backend', form) ?? 'hnswlib';

  const paramDefs = useMemo<ParamDef[]>(() => BACKEND_PARAMS[backend] ?? [], [backend]);

  const fetchIndexes = useCallback(async () => {
    if (!currentDataset) {
      setIndexes([]);
      return;
    }
    setLoading(true);
    try {
      const list = await indexesApi.listByDataset(currentDataset.id);
      setIndexes(list);
    } catch (err) {
      message.error(extractError(err));
    } finally {
      setLoading(false);
    }
  }, [currentDataset]);

  useEffect(() => {
    void fetchIndexes();
  }, [fetchIndexes]);

  useEffect(() => {
    form.setFieldsValue({
      backend: 'hnswlib',
      metric: 'l2',
      params: BACKEND_PARAMS.hnswlib.reduce<Record<string, number>>(
        (acc, p) => ({ ...acc, [p.key]: p.defaultValue }),
        {},
      ),
    });
  }, [form]);

  // 后端切换时重置参数为默认值
  const handleBackendChange = (value: IndexBackend) => {
    const defs = BACKEND_PARAMS[value] ?? [];
    form.setFieldsValue({
      backend: value,
      params: defs.reduce<Record<string, number>>(
        (acc, p) => ({ ...acc, [p.key]: p.defaultValue }),
        {},
      ),
    });
  };

  const buildingIds = useMemo(
    () => indexes.filter((i) => !FINAL_STATUSES.includes(i.status)).map((i) => i.id),
    [indexes],
  );

  const refreshBuilding = useCallback(async () => {
    if (buildingIds.length === 0) return;
    try {
      const updates = await Promise.all(buildingIds.map((id) => indexesApi.status(id)));
      setIndexes((prev) =>
        prev.map((rec) => {
          const u = updates.find((it) => it.id === rec.id);
          if (!u) return rec;
          return {
            ...rec,
            status: u.status,
            build_time_seconds: u.build_time_seconds,
            memory_mb: u.memory_mb,
          };
        }),
      );
    } catch {
      // 单次轮询失败忽略
    }
  }, [buildingIds]);

  usePolling(refreshBuilding, {
    interval: POLL_INTERVAL_MS,
    enabled: buildingIds.length > 0,
  });

  const handleSubmit = async () => {
    if (!currentDataset) {
      message.warning('请先在数据集页选中数据集');
      return;
    }
    let values: BuildFormValues;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    setSubmitting(true);
    try {
      const params: IndexParams = {};
      for (const def of BACKEND_PARAMS[values.backend]) {
        const v = values.params?.[def.key];
        if (v !== undefined && v !== null) params[def.key] = v;
      }
      await indexesApi.create(currentDataset.id, {
        backend: values.backend,
        metric: values.metric,
        params,
      });
      message.success('索引构建任务已入队');
      await fetchIndexes();
    } catch (err) {
      message.error(extractError(err));
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await indexesApi.remove(id);
      message.success('删除成功');
      if (currentIndex?.id === id) setCurrentIndex(null);
      setIndexes((prev) => prev.filter((i) => i.id !== id));
    } catch (err) {
      message.error(extractError(err));
    }
  };

  const columns: ColumnsType<IndexRecord> = [
    { title: 'ID', dataIndex: 'id', key: 'id', width: 70 },
    {
      title: '后端',
      dataIndex: 'backend',
      key: 'backend',
      width: 140,
      render: (v: IndexBackend) => <Tag color="blue">{v}</Tag>,
    },
    {
      title: '距离',
      dataIndex: 'metric',
      key: 'metric',
      width: 90,
      render: (v: string) => <Tag>{v}</Tag>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 110,
      render: (status: IndexStatusName) => <Tag color={indexStatusColor(status)}>{status}</Tag>,
    },
    {
      title: '构建耗时',
      dataIndex: 'build_time_seconds',
      key: 'build_time_seconds',
      width: 130,
      render: (v: number | null) => formatSeconds(v),
    },
    {
      title: '内存',
      dataIndex: 'memory_mb',
      key: 'memory_mb',
      width: 120,
      render: (v: number | null) => formatMemoryMb(v),
    },
    {
      title: '参数',
      dataIndex: 'params',
      key: 'params',
      width: 220,
      render: (params: IndexParams | null) => {
        if (!params || Object.keys(params).length === 0) return <Text type="secondary">-</Text>;
        const text = Object.entries(params)
          .map(([k, v]) => `${k}=${v}`)
          .join(', ');
        return (
          <Tooltip title={text}>
            <Text code style={{ fontSize: 12 }} ellipsis>
              {text}
            </Text>
          </Tooltip>
        );
      },
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (v: string) => formatDateTime(v),
    },
    {
      title: '操作',
      key: 'actions',
      width: 200,
      render: (_: unknown, record) => (
        <Space size="small">
          <Button
            size="small"
            type={currentIndex?.id === record.id ? 'primary' : 'default'}
            disabled={record.status !== 'ready'}
            onClick={(e) => {
              e.stopPropagation();
              setCurrentIndex(record);
              message.success(`已选中索引 #${record.id}`);
            }}
          >
            选用
          </Button>
          <Popconfirm
            title={`删除索引 #${record.id}？`}
            okType="danger"
            onConfirm={() => handleDelete(record.id)}
          >
            <Button
              danger
              size="small"
              icon={<DeleteOutlined />}
              onClick={(e) => e.stopPropagation()}
            >
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  if (!currentDataset) {
    return (
      <div>
        <Title level={3}>索引管理</Title>
        <Alert
          type="info"
          showIcon
          message="请先选择数据集"
          description={
            <>
              <span>请前往 </span>
              <Link to="/datasets">数据集页</Link>
              <span> 上传或选中一份数据集后再来构建索引。</span>
            </>
          }
        />
      </div>
    );
  }

  return (
    <div>
      <Title level={3}>索引管理</Title>
      <Paragraph type="secondary">
        当前数据集：<Text strong>{currentDataset.name}</Text>
        <Text type="secondary" style={{ marginLeft: 12 }}>
          维度 {currentDataset.vector_dim ?? '-'} | 细胞数 {currentDataset.cell_count ?? '-'}
        </Text>
      </Paragraph>

      <Card title="构建新索引" style={{ marginBottom: 24 }}>
        <Form form={form} layout="vertical" initialValues={{ backend: 'hnswlib', metric: 'l2' }}>
          <Row gutter={16}>
            <Col xs={24} md={8}>
              <Form.Item label="后端" name="backend" rules={[{ required: true }]}>
                <Select onChange={handleBackendChange}>
                  {BACKEND_OPTIONS.map((opt) => (
                    <Select.Option key={opt.value} value={opt.value}>
                      {opt.label} - {opt.desc}
                    </Select.Option>
                  ))}
                </Select>
              </Form.Item>
            </Col>
            <Col xs={24} md={6}>
              <Form.Item label="距离度量" name="metric" rules={[{ required: true }]}>
                <Select>
                  {METRIC_OPTIONS.map((m) => (
                    <Select.Option key={m} value={m}>
                      {m}
                    </Select.Option>
                  ))}
                </Select>
              </Form.Item>
            </Col>
          </Row>

          {paramDefs.length === 0 ? (
            <Alert type="info" message="该后端无需额外参数" showIcon />
          ) : (
            <Row gutter={16}>
              {paramDefs.map((def) => (
                <Col xs={24} sm={12} md={8} key={def.key}>
                  <Form.Item
                    label={def.label}
                    name={['params', def.key]}
                    rules={[{ required: true, message: '必填' }]}
                  >
                    <InputNumber
                      min={def.min}
                      max={def.max}
                      step={def.step ?? 1}
                      style={{ width: '100%' }}
                    />
                  </Form.Item>
                </Col>
              ))}
            </Row>
          )}

          <Space style={{ marginTop: 8 }}>
            <Button type="primary" loading={submitting} onClick={handleSubmit}>
              开始构建
            </Button>
            <Button onClick={() => fetchIndexes()} disabled={loading}>
              刷新列表
            </Button>
          </Space>
        </Form>
      </Card>

      <Card
        title={`索引列表（${indexes.length}）`}
        extra={
          <Button icon={<ReloadOutlined />} onClick={() => fetchIndexes()} loading={loading}>
            刷新
          </Button>
        }
      >
        {loading && indexes.length === 0 ? (
          <Skeleton active paragraph={{ rows: 5 }} />
        ) : indexes.length === 0 ? (
          <Empty description="暂无索引，请在上方表单构建" />
        ) : (
          <Table<IndexRecord>
            rowKey="id"
            columns={columns}
            dataSource={indexes}
            pagination={{ pageSize: 10 }}
            locale={{
              emptyText: loading ? (
                <Skeleton active paragraph={{ rows: 5 }} />
              ) : (
                <Empty description="暂无索引，请在上方表单构建" />
              ),
            }}
            onRow={(record) => ({
              onClick: () => navigate(`/indexes/${record.id}`),
              style: { cursor: 'pointer' },
            })}
            rowClassName={(record) =>
              currentIndex?.id === record.id ? 'ant-table-row-selected' : ''
            }
          />
        )}
      </Card>
    </div>
  );
};

export default IndexManagePage;
