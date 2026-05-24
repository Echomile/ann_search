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
  Slider,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { ExperimentOutlined, ReloadOutlined } from '@ant-design/icons';
import { datasetsApi } from '@/api/datasets';
import { indexesApi } from '@/api/indexes';
import { evaluationApi } from '@/api/evaluation';
import { searchApi } from '@/api/search';
import PlotlyChart, {
  type PlotlyClickEvent,
  type PlotlyData,
} from '@/components/PlotlyChart';
import type { Dataset } from '@/types/dataset';
import type { IndexRecord } from '@/types/indexRecord';
import type { SweepPoint, SweepRun } from '@/types/evaluation';
import type { SearchHit, SearchResponseWithParams } from '@/types/search';
import { extractError } from '@/utils/error';

const { Text } = Typography;

// 与后端 backend 名称对齐
const SWEEPABLE_BACKENDS = [
  { value: 'hnswlib', label: 'hnswlib', param: 'ef_search' },
  { value: 'faiss-hnsw', label: 'faiss-hnsw', param: 'ef_search' },
  { value: 'adaptive-hnsw', label: 'adaptive-hnsw', param: 'ef_search' },
  { value: 'faiss-ivfpq', label: 'faiss-ivfpq', param: 'nprobe' },
  { value: 'brute', label: 'brute', param: '—' },
];

const DEFAULT_EF_GRID = [16, 32, 64, 128, 256, 512];
const DEFAULT_NPROBE_GRID = [4, 8, 16, 32, 64, 128];

// 按 backend 分组的 Plotly 颜色 (高对比度)
const BACKEND_COLORS: Record<string, string> = {
  hnswlib: '#1677ff',
  'faiss-hnsw': '#52c41a',
  'faiss-ivfpq': '#fa8c16',
  'adaptive-hnsw': '#722ed1',
  brute: '#8c8c8c',
};

interface SweepFormValues {
  dataset_id: number;
  backends: string[];
  top_k: number;
  query_count: number;
}

interface SweepTabProps {
  defaultDatasetId?: number;
}

