import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Form,
  Input,
  InputNumber,
  Row,
  Select,
  Space,
  Switch,
  Typography,
  message,
} from 'antd';
import { DotChartOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';
import { datasetsApi } from '@/api/datasets';
import { indexesApi } from '@/api/indexes';
import { searchApi } from '@/api/search';
import type { Dataset, UmapResponse } from '@/types/dataset';
import type { IndexRecord } from '@/types/indexRecord';
import type { SearchHit, SearchResponse } from '@/types/search';
import { useDatasetStore } from '@/store/datasetStore';
import { extractError } from '@/utils/error';
import PlotlyChart, { type PlotlyData } from '@/components/PlotlyChart';

const { Title, Paragraph, Text } = Typography;

interface FormValues {
  dataset_id: number;
  index_id?: number | null;
  cell_id: string;
  top_k: number;
}

// 把命中按 distance 升序排列后投射到 2D 平面（兜底用）：
// 半径用 distance 归一化，角度用 cell_id 的稳定哈希散开。
// 仅在后端 UMAP API 返回 has_umap=false 时使用。
const stableAngle = (s: string): number => {
  let hash = 0;
  for (let i = 0; i < s.length; i += 1) hash = (hash * 31 + s.charCodeAt(i)) | 0;
  return ((hash % 360) + 360) % 360;
};

interface ProjectedPoint {
  cell_id: string;
  x: number;
  y: number;
  distance: number;
  rank: number;
  cell_type?: string;
  meta: SearchHit['meta'];
}

const projectHits = (hits: SearchHit[]): ProjectedPoint[] => {
  if (hits.length === 0) return [];
  const distances = hits.map((h) => h.distance);
  const maxDist = Math.max(...distances, 1e-6);
  return hits.map((h) => {
    const r = (h.distance / maxDist) * 8 + (h.rank === 1 ? 0 : 0.5);
    const theta = (stableAngle(h.cell_id) * Math.PI) / 180;
    const cellType =
      h.meta && typeof h.meta.cell_type === 'string' ? (h.meta.cell_type as string) : undefined;
    return {
      cell_id: h.cell_id,
      x: r * Math.cos(theta),
      y: r * Math.sin(theta),
      distance: h.distance,
      rank: h.rank,
      cell_type: cellType,
      meta: h.meta,
    };
  });
};

// 生成背景点：稳定使用 cell_id 散布在更外圈
const buildBackground = (queryCell: string, count = 60): ProjectedPoint[] => {
  const out: ProjectedPoint[] = [];
  for (let i = 0; i < count; i += 1) {
    const seed = `${queryCell}-bg-${i}`;
    const theta = (stableAngle(seed) * Math.PI) / 180;
    const r = 10 + (i % 7);
    out.push({
      cell_id: seed,
      x: r * Math.cos(theta),
      y: r * Math.sin(theta),
      distance: NaN,
      rank: -1,
      meta: null,
    });
  }
  return out;
};

// 按 cell_type 着色：用稳定哈希映射到 HSL
const colorByCellType = (type: string): string => {
  const hue = stableAngle(type);
  return `hsl(${hue}, 65%, 55%)`;
};

const VisualizationPage = () => {
  const currentDataset = useDatasetStore((s) => s.currentDataset);
  const currentIndex = useDatasetStore((s) => s.currentIndex);

  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [indexes, setIndexes] = useState<IndexRecord[]>([]);
  const [datasetDetail, setDatasetDetail] = useState<Dataset | null>(null);
  const [umap, setUmap] = useState<UmapResponse | null>(null);
  const [response, setResponse] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [colorByType, setColorByType] = useState(false);
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

  const loadDatasetDetail = useCallback(async (id: number) => {
    try {
      const d = await datasetsApi.get(id);
      setDatasetDetail(d);
    } catch (err) {
      message.error(extractError(err));
    }
  }, []);

  const loadUmap = useCallback(async (id: number) => {
    try {
      const u = await datasetsApi.umap(id);
      setUmap(u);
    } catch (err) {
      // 静默降级到 mock 投影
      setUmap(null);
      console.warn('UMAP API 调用失败，降级到 mock 投影', err);
    }
  }, []);

  useEffect(() => {
    void loadDatasets();
  }, [loadDatasets]);

  useEffect(() => {
    if (!currentDataset) return;
    form.setFieldsValue({
      dataset_id: currentDataset.id,
      index_id: currentIndex?.id ?? undefined,
      top_k: 20,
    });
    void loadIndexes(currentDataset.id);
    void loadDatasetDetail(currentDataset.id);
    void loadUmap(currentDataset.id);
  }, [currentDataset, currentIndex, form, loadIndexes, loadDatasetDetail, loadUmap]);

  useEffect(() => {
    if (watchedDatasetId) {
      void loadIndexes(watchedDatasetId);
      void loadDatasetDetail(watchedDatasetId);
      void loadUmap(watchedDatasetId);
    }
  }, [watchedDatasetId, loadIndexes, loadDatasetDetail, loadUmap]);

  const handleRender = async () => {
    let v: FormValues;
    try {
      v = await form.validateFields();
    } catch {
      return;
    }
    if (!v.cell_id?.trim()) {
      setResponse(null);
      message.info('未提供 cell_id，仅展示背景占位点');
      return;
    }
    setLoading(true);
    try {
      const resp = await searchApi.byId({
        dataset_id: v.dataset_id,
        cell_id: v.cell_id.trim(),
        top_k: v.top_k ?? 20,
        index_id: v.index_id ?? null,
      });
      setResponse(resp);
    } catch (err) {
      message.error(extractError(err));
      setResponse(null);
    } finally {
      setLoading(false);
    }
  };

  const datasetOptions = useMemo(
    () => datasets.map((d) => ({ label: `${d.name} (#${d.id})`, value: d.id })),
    [datasets],
  );

  const indexOptions = useMemo(
    () =>
      indexes.map((i) => ({ label: `#${i.id} · ${i.backend} · ${i.metric}`, value: i.id })),
    [indexes],
  );

  // cell_id -> UMAP 坐标的查找索引（O(1) 命中）
  const umapIndex = useMemo(() => {
    if (!umap?.has_umap || !umap.coords || !umap.cell_ids) return null;
    const map = new Map<string, [number, number]>();
    for (let i = 0; i < umap.cell_ids.length; i += 1) {
      const c = umap.coords[i];
      if (c) map.set(umap.cell_ids[i], [c[0], c[1]]);
    }
    return map;
  }, [umap]);

  const plotData = useMemo<PlotlyData>(() => {
    const queryCell = form.getFieldValue('cell_id')?.trim?.() ?? '';

    // 路径 A：真实 UMAP 坐标
    if (umap?.has_umap && umapIndex && umap.coords && umap.cell_ids) {
      const traces: PlotlyData = [
        {
          x: umap.coords.map((c) => c[0]),
          y: umap.coords.map((c) => c[1]),
          mode: 'markers',
          type: 'scattergl',
          name: `背景细胞 (${umap.coords.length.toLocaleString()})`,
          marker: { color: 'rgba(140, 140, 140, 0.35)', size: 3 },
          text: umap.cell_ids,
          hovertemplate: '%{text}<extra></extra>',
        },
      ];
      if (response && response.hits.length > 0) {
        const hits = response.hits;
        const neighborHits = hits.filter((h) => h.rank > 1);
        const queryHits = hits.filter((h) => h.rank === 1);
        const lookup = (cid: string) => umapIndex.get(cid);
        const neighborCoords = neighborHits.map((h) => lookup(h.cell_id)).filter(Boolean) as [number, number][];
        const queryCoords = queryHits.map((h) => lookup(h.cell_id)).filter(Boolean) as [number, number][];
        const neighborColors = colorByType
          ? neighborHits.map((h) => {
              const t = h.meta?.cell_type;
              return typeof t === 'string' ? colorByCellType(t) : '#fa8c16';
            })
          : '#fa8c16';

        if (neighborCoords.length > 0) {
          traces.push({
            x: neighborCoords.map((c) => c[0]),
            y: neighborCoords.map((c) => c[1]),
            mode: 'markers',
            type: 'scatter',
            name: `Top-${neighborHits.length} 邻居`,
            marker: { color: neighborColors, size: 11, line: { width: 1, color: '#fff' } },
            text: neighborHits.map((h) => `${h.cell_id}<br>rank=${h.rank}<br>distance=${h.distance.toFixed(4)}`),
            hovertemplate: '%{text}<extra></extra>',
          });
        }
        if (queryCoords.length > 0) {
          traces.push({
            x: queryCoords.map((c) => c[0]),
            y: queryCoords.map((c) => c[1]),
            mode: 'markers',
            type: 'scatter',
            name: '查询细胞',
            marker: { color: '#f5222d', size: 18, symbol: 'star', line: { width: 2, color: '#fff' } },
            text: queryHits.map((h) => `${h.cell_id}<br>distance=${h.distance.toFixed(4)}`),
            hovertemplate: '%{text}<extra></extra>',
          });
        }
      }
      return traces;
    }

    // 路径 B：mock 投影兜底
    const projected = response ? projectHits(response.hits) : [];
    const background = buildBackground(queryCell || 'noquery');
    const traces: PlotlyData = [
      {
        x: background.map((p) => p.x),
        y: background.map((p) => p.y),
        mode: 'markers',
        type: 'scatter',
        name: '背景细胞 (mock)',
        marker: { color: 'rgba(140, 140, 140, 0.35)', size: 5 },
        text: background.map((p) => p.cell_id),
        hovertemplate: 'background %{text}<extra></extra>',
      },
    ];
    if (projected.length > 0) {
      const neighborPoints = projected.filter((p) => p.rank > 1);
      const queryPoints = projected.filter((p) => p.rank === 1);
      const neighborColors = colorByType
        ? neighborPoints.map((p) => (p.cell_type ? colorByCellType(p.cell_type) : '#fa8c16'))
        : '#fa8c16';
      traces.push({
        x: neighborPoints.map((p) => p.x),
        y: neighborPoints.map((p) => p.y),
        mode: 'markers',
        type: 'scatter',
        name: `Top-${neighborPoints.length} 邻居`,
        marker: { color: neighborColors, size: 10, line: { width: 1, color: '#fff' } },
        text: neighborPoints.map(
          (p) =>
            `${p.cell_id}<br>rank=${p.rank}<br>distance=${p.distance.toFixed(4)}` +
            (p.cell_type ? `<br>cell_type=${p.cell_type}` : ''),
        ),
        hovertemplate: '%{text}<extra></extra>',
      });
      traces.push({
        x: queryPoints.map((p) => p.x),
        y: queryPoints.map((p) => p.y),
        mode: 'markers',
        type: 'scatter',
        name: '查询细胞',
        marker: { color: '#f5222d', size: 16, symbol: 'star', line: { width: 2, color: '#fff' } },
        text: queryPoints.map((p) => `${p.cell_id}<br>distance=${p.distance.toFixed(4)}`),
        hovertemplate: '%{text}<extra></extra>',
      });
    }
    return traces;
  }, [umap, umapIndex, response, colorByType, form]);

  const plotLayout = useMemo(() => {
    const useReal = umap?.has_umap === true;
    return {
      title: { text: useReal ? '细胞 UMAP 散点图 + Top-K 邻居' : '查询 + Top-K 邻居 (2D mock 投影)' },
      xaxis: { title: { text: useReal ? 'UMAP-1' : 'mock-x' }, zeroline: false, showgrid: true, gridcolor: '#f0f0f0' },
      yaxis: { title: { text: useReal ? 'UMAP-2' : 'mock-y' }, zeroline: false, showgrid: true, gridcolor: '#f0f0f0' },
      legend: { orientation: 'h' as const, y: -0.18 },
      margin: { l: 50, r: 30, t: 40, b: 60 },
    };
  }, [umap]);

  if (!currentDataset && datasets.length === 0) {
    return (
      <div>
        <Title level={3}>结果可视化</Title>
        <Alert
          type="info"
          showIcon
          message="尚无 ready 状态的数据集"
          description={
            <>
              <span>请先前往 </span>
              <Link to="/datasets">数据集页</Link>
              <span> 上传并等待预处理完成。</span>
            </>
          }
        />
      </div>
    );
  }

  return (
    <div>
      <Title level={3}>结果可视化</Title>
      <Paragraph type="secondary">
        基于 Plotly 渲染细胞 UMAP 2D 散点图：背景显示数据集全部细胞的 UMAP 投影，
        发起 cell_id 检索后会用红色五角星高亮查询细胞、橙色高亮 Top-K 邻居。
      </Paragraph>

      <Card style={{ marginBottom: 24 }}>
        <Form form={form} layout="vertical" initialValues={{ top_k: 20 }}>
          <Row gutter={16}>
            <Col xs={24} md={6}>
              <Form.Item label="数据集" name="dataset_id" rules={[{ required: true }]}>
                <Select options={datasetOptions} placeholder="选择数据集" showSearch optionFilterProp="label" />
              </Form.Item>
            </Col>
            <Col xs={24} md={6}>
              <Form.Item label="索引" name="index_id">
                <Select options={indexOptions} placeholder="自动选最新 ready" allowClear />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item label="查询 cell_id（可选）" name="cell_id">
                <Input placeholder="留空则只显示背景点" />
              </Form.Item>
            </Col>
            <Col xs={24} md={4}>
              <Form.Item label="Top-K" name="top_k" rules={[{ required: true }]}>
                <InputNumber min={1} max={500} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Space>
            <Button
              type="primary"
              icon={<DotChartOutlined />}
              onClick={handleRender}
              loading={loading}
            >
              渲染散点图
            </Button>
            <Space size={8}>
              <Switch checked={colorByType} onChange={setColorByType} />
              <Text>按 cell_type 着色</Text>
            </Space>
          </Space>
        </Form>
      </Card>

      {datasetDetail && (
        <Card title="数据集元信息" style={{ marginBottom: 24 }} size="small">
          <Descriptions
            size="small"
            column={4}
            items={[
              { key: 'cells', label: '细胞数', children: datasetDetail.cell_count ?? '-' },
              { key: 'dim', label: '向量维度', children: datasetDetail.vector_dim ?? '-' },
              { key: 'src', label: '向量来源', children: datasetDetail.vector_source ?? '-' },
              {
                key: 'meta',
                label: '可过滤列',
                children: datasetDetail.meta_columns?.join(', ') ?? '-',
              },
            ]}
          />
        </Card>
      )}

      <Card title="散点图">
        {umap?.has_umap ? (
          <Alert
            type="success"
            showIcon
            style={{ marginBottom: 12 }}
            message={`真实 UMAP 散点（${umap.coords?.length.toLocaleString()} 点${umap.sampled ? `，已从 ${umap.total_cells.toLocaleString()} 下采样` : ''}）`}
          />
        ) : (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            message="未检测到 UMAP 坐标文件，散点位置由 distance + cell_id 哈希兜底生成"
          />
        )}
        <PlotlyChart data={plotData} layout={plotLayout} height={620} loading={loading} />
      </Card>
    </div>
  );
};

export default VisualizationPage;
