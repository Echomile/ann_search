import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Form,
  InputNumber,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { ExperimentOutlined, ReloadOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';
import { datasetsApi } from '@/api/datasets';
import { indexesApi } from '@/api/indexes';
import { evaluationApi } from '@/api/evaluation';
import type { Dataset } from '@/types/dataset';
import type { IndexRecord } from '@/types/indexRecord';
import type {
  BenchmarkResult,
  BenchmarkSummary,
  DatasetStat,
  SearchStats,
} from '@/types/evaluation';
import { useDatasetStore } from '@/store/datasetStore';
import { formatDateTime, formatMemoryMb, formatSeconds } from '@/utils/format';
import { extractError } from '@/utils/error';
import PlotlyChart, { type PlotlyData } from '@/components/PlotlyChart';

const { Title, Paragraph, Text } = Typography;

interface FormValues {
  dataset_id: number;
  index_id: number;
  num_queries: number;
  top_k_list: number[];
  concurrency_list: number[];
}

const DEFAULT_TOP_K = [10, 100];
const DEFAULT_CONCURRENCY = [1, 4, 8, 16];

const EvaluationPage = () => {
  const currentDataset = useDatasetStore((s) => s.currentDataset);
  const currentIndex = useDatasetStore((s) => s.currentIndex);

  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [indexes, setIndexes] = useState<IndexRecord[]>([]);
  const [history, setHistory] = useState<BenchmarkSummary[]>([]);
  const [selectedResult, setSelectedResult] = useState<BenchmarkResult | null>(null);
  const [loadingResult, setLoadingResult] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [searchStats, setSearchStats] = useState<SearchStats | null>(null);
  const [statsLoading, setStatsLoading] = useState(false);
  const [form] = Form.useForm<FormValues>();
  const watchedDatasetId = Form.useWatch('dataset_id', form);

  const loadDatasets = useCallback(async () => {
    try {
      const list = await datasetsApi.list();
      setDatasets(list.filter((d) => d.status === 'ready'));
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

  const loadHistory = useCallback(async () => {
    setRefreshing(true);
    try {
      const list = await evaluationApi.list();
      setHistory(list);
    } catch (err) {
      message.error(extractError(err));
    } finally {
      setRefreshing(false);
    }
  }, []);

  const loadResult = useCallback(async (indexId: number) => {
    setLoadingResult(true);
    try {
      const r = await evaluationApi.latest(indexId);
      setSelectedResult(r);
    } catch (err) {
      setSelectedResult(null);
      message.error(extractError(err));
    } finally {
      setLoadingResult(false);
    }
  }, []);

  const loadSearchStats = useCallback(async (datasetId?: number) => {
    setStatsLoading(true);
    try {
      const stats = await evaluationApi.searchStats(datasetId);
      setSearchStats(stats);
    } catch (err) {
      message.error(extractError(err));
    } finally {
      setStatsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadDatasets();
    void loadHistory();
    void loadSearchStats();
  }, [loadDatasets, loadHistory, loadSearchStats]);

  useEffect(() => {
    if (watchedDatasetId !== undefined && watchedDatasetId !== null) {
      void loadSearchStats(watchedDatasetId);
    }
  }, [watchedDatasetId, loadSearchStats]);

  useEffect(() => {
    if (!currentDataset) return;
    form.setFieldsValue({
      dataset_id: currentDataset.id,
      index_id: currentIndex?.id ?? undefined,
      num_queries: 100,
      top_k_list: DEFAULT_TOP_K,
      concurrency_list: DEFAULT_CONCURRENCY,
    });
    void loadIndexes(currentDataset.id);
  }, [currentDataset, currentIndex, form, loadIndexes]);

  useEffect(() => {
    if (watchedDatasetId) void loadIndexes(watchedDatasetId);
  }, [watchedDatasetId, loadIndexes]);

  const datasetOptions = useMemo(
    () => datasets.map((d) => ({ label: `${d.name} (#${d.id})`, value: d.id })),
    [datasets],
  );
  const indexOptions = useMemo(
    () => indexes.map((i) => ({ label: `#${i.id} · ${i.backend} · ${i.metric}`, value: i.id })),
    [indexes],
  );

  const handleSubmit = async () => {
    let v: FormValues;
    try {
      v = await form.validateFields();
    } catch {
      return;
    }
    setSubmitting(true);
    try {
      const resp = await evaluationApi.run({
        index_id: v.index_id,
        num_queries: v.num_queries,
        top_k_list: v.top_k_list,
        concurrency_list: v.concurrency_list,
      });
      message.success(
        resp.status === 'completed'
          ? `评测已同步完成，task=${resp.task_id}`
          : `评测已入队，task=${resp.task_id}`,
      );
      if (resp.status === 'completed') {
        await loadResult(v.index_id);
      }
      await loadHistory();
    } catch (err) {
      message.error(extractError(err));
    } finally {
      setSubmitting(false);
    }
  };

  const summaryColumns: ColumnsType<BenchmarkSummary> = [
    { title: '索引 ID', dataIndex: 'index_id', key: 'index_id', width: 100 },
    {
      title: '数据集',
      dataIndex: 'dataset_id',
      key: 'dataset_id',
      width: 110,
      render: (v: number | null) => (v == null ? '-' : `#${v}`),
    },
    {
      title: '后端',
      dataIndex: 'backend',
      key: 'backend',
      width: 130,
      render: (v: string) => <Tag color="blue">{v}</Tag>,
    },
    {
      title: 'Recall',
      dataIndex: 'recalls',
      key: 'recalls',
      render: (recalls: Record<string, number>) => (
        <Space size={[4, 4]} wrap>
          {Object.entries(recalls).map(([k, v]) => (
            <Tag key={k}>{`R@${k}=${(v * 100).toFixed(1)}%`}</Tag>
          ))}
        </Space>
      ),
    },
    {
      title: '完成时间',
      dataIndex: 'finished_at',
      key: 'finished_at',
      width: 180,
      render: (v: string | null) => formatDateTime(v),
    },
    {
      title: '操作',
      key: 'actions',
      width: 120,
      render: (_: unknown, record) => (
        <Button size="small" onClick={() => loadResult(record.index_id)}>
          查看详情
        </Button>
      ),
    },
  ];

  const latencyTraces = useMemo<PlotlyData>(() => {
    if (!selectedResult) return [];
    const xs = selectedResult.latencies.map((l) => l.concurrency);
    return [
      {
        x: xs,
        y: selectedResult.latencies.map((l) => l.p50_ms),
        mode: 'lines+markers',
        type: 'scatter',
        name: 'P50',
        line: { color: '#52c41a' },
      },
      {
        x: xs,
        y: selectedResult.latencies.map((l) => l.p95_ms),
        mode: 'lines+markers',
        type: 'scatter',
        name: 'P95',
        line: { color: '#faad14' },
      },
      {
        x: xs,
        y: selectedResult.latencies.map((l) => l.p99_ms),
        mode: 'lines+markers',
        type: 'scatter',
        name: 'P99',
        line: { color: '#f5222d' },
      },
    ];
  }, [selectedResult]);

  const qpsTraces = useMemo<PlotlyData>(() => {
    if (!selectedResult) return [];
    return [
      {
        x: selectedResult.latencies.map((l) => String(l.concurrency)),
        y: selectedResult.latencies.map((l) => l.qps),
        type: 'bar',
        name: 'QPS',
        marker: { color: '#1677ff' },
      },
    ];
  }, [selectedResult]);

  const hourlyTraces = useMemo<PlotlyData>(() => {
    if (!searchStats) return [];
    const xs = searchStats.hourly_24h.map((b) => b.hour_iso);
    return [
      {
        x: xs,
        y: searchStats.hourly_24h.map((b) => b.queries),
        type: 'bar',
        name: '查询数',
        marker: { color: '#1677ff' },
        yaxis: 'y',
      },
      {
        x: xs,
        y: searchStats.hourly_24h.map((b) => b.avg_latency_ms),
        type: 'scatter',
        mode: 'lines+markers',
        name: '平均延迟 (ms)',
        line: { color: '#fa8c16' },
        yaxis: 'y2',
      },
    ];
  }, [searchStats]);

  const statsColumns: ColumnsType<DatasetStat> = [
    {
      title: '数据集',
      key: 'dataset',
      render: (_: unknown, record) =>
        record.dataset_name
          ? `${record.dataset_name} (#${record.dataset_id})`
          : `#${record.dataset_id}`,
    },
    {
      title: '总查询数',
      dataIndex: 'total_queries',
      key: 'total_queries',
      width: 120,
      sorter: (a, b) => a.total_queries - b.total_queries,
    },
    {
      title: '平均延迟',
      dataIndex: 'avg_latency_ms',
      key: 'avg_latency_ms',
      width: 140,
      render: (v: number) => `${v.toFixed(2)} ms`,
      sorter: (a, b) => a.avg_latency_ms - b.avg_latency_ms,
    },
    {
      title: 'P95 延迟',
      dataIndex: 'p95_latency_ms',
      key: 'p95_latency_ms',
      width: 140,
      render: (v: number) => `${v.toFixed(2)} ms`,
      sorter: (a, b) => a.p95_latency_ms - b.p95_latency_ms,
    },
  ];

  return (
    <div>
      <Title level={3}>性能评测</Title>
      <Paragraph type="secondary">
        对指定 ANN 索引执行 Recall / 延迟分位 / QPS 评测，结果异步落盘。
        {currentIndex == null && (
          <Text type="secondary" style={{ marginLeft: 12 }}>
            可前往 <Link to="/indexes">索引管理</Link> 选用索引以预填。
          </Text>
        )}
      </Paragraph>

      <Card title="发起评测" style={{ marginBottom: 24 }}>
        <Form form={form} layout="vertical">
          <Row gutter={16}>
            <Col xs={24} md={6}>
              <Form.Item label="数据集" name="dataset_id" rules={[{ required: true }]}>
                <Select
                  options={datasetOptions}
                  placeholder="选择数据集"
                  showSearch
                  optionFilterProp="label"
                />
              </Form.Item>
            </Col>
            <Col xs={24} md={6}>
              <Form.Item label="索引" name="index_id" rules={[{ required: true }]}>
                <Select options={indexOptions} placeholder="选择 ready 索引" />
              </Form.Item>
            </Col>
            <Col xs={24} md={4}>
              <Form.Item label="num_queries" name="num_queries" rules={[{ required: true }]}>
                <InputNumber min={1} max={10000} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col xs={24} md={4}>
              <Form.Item label="top_k 列表" name="top_k_list" rules={[{ required: true }]}>
                <Select
                  mode="tags"
                  tokenSeparators={[',', ' ']}
                  placeholder="如 10,100"
                  onChange={(vals: (string | number)[]) =>
                    form.setFieldValue(
                      'top_k_list',
                      vals.map((v) => Number(v)).filter((n) => !Number.isNaN(n)),
                    )
                  }
                />
              </Form.Item>
            </Col>
            <Col xs={24} md={4}>
              <Form.Item label="并发列表" name="concurrency_list" rules={[{ required: true }]}>
                <Select
                  mode="tags"
                  tokenSeparators={[',', ' ']}
                  placeholder="如 1,4,8,16"
                  onChange={(vals: (string | number)[]) =>
                    form.setFieldValue(
                      'concurrency_list',
                      vals.map((v) => Number(v)).filter((n) => !Number.isNaN(n)),
                    )
                  }
                />
              </Form.Item>
            </Col>
          </Row>
          <Space>
            <Button
              type="primary"
              icon={<ExperimentOutlined />}
              loading={submitting}
              onClick={handleSubmit}
            >
              运行评测
            </Button>
            <Button onClick={() => loadHistory()} icon={<ReloadOutlined />} loading={refreshing}>
              刷新历史
            </Button>
          </Space>
        </Form>
      </Card>

      <Card title="历史评测结果" style={{ marginBottom: 24 }}>
        {history.length === 0 ? (
          <Empty description="尚无历史评测" />
        ) : (
          <Table<BenchmarkSummary>
            rowKey={(r) => `${r.index_id}-${r.finished_at ?? ''}`}
            columns={summaryColumns}
            dataSource={history}
            pagination={{ pageSize: 8 }}
            loading={refreshing}
            rowClassName={(record) =>
              selectedResult?.index_id === record.index_id ? 'ant-table-row-selected' : ''
            }
          />
        )}
      </Card>

      <Card title="评测详情" loading={loadingResult}>
        {selectedResult === null ? (
          <Alert
            type="info"
            showIcon
            message="尚未选择评测结果"
            description="可在上方历史结果点击「查看详情」，或运行新评测后自动展示。"
          />
        ) : (
          <>
            <Row gutter={16} style={{ marginBottom: 24 }}>
              <Col xs={12} md={6}>
                <Statistic
                  title="构建耗时"
                  value={formatSeconds(selectedResult.build_time_seconds)}
                />
              </Col>
              <Col xs={12} md={6}>
                <Statistic title="内存占用" value={formatMemoryMb(selectedResult.memory_mb)} />
              </Col>
              {Object.entries(selectedResult.recalls).map(([k, v]) => (
                <Col xs={12} md={6} key={k}>
                  <Statistic title={`Recall@${k}`} value={(v * 100).toFixed(2)} suffix="%" />
                </Col>
              ))}
            </Row>

            <Row gutter={16}>
              <Col xs={24} lg={12}>
                <Card type="inner" title="并发 vs 延迟（ms）">
                  {selectedResult.latencies.length === 0 ? (
                    <Empty description="无延迟数据" />
                  ) : (
                    <PlotlyChart
                      data={latencyTraces}
                      layout={{
                        xaxis: { title: { text: 'concurrency' } },
                        yaxis: { title: { text: 'latency (ms)' } },
                      }}
                      height={320}
                    />
                  )}
                </Card>
              </Col>
              <Col xs={24} lg={12}>
                <Card type="inner" title="并发 vs QPS">
                  {selectedResult.latencies.length === 0 ? (
                    <Empty description="无 QPS 数据" />
                  ) : (
                    <PlotlyChart
                      data={qpsTraces}
                      layout={{
                        xaxis: { title: { text: 'concurrency' }, type: 'category' },
                        yaxis: { title: { text: 'QPS' } },
                      }}
                      height={320}
                    />
                  )}
                </Card>
              </Col>
            </Row>
          </>
        )}
      </Card>

      <Card
        title="检索日志统计"
        loading={statsLoading}
        style={{ marginTop: 24 }}
        extra={
          <Button
            icon={<ReloadOutlined />}
            onClick={() => loadSearchStats(watchedDatasetId)}
            loading={statsLoading}
          >
            刷新
          </Button>
        }
      >
        {searchStats === null ? (
          <Alert
            type="info"
            showIcon
            message="尚无统计数据"
            description="完成一次相似检索后此处将自动汇总历史日志。"
          />
        ) : (
          <>
            <Row gutter={16} style={{ marginBottom: 24 }}>
              <Col xs={12} md={6}>
                <Statistic title="总查询数" value={searchStats.total_queries} />
              </Col>
              <Col xs={12} md={6}>
                <Statistic
                  title="平均延迟"
                  value={searchStats.overall_avg_latency_ms.toFixed(2)}
                  suffix="ms"
                />
              </Col>
              <Col xs={12} md={6}>
                <Statistic
                  title="P95 延迟"
                  value={searchStats.overall_p95_latency_ms.toFixed(2)}
                  suffix="ms"
                />
              </Col>
              <Col xs={12} md={6}>
                <Statistic title="数据集数" value={searchStats.by_dataset.length} />
              </Col>
            </Row>

            <Card type="inner" title="最近 24h 查询量与延迟" style={{ marginBottom: 16 }}>
              {searchStats.total_queries === 0 ? (
                <Empty description="近 24h 无查询" />
              ) : (
                <PlotlyChart
                  data={hourlyTraces}
                  layout={{
                    xaxis: { title: { text: 'hour (UTC)' }, type: 'date' },
                    yaxis: { title: { text: '查询数' }, rangemode: 'tozero' },
                    yaxis2: {
                      title: { text: '平均延迟 (ms)' },
                      overlaying: 'y',
                      side: 'right',
                      rangemode: 'tozero',
                    },
                    barmode: 'group',
                  }}
                  height={320}
                />
              )}
            </Card>

            <Card type="inner" title="按数据集聚合">
              {searchStats.by_dataset.length === 0 ? (
                <Empty description="尚无任一数据集的查询日志" />
              ) : (
                <Table<DatasetStat>
                  rowKey={(r) => String(r.dataset_id)}
                  columns={statsColumns}
                  dataSource={searchStats.by_dataset}
                  pagination={false}
                  size="small"
                />
              )}
            </Card>
          </>
        )}
      </Card>
    </div>
  );
};

export default EvaluationPage;