const SweepTab = ({ defaultDatasetId }: SweepTabProps) => {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [indexes, setIndexes] = useState<IndexRecord[]>([]);
  const [sweep, setSweep] = useState<SweepRun | null>(null);
  const [sweepLoading, setSweepLoading] = useState(false);
  const [selectedPoint, setSelectedPoint] = useState<SweepPoint | null>(null);

  // D1 交互层：滑块当前值 + 实时预览
  const [previewEf, setPreviewEf] = useState<number>(64);
  const [previewNprobe, setPreviewNprobe] = useState<number>(16);
  const [previewBackend, setPreviewBackend] = useState<string>('hnswlib');
  const [previewIndexId, setPreviewIndexId] = useState<number | null>(null);
  const [previewCellId, setPreviewCellId] = useState<string>('');
  const [previewResp, setPreviewResp] = useState<SearchResponseWithParams | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [livePreview, setLivePreview] = useState<boolean>(true);

  const [form] = Form.useForm<SweepFormValues>();
  const previewTimerRef = useRef<number | null>(null);

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

  useEffect(() => {
    void loadDatasets();
  }, [loadDatasets]);

  useEffect(() => {
    if (defaultDatasetId) {
      form.setFieldValue('dataset_id', defaultDatasetId);
      void loadIndexes(defaultDatasetId);
    }
  }, [defaultDatasetId, form, loadIndexes]);

  const watchedDatasetId = Form.useWatch('dataset_id', form);
  useEffect(() => {
    if (watchedDatasetId) void loadIndexes(watchedDatasetId);
  }, [watchedDatasetId, loadIndexes]);

  // 触发参数扫描
  const handleSweep = async () => {
    let v: SweepFormValues;
    try {
      v = await form.validateFields();
    } catch {
      return;
    }
    setSweepLoading(true);
    setSelectedPoint(null);
    try {
      const run = await evaluationApi.triggerSweep({
        dataset_id: v.dataset_id,
        backends: v.backends,
        top_k: v.top_k,
        query_count: v.query_count,
        ef_search_grid: DEFAULT_EF_GRID,
        nprobe_grid: DEFAULT_NPROBE_GRID,
      });
      setSweep(run);
      message.success(
        `扫描完成，共 ${run.points.length} 个数据点，其中 ${
          run.points.filter((p) => p.on_pareto).length
        } 个在帕累托前沿`,
      );
    } catch (err) {
      message.error(extractError(err));
    } finally {
      setSweepLoading(false);
    }
  };

  // 帕累托散点图数据组装：按 backend 分 trace，前沿点放大 + 连线
  const scatterData = useMemo<PlotlyData>(() => {
    if (!sweep || sweep.points.length === 0) return [];

    const byBackend = new Map<string, SweepPoint[]>();
    for (const p of sweep.points) {
      if (!byBackend.has(p.backend)) byBackend.set(p.backend, []);
      byBackend.get(p.backend)!.push(p);
    }

    const traces: PlotlyData = [];

    // 帕累托前沿连线（按 recall 升序）
    const paretoPoints = [...sweep.points].filter((p) => p.on_pareto).sort((a, b) => a.recall - b.recall);
    if (paretoPoints.length > 1) {
      traces.push({
        x: paretoPoints.map((p) => p.recall),
        y: paretoPoints.map((p) => p.qps),
        mode: 'lines',
        type: 'scatter',
        name: '帕累托前沿',
        line: { color: '#ff4d4f', width: 2, dash: 'dash' },
        hoverinfo: 'skip',
        showlegend: true,
      });
    }

    for (const [backend, points] of byBackend.entries()) {
      const color = BACKEND_COLORS[backend] ?? '#999';
      const paretoMask = points.map((p) => p.on_pareto);
      traces.push({
        x: points.map((p) => p.recall),
        y: points.map((p) => p.qps),
        mode: 'markers',
        type: 'scatter',
        name: backend,
        marker: {
          color,
          size: paretoMask.map((on) => (on ? 14 : 9)),
          symbol: paretoMask.map((on) => (on ? 'star' : 'circle')),
          line: { width: paretoMask.map((on) => (on ? 1.5 : 0.5)), color: '#fff' },
        },
        customdata: points.map((p) => p.id),
        text: points.map(
          (p) =>
            `<b>${p.backend}</b><br>params=${JSON.stringify(p.params_json)}<br>` +
            `recall=${p.recall.toFixed(4)}<br>QPS=${p.qps.toFixed(1)}<br>` +
            `p50=${p.p50_ms.toFixed(3)}ms · p95=${p.p95_ms.toFixed(3)}ms<br>` +
            `mem=${p.mem_mb.toFixed(2)}MB${p.on_pareto ? '<br><b>on pareto</b>' : ''}`,
        ),
        hovertemplate: '%{text}<extra></extra>',
      });
    }

    return traces;
  }, [sweep]);

  // 散点点击：反查到选中点并联动滑块
  const handleScatterClick = useCallback(
    (e: PlotlyClickEvent) => {
      if (!sweep) return;
      const p = e.points?.[0];
      if (!p) return;
      const pointId = (p as { customdata?: number }).customdata;
      if (typeof pointId !== 'number') return;
      const matched = sweep.points.find((sp) => sp.id === pointId);
      if (!matched) return;
      setSelectedPoint(matched);
      setPreviewBackend(matched.backend);
      const params = matched.params_json;
      if (typeof params.ef_search === 'number') setPreviewEf(params.ef_search);
      if (typeof params.nprobe === 'number') setPreviewNprobe(params.nprobe);
    },
    [sweep],
  );

  // D1: 实时预览 search/with_params（滑块/cell_id 变化触发 debounce）
  const triggerPreview = useCallback(() => {
    if (!livePreview) return;
    if (!previewCellId.trim()) return;
    if (!watchedDatasetId) return;
    if (previewTimerRef.current !== null) {
      window.clearTimeout(previewTimerRef.current);
    }
    previewTimerRef.current = window.setTimeout(async () => {
      setPreviewLoading(true);
      try {
        const runtimeParams: Record<string, number> = {};
        if (['hnswlib', 'faiss-hnsw', 'adaptive-hnsw'].includes(previewBackend)) {
          runtimeParams.ef_search = previewEf;
        }
        if (previewBackend === 'faiss-ivfpq') {
          runtimeParams.nprobe = previewNprobe;
        }
        const resp = await searchApi.withParams({
          dataset_id: watchedDatasetId,
          index_id: previewIndexId,
          cell_id: previewCellId.trim(),
          top_k: form.getFieldValue('top_k') ?? 10,
          runtime_params: runtimeParams,
        });
        setPreviewResp(resp);
      } catch (err) {
        message.error(extractError(err));
        setPreviewResp(null);
      } finally {
        setPreviewLoading(false);
      }
    }, 200);
  }, [
    livePreview,
    previewCellId,
    previewBackend,
    previewEf,
    previewNprobe,
    previewIndexId,
    watchedDatasetId,
    form,
  ]);

  useEffect(() => {
    triggerPreview();
    return () => {
      if (previewTimerRef.current !== null) {
        window.clearTimeout(previewTimerRef.current);
      }
    };
  }, [triggerPreview]);

  const datasetOptions = useMemo(
    () => datasets.map((d) => ({ label: `${d.name} (#${d.id})`, value: d.id })),
    [datasets],
  );

  const indexOptionsByBackend = useMemo(
    () =>
      indexes
        .filter((i) => i.backend === previewBackend)
        .map((i) => ({ label: `#${i.id} · ${i.backend} · ${i.metric}`, value: i.id })),
    [indexes, previewBackend],
  );

  const hitColumns: ColumnsType<SearchHit> = [
    { title: '#', dataIndex: 'rank', key: 'rank', width: 50 },
    { title: 'cell_id', dataIndex: 'cell_id', key: 'cell_id', ellipsis: true },
    {
      title: 'distance',
      dataIndex: 'distance',
      key: 'distance',
      width: 110,
      render: (v: number) => v.toFixed(4),
    },
    {
      title: 'cell_type',
      key: 'cell_type',
      width: 160,
      render: (_: unknown, h) =>
        h.meta && typeof h.meta.cell_type === 'string' ? (h.meta.cell_type as string) : '-',
    },
  ];

  const showEfSlider = ['hnswlib', 'faiss-hnsw', 'adaptive-hnsw'].includes(previewBackend);
  const showNprobeSlider = previewBackend === 'faiss-ivfpq';

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Card title="参数扫描配置" size="small">
        <Form form={form} layout="vertical" initialValues={{ top_k: 10, query_count: 200 }}>
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
            <Col xs={24} md={10}>
              <Form.Item label="扫描 backend" name="backends" rules={[{ required: true }]}>
                <Select
                  mode="multiple"
                  placeholder="选择要扫描的后端 (默认全部 5 个)"
                  options={SWEEPABLE_BACKENDS.map((b) => ({
                    label: `${b.label} (扫 ${b.param})`,
                    value: b.value,
                  }))}
                  defaultValue={SWEEPABLE_BACKENDS.map((b) => b.value)}
                />
              </Form.Item>
            </Col>
            <Col xs={12} md={4}>
              <Form.Item label="query_count" name="query_count" rules={[{ required: true }]}>
                <InputNumber min={10} max={2000} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col xs={12} md={4}>
              <Form.Item label="top_k" name="top_k" rules={[{ required: true }]}>
                <InputNumber min={1} max={1000} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Space>
            <Button
              type="primary"
              icon={<ExperimentOutlined />}
              loading={sweepLoading}
              onClick={handleSweep}
            >
              跑参数扫描
            </Button>
            <Text type="secondary">
              估计耗时：单 backend × 单参数 ≈ 1-3 秒，全扫描 ≈ 20-60 秒
            </Text>
          </Space>
        </Form>
      </Card>

      <Card
        title="recall-QPS 帕累托散点 (点击散点 → 联动右侧滑块与实时 Top-K)"
        loading={sweepLoading}
      >
        {!sweep ? (
          <Empty
            description="尚无扫描结果，请先配置后点击「跑参数扫描」"
            style={{ padding: '60px 0' }}
          />
        ) : (
          <PlotlyChart
            data={scatterData}
            layout={{
              xaxis: {
                title: { text: `Recall@${sweep.top_k}` },
                range: [Math.max(0, Math.min(...sweep.points.map((p) => p.recall)) - 0.05), 1.02],
              },
              yaxis: {
                title: { text: 'QPS (concurrency=1)' },
                type: 'log',
              },
              hovermode: 'closest',
            }}
            height={420}
            onClick={handleScatterClick}
          />
        )}
      </Card>

      <Row gutter={16}>
        <Col xs={24} lg={8}>
          <Card title="参数调节 (D1 仪表盘)" size="small">
            <Form layout="vertical">
              <Form.Item label="目标 backend">
                <Select
                  value={previewBackend}
                  onChange={setPreviewBackend}
                  options={SWEEPABLE_BACKENDS.map((b) => ({ label: b.label, value: b.value }))}
                />
              </Form.Item>
              <Form.Item label={`索引 (${previewBackend})`}>
                <Select
                  value={previewIndexId}
                  onChange={setPreviewIndexId}
                  options={indexOptionsByBackend}
                  placeholder="自动选择最新 ready"
                  allowClear
                />
              </Form.Item>
              {showEfSlider && (
                <Form.Item label={`ef_search = ${previewEf}`}>
                  <Slider
                    min={8}
                    max={512}
                    step={8}
                    value={previewEf}
                    onChange={setPreviewEf}
                    marks={{ 16: '16', 64: '64', 128: '128', 256: '256', 512: '512' }}
                  />
                </Form.Item>
              )}
              {showNprobeSlider && (
                <Form.Item label={`nprobe = ${previewNprobe}`}>
                  <Slider
                    min={1}
                    max={256}
                    step={1}
                    value={previewNprobe}
                    onChange={setPreviewNprobe}
                    marks={{ 4: '4', 16: '16', 64: '64', 128: '128', 256: '256' }}
                  />
                </Form.Item>
              )}
              <Form.Item label="实时预览 Top-K">
                <Space>
                  <Switch checked={livePreview} onChange={setLivePreview} />
                  <Text type="secondary">滑块/cell_id 变化时自动跑检索（debounce 200ms）</Text>
                </Space>
              </Form.Item>
            </Form>
          </Card>
        </Col>

        <Col xs={24} lg={8}>
          <Card title="选中数据点详情" size="small">
            {!selectedPoint ? (
              <Alert
                type="info"
                showIcon
                message="点击散点上的任意点查看详情"
                description="点击后会自动把对应参数填入左侧滑块，右侧也会跑一次该参数下的 Top-K 预览"
              />
            ) : (
              <Descriptions size="small" column={1}>
                <Descriptions.Item label="backend">
                  <Tag color="blue">{selectedPoint.backend}</Tag>
                </Descriptions.Item>
                <Descriptions.Item label="params">
                  <code>{JSON.stringify(selectedPoint.params_json)}</code>
                </Descriptions.Item>
                <Descriptions.Item label="Recall">
                  {(selectedPoint.recall * 100).toFixed(2)}%
                </Descriptions.Item>
                <Descriptions.Item label="QPS">{selectedPoint.qps.toFixed(1)}</Descriptions.Item>
                <Descriptions.Item label="p50">
                  {selectedPoint.p50_ms.toFixed(3)} ms
                </Descriptions.Item>
                <Descriptions.Item label="p95">
                  {selectedPoint.p95_ms.toFixed(3)} ms
                </Descriptions.Item>
                <Descriptions.Item label="memory">
                  {selectedPoint.mem_mb.toFixed(2)} MB
                </Descriptions.Item>
                <Descriptions.Item label="on Pareto">
                  {selectedPoint.on_pareto ? (
                    <Tag color="success">是</Tag>
                  ) : (
                    <Tag color="default">否</Tag>
                  )}
                </Descriptions.Item>
              </Descriptions>
            )}
          </Card>
        </Col>

        <Col xs={24} lg={8}>
          <Card
            title="实时 Top-K 预览"
            size="small"
            extra={
              <Button
                size="small"
                icon={<ReloadOutlined />}
                onClick={triggerPreview}
                loading={previewLoading}
              >
                刷新
              </Button>
            }
          >
            <Space direction="vertical" style={{ width: '100%' }}>
              <Input
                placeholder="cell_id（输入后随滑块变化即时更新）"
                value={previewCellId}
                onChange={(e) => setPreviewCellId(e.target.value)}
                allowClear
              />
              {previewResp && (
                <Row gutter={8}>
                  <Col span={12}>
                    <Statistic
                      title="延迟"
                      value={previewResp.latency_ms.toFixed(2)}
                      suffix="ms"
                      valueStyle={{ fontSize: 14 }}
                    />
                  </Col>
                  <Col span={12}>
                    <Statistic
                      title="生效参数"
                      value={JSON.stringify(previewResp.effective_params)}
                      valueStyle={{ fontSize: 12 }}
                    />
                  </Col>
                </Row>
              )}
              {previewResp && previewResp.ignored_params.length > 0 && (
                <Alert
                  type="warning"
                  showIcon
                  message={`被忽略的参数: ${previewResp.ignored_params.join(', ')}`}
                />
              )}
              <Table<SearchHit>
                rowKey={(h) => `${h.rank}-${h.cell_id}`}
                columns={hitColumns}
                dataSource={previewResp?.hits ?? []}
                pagination={false}
                size="small"
                loading={previewLoading}
                locale={{
                  emptyText: previewCellId.trim() ? (
                    previewLoading ? (
                      '加载中...'
                    ) : (
                      '无结果'
                    )
                  ) : (
                    '输入 cell_id 启动预览'
                  ),
                }}
                scroll={{ y: 240 }}
              />
            </Space>
          </Card>
        </Col>
      </Row>
    </Space>
  );
};

export default SweepTab;
