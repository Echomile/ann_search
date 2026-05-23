import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  Alert,
  Breadcrumb,
  Button,
  Card,
  Descriptions,
  Popconfirm,
  Skeleton,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  CheckCircleOutlined,
  DashboardOutlined,
  DeleteOutlined,
  SearchOutlined,
} from '@ant-design/icons';
import { indexesApi } from '@/api/indexes';
import { evaluationApi } from '@/api/evaluation';
import type { IndexRecord } from '@/types/indexRecord';
import type { BenchmarkResult, LatencyStats } from '@/types/evaluation';
import { useDatasetStore } from '@/store/datasetStore';
import {
  formatDateTime,
  formatMemoryMb,
  formatSeconds,
  indexStatusColor,
} from '@/utils/format';
import { extractError } from '@/utils/error';

const { Title, Paragraph, Text } = Typography;

/**
 * 索引详情页：聚合展示单个索引的元数据、操作入口与最近一次评测结果。
 * 路由：`/indexes/:id`
 */
const IndexDetailPage = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const indexId = Number(id);
  const currentIndex = useDatasetStore((s) => s.currentIndex);
  const setCurrentIndex = useDatasetStore((s) => s.setCurrentIndex);

  const [record, setRecord] = useState<IndexRecord | null>(null);
  const [loading, setLoading] = useState(false);
  const [evalResult, setEvalResult] = useState<BenchmarkResult | null>(null);
  const [evalLoading, setEvalLoading] = useState(false);

  const fetchDetail = useCallback(async () => {
    if (!Number.isFinite(indexId) || indexId <= 0) return;
    setLoading(true);
    try {
      const data = await indexesApi.get(indexId);
      setRecord(data);
    } catch (err) {
      message.error(extractError(err));
      setRecord(null);
    } finally {
      setLoading(false);
    }
  }, [indexId]);

  const fetchEvaluation = useCallback(async () => {
    if (!Number.isFinite(indexId) || indexId <= 0) return;
    setEvalLoading(true);
    try {
      const data = await evaluationApi.latest(indexId);
      setEvalResult(data);
    } catch {
      // 无评测记录或权限不足时静默忽略
      setEvalResult(null);
    } finally {
      setEvalLoading(false);
    }
  }, [indexId]);

  useEffect(() => {
    void fetchDetail();
  }, [fetchDetail]);

  useEffect(() => {
    if (record?.status === 'ready') void fetchEvaluation();
  }, [record?.status, fetchEvaluation]);

  const paramsJson = useMemo(() => {
    if (!record?.params || Object.keys(record.params).length === 0) return '{}';
    return JSON.stringify(record.params, null, 2);
  }, [record?.params]);

  const handleGoSearch = () => {
    if (!record) return;
    if (record.status === 'ready') setCurrentIndex(record);
    navigate(`/search?index_id=${record.id}`);
  };

  const handleGoEvaluation = () => {
    if (!record) return;
    if (record.status === 'ready') setCurrentIndex(record);
    navigate(`/evaluation?index_id=${record.id}`);
  };

  const handleDelete = async () => {
    if (!record) return;
    try {
      await indexesApi.remove(record.id);
      message.success(`索引 #${record.id} 已删除`);
      if (currentIndex?.id === record.id) setCurrentIndex(null);
      navigate('/indexes');
    } catch (err) {
      message.error(extractError(err));
    }
  };

  if (!Number.isFinite(indexId) || indexId <= 0) {
    return (
      <div>
        <Title level={3}>索引详情</Title>
        <Alert type="error" showIcon message="非法的索引 ID" />
      </div>
    );
  }

  if (loading && !record) {
    return (
      <div>
        <Title level={3}>索引详情</Title>
        <Card>
          <Skeleton active paragraph={{ rows: 6 }} />
        </Card>
      </div>
    );
  }

  if (!record) {
    return (
      <div>
        <Title level={3}>索引详情</Title>
        <Alert
          type="warning"
          showIcon
          message="未找到该索引"
          description={
            <>
              该索引可能已被删除，请返回
              <Link to="/indexes"> 索引管理 </Link>查看列表。
            </>
          }
        />
      </div>
    );
  }

  const isReady = record.status === 'ready';

  const latencyColumns: ColumnsType<LatencyStats> = [
    { title: '并发', dataIndex: 'concurrency', key: 'concurrency', width: 90 },
    {
      title: 'QPS',
      dataIndex: 'qps',
      key: 'qps',
      width: 120,
      render: (v: number) => v.toFixed(2),
    },
    {
      title: 'P50 (ms)',
      dataIndex: 'p50_ms',
      key: 'p50_ms',
      width: 120,
      render: (v: number) => v.toFixed(2),
    },
    {
      title: 'P95 (ms)',
      dataIndex: 'p95_ms',
      key: 'p95_ms',
      width: 120,
      render: (v: number) => v.toFixed(2),
    },
    {
      title: 'P99 (ms)',
      dataIndex: 'p99_ms',
      key: 'p99_ms',
      width: 120,
      render: (v: number) => v.toFixed(2),
    },
  ];

  return (
    <div>
      <Breadcrumb
        style={{ marginBottom: 16 }}
        items={[
          { title: <Link to="/">首页</Link> },
          { title: <Link to="/indexes">索引管理</Link> },
          { title: `索引 #${record.id}` },
        ]}
      />

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <Title level={3} style={{ marginBottom: 8 }}>
          索引 #{record.id}
          <Tag color={indexStatusColor(record.status)} style={{ marginLeft: 12 }}>
            {record.status}
          </Tag>
        </Title>
        <Space>
          <Button icon={<SearchOutlined />} onClick={handleGoSearch} disabled={!isReady}>
            去检索
          </Button>
          <Button icon={<DashboardOutlined />} onClick={handleGoEvaluation} disabled={!isReady}>
            跑评测
          </Button>
          <Button
            type={currentIndex?.id === record.id ? 'primary' : 'default'}
            icon={<CheckCircleOutlined />}
            disabled={!isReady}
            onClick={() => {
              setCurrentIndex(record);
              message.success(`已选中索引 #${record.id}`);
            }}
          >
            {currentIndex?.id === record.id ? '已选用' : '选用为当前索引'}
          </Button>
          <Popconfirm
            title={`删除索引 #${record.id}？`}
            okType="danger"
            onConfirm={handleDelete}
          >
            <Button danger icon={<DeleteOutlined />}>
              删除
            </Button>
          </Popconfirm>
        </Space>
      </div>

      {!isReady && (
        <Alert
          type={record.status === 'failed' ? 'error' : 'info'}
          showIcon
          style={{ marginBottom: 16 }}
          message={record.status === 'failed' ? '索引构建失败' : '索引建设中'}
          description={
            record.status === 'failed'
              ? '请检查后端日志或删除该索引后重新创建。'
              : '请耐心等待，构建完成后即可去检索或跑评测。本页会在你刷新时同步最新状态。'
          }
        />
      )}

      <Card title="基础信息" style={{ marginBottom: 24 }} loading={loading}>
        <Descriptions column={{ xs: 1, sm: 2, md: 3 }} bordered size="small">
          <Descriptions.Item label="ID">{record.id}</Descriptions.Item>
          <Descriptions.Item label="后端">
            <Tag color="blue">{record.backend}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="距离度量">
            <Tag>{record.metric}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="状态">
            <Tag color={indexStatusColor(record.status)}>{record.status}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="构建耗时">
            {formatSeconds(record.build_time_seconds)}
          </Descriptions.Item>
          <Descriptions.Item label="内存占用">{formatMemoryMb(record.memory_mb)}</Descriptions.Item>
          <Descriptions.Item label="所属数据集">#{record.dataset_id}</Descriptions.Item>
          <Descriptions.Item label="创建时间">{formatDateTime(record.created_at)}</Descriptions.Item>
          <Descriptions.Item label="索引文件路径">
            {record.index_path ? (
              <Text code style={{ fontSize: 12 }}>
                {record.index_path}
              </Text>
            ) : (
              <Text type="secondary">-</Text>
            )}
          </Descriptions.Item>
        </Descriptions>

        <Paragraph style={{ marginTop: 16, marginBottom: 0 }}>
          <Text strong>参数：</Text>
        </Paragraph>
        <pre
          style={{
            background: '#fafafa',
            padding: 12,
            borderRadius: 6,
            margin: 0,
            fontSize: 12,
            overflowX: 'auto',
          }}
        >
          {paramsJson}
        </pre>
      </Card>

      {isReady && (
        <Card title="最近一次评测" loading={evalLoading}>
          {!evalResult ? (
            <Alert
              type="info"
              showIcon
              message="尚无评测结果"
              description={
                <>
                  可点击右上角
                  <Text strong> 跑评测 </Text>
                  按钮，跳转到性能评测页发起一次基准测试。
                </>
              }
            />
          ) : (
            <>
              <Descriptions column={{ xs: 1, sm: 2, md: 4 }} size="small" style={{ marginBottom: 16 }}>
                {Object.entries(evalResult.recalls).map(([k, v]) => (
                  <Descriptions.Item key={k} label={`Recall@${k}`}>
                    <Text strong>{(v * 100).toFixed(2)}%</Text>
                  </Descriptions.Item>
                ))}
                <Descriptions.Item label="完成时间">
                  {formatDateTime(evalResult.finished_at)}
                </Descriptions.Item>
              </Descriptions>

              {evalResult.latencies.length > 0 && (
                <Table<LatencyStats>
                  rowKey={(r) => `${r.concurrency}`}
                  columns={latencyColumns}
                  dataSource={evalResult.latencies}
                  size="small"
                  pagination={false}
                />
              )}
            </>
          )}
        </Card>
      )}
    </div>
  );
};

export default IndexDetailPage;
