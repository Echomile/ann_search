import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import {
  Alert,
  Breadcrumb,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Form,
  InputNumber,
  Row,
  Select,
  Slider,
  Space,
  Tag,
  Typography,
  message,
} from 'antd';
import { ApartmentOutlined, ReloadOutlined } from '@ant-design/icons';
import { datasetsApi } from '@/api/datasets';
import { indexesApi } from '@/api/indexes';
import type { Dataset } from '@/types/dataset';
import type { IndexRecord } from '@/types/indexRecord';
import type { SubgraphNode, SubgraphResponse } from '@/types/subgraph';
import PlotlyChart, { type PlotlyData } from '@/components/PlotlyChart';
import { extractError } from '@/utils/error';

const { Title, Paragraph, Text } = Typography;

// 仅 HNSW 系后端暴露图结构
const HNSW_BACKENDS = ['hnswlib', 'adaptive-hnsw'] as const;

// 节点颜色：depth=0 entry 红 / 1 橙 / 2 黄 / 3 灰，配色对齐 task 描述
const DEPTH_COLORS: Record<number, string> = {
  0: '#ff4d4f',
  1: '#fa8c16',
  2: '#fadb14',
  3: '#bfbfbf',
};
const depthColor = (d: number): string => DEPTH_COLORS[d] ?? '#bfbfbf';

// 把节点按 depth 分环：ring radius = depth * R，同环角度均匀分布
interface PositionedNode extends SubgraphNode {
  x: number;
  y: number;
}

const RING_RADIUS = 100;

const layoutNodes = (nodes: SubgraphNode[]): PositionedNode[] => {
  if (nodes.length === 0) return [];
  // 按 depth 分桶 → 同桶内按 label 稳定排序，保证刷新后位置一致
  const buckets: Record<number, SubgraphNode[]> = {};
  for (const n of nodes) {
    const d = n.depth;
    if (!buckets[d]) buckets[d] = [];
    buckets[d].push(n);
  }
  const positioned: PositionedNode[] = [];
  for (const [d, group] of Object.entries(buckets)) {
    const depth = Number(d);
    const sorted = [...group].sort((a, b) => a.label - b.label);
    if (depth === 0) {
      // entry 自身放在原点
      sorted.forEach((n) => positioned.push({ ...n, x: 0, y: 0 }));
      continue;
    }
    const radius = depth * RING_RADIUS;
    sorted.forEach((n, idx) => {
      const theta = (2 * Math.PI * idx) / sorted.length;
      positioned.push({ ...n, x: radius * Math.cos(theta), y: radius * Math.sin(theta) });
    });
  }
  return positioned;
};

// 把 (src, dst) 边集铺成 Plotly lines mode 所需的 (x: [a,b,null,...], y: [...]) 形态
const buildEdgeTrace = (
  edges: { src: number; dst: number }[],
  posMap: Map<number, PositionedNode>,
): PlotlyData[number] => {
  const xs: (number | null)[] = [];
  const ys: (number | null)[] = [];
  for (const e of edges) {
    const a = posMap.get(e.src);
    const b = posMap.get(e.dst);
    if (!a || !b) continue;
    xs.push(a.x, b.x, null);
    ys.push(a.y, b.y, null);
  }
  return {
    x: xs,
    y: ys,
    mode: 'lines',
    type: 'scatter',
    line: { color: 'rgba(140,140,140,0.55)', width: 1 },
    hoverinfo: 'skip',
    showlegend: false,
    name: '边',
  } as PlotlyData[number];
};

const buildNodeTraces = (positioned: PositionedNode[]): PlotlyData => {
  // 按 depth 分组，每组一个 scatter trace，便于 legend 与不同 marker
  const depthGroups: Record<number, PositionedNode[]> = {};
  for (const n of positioned) {
    if (!depthGroups[n.depth]) depthGroups[n.depth] = [];
    depthGroups[n.depth].push(n);
  }
  return Object.entries(depthGroups)
    .map(([d, group]) => {
      const depth = Number(d);
      const isEntry = depth === 0;
      const symbol = isEntry ? 'star' : 'circle';
      const size = isEntry ? 22 : 14;
      const color = depthColor(depth);
      const text = group.map(
        (n) => `${n.cell_id}<br>depth=${n.depth}<br>cell_type=${n.cell_type ?? '-'}`,
      );
      return {
        x: group.map((n) => n.x),
        y: group.map((n) => n.y),
        mode: 'markers',
        type: 'scatter',
        marker: {
          color,
          size,
          symbol,
          line: { width: isEntry ? 2 : 1, color: '#1f1f1f' },
        },
        text,
        hoverinfo: 'text',
        name: isEntry ? `Entry (depth=0)` : `depth=${depth}`,
        showlegend: true,
      };
    }) as PlotlyData;
};

interface FormValues {
  dataset_id: number | null;
  index_id: number | null;
  cell_id: string;
  depth: number;
  layer: number;
  max_nodes: number;
}

const DEFAULT_FORM: FormValues = {
  dataset_id: null,
  index_id: null,
  cell_id: '',
  depth: 2,
  layer: 0,
  max_nodes: 200,
};

/**
 * HNSW 邻居子图可视化页（v1.2 D2 扩展功能）。
 *
 * 路由：``/indexes/:id/graph``。允许用户：
 *  1. 选择数据集 + HNSW 系索引（``hnswlib`` / ``adaptive-hnsw``）；
 *  2. 输入 entry cell_id、BFS 深度、HNSW 层、节点上限；
 *  3. 拉取局部子图并通过 Plotly 渲染节点 + 边，按 depth 分环 + 着色。
 */
const IndexGraphPage = () => {
  const { id } = useParams<{ id: string }>();
  const [searchParams] = useSearchParams();
  const presetIndexId = Number(id || searchParams.get('index_id') || 0) || null;

  const [form] = Form.useForm<FormValues>();
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [indexes, setIndexes] = useState<IndexRecord[]>([]);
  const [loadingDatasets, setLoadingDatasets] = useState(false);
  const [loadingIndexes, setLoadingIndexes] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [subgraph, setSubgraph] = useState<SubgraphResponse | null>(null);

  const fetchDatasets = useCallback(async () => {
    setLoadingDatasets(true);
    try {
      const data = await datasetsApi.list();
      setDatasets(data.filter((d) => d.status === 'ready'));
    } catch (err) {
      message.error(extractError(err));
    } finally {
      setLoadingDatasets(false);
    }
  }, []);

  const fetchIndexesFor = useCallback(async (datasetId: number) => {
    setLoadingIndexes(true);
    try {
      const data = await indexesApi.listByDataset(datasetId);
      // 仅保留 ready 的 HNSW 系索引
      setIndexes(
        data.filter(
          (r) => r.status === 'ready' && (HNSW_BACKENDS as readonly string[]).includes(r.backend),
        ),
      );
    } catch (err) {
      message.error(extractError(err));
    } finally {
      setLoadingIndexes(false);
    }
  }, []);

  // 初始化：拉数据集列表 + 若 url 带 index_id 则反查 dataset_id
  useEffect(() => {
    void fetchDatasets();
  }, [fetchDatasets]);

  useEffect(() => {
    if (!presetIndexId) return;
    (async () => {
      try {
        const rec = await indexesApi.get(presetIndexId);
        if (!(HNSW_BACKENDS as readonly string[]).includes(rec.backend)) {
          message.warning(`索引 #${rec.id} 后端 ${rec.backend} 不支持邻居图`);
          return;
        }
        form.setFieldsValue({ dataset_id: rec.dataset_id, index_id: rec.id });
        await fetchIndexesFor(rec.dataset_id);
      } catch (err) {
        // 静默忽略：路径里的 :id 可能不属于当前用户
        console.warn('preset index 加载失败', err);
      }
    })();
  }, [presetIndexId, form, fetchIndexesFor]);

  const handleDatasetChange = async (value: number | null) => {
    form.setFieldValue('index_id', null);
    setIndexes([]);
    setSubgraph(null);
    if (value) await fetchIndexesFor(value);
  };

  const handleSubmit = async (values: FormValues) => {
    if (!values.index_id) {
      message.warning('请先选择索引');
      return;
    }
    setSubmitting(true);
    try {
      const res = await indexesApi.getSubgraph(values.index_id, {
        cell_id: values.cell_id.trim(),
        depth: values.depth,
        layer: values.layer,
        max_nodes: values.max_nodes,
      });
      setSubgraph(res);
      if (res.truncated) {
        message.warning(`子图被 max_nodes=${values.max_nodes} 截断，可调大节点上限`);
      }
    } catch (err) {
      message.error(extractError(err));
      setSubgraph(null);
    } finally {
      setSubmitting(false);
    }
  };

  // Plotly 数据装配
  const plotData = useMemo<PlotlyData>(() => {
    if (!subgraph) return [];
    const positioned = layoutNodes(subgraph.nodes);
    const posMap = new Map(positioned.map((n) => [n.label, n]));
    const edgeTrace = buildEdgeTrace(subgraph.edges, posMap);
    const nodeTraces = buildNodeTraces(positioned);
    return [edgeTrace, ...nodeTraces];
  }, [subgraph]);

  const stats = useMemo(() => {
    if (!subgraph) return null;
    const depthCount: Record<number, number> = {};
    for (const n of subgraph.nodes) {
      depthCount[n.depth] = (depthCount[n.depth] ?? 0) + 1;
    }
    return {
      nodes: subgraph.nodes.length,
      edges: subgraph.edges.length,
      depthCount,
    };
  }, [subgraph]);

  return (
    <div>
      <Breadcrumb
        style={{ marginBottom: 16 }}
        items={[
          { title: <Link to="/">首页</Link> },
          { title: <Link to="/indexes">索引管理</Link> },
          { title: 'HNSW 邻居图' },
        ]}
      />
      <Title level={3} style={{ marginBottom: 8 }}>
        <ApartmentOutlined /> HNSW 邻居图谱
      </Title>
      <Paragraph type="secondary" style={{ marginBottom: 16 }}>
        以一个 cell 为起点，BFS 展开 HNSW 索引在该节点周围的小世界图结构。
        仅 <Text code>hnswlib</Text> / <Text code>adaptive-hnsw</Text> 后端支持。
      </Paragraph>

      <Card style={{ marginBottom: 16 }}>
        <Form<FormValues>
          form={form}
          layout="vertical"
          initialValues={DEFAULT_FORM}
          onFinish={handleSubmit}
        >
          <Row gutter={16}>
            <Col xs={24} sm={12} md={6}>
              <Form.Item
                label="数据集"
                name="dataset_id"
                rules={[{ required: true, message: '请选择数据集' }]}
              >
                <Select<number>
                  placeholder="选择 ready 数据集"
                  loading={loadingDatasets}
                  onChange={handleDatasetChange}
                  options={datasets.map((d) => ({ value: d.id, label: `#${d.id} ${d.name}` }))}
                  allowClear
                />
              </Form.Item>
            </Col>
            <Col xs={24} sm={12} md={6}>
              <Form.Item
                label="索引 (仅 HNSW 系)"
                name="index_id"
                rules={[{ required: true, message: '请选择 HNSW 索引' }]}
              >
                <Select<number>
                  placeholder="hnswlib / adaptive-hnsw"
                  loading={loadingIndexes}
                  options={indexes.map((r) => ({
                    value: r.id,
                    label: `#${r.id} ${r.backend}`,
                  }))}
                  disabled={indexes.length === 0}
                  allowClear
                />
              </Form.Item>
            </Col>
            <Col xs={24} sm={12} md={6}>
              <Form.Item
                label="Entry cell_id"
                name="cell_id"
                rules={[{ required: true, message: '请输入起点 cell_id' }]}
              >
                <Select
                  mode="tags"
                  maxCount={1}
                  placeholder="例如 c001"
                  tokenSeparators={[',']}
                  // antd Select tags mode 把数组写回字符串：onChange 内取首个
                  onChange={(value: string[]) =>
                    form.setFieldValue('cell_id', value[value.length - 1] ?? '')
                  }
                />
              </Form.Item>
            </Col>
            <Col xs={24} sm={12} md={6}>
              <Form.Item label="HNSW 层 (layer)" name="layer">
                <Select<number>
                  options={[0, 1, 2, 3].map((v) => ({ value: v, label: `layer ${v}` }))}
                />
              </Form.Item>
            </Col>
            <Col xs={24} sm={12} md={8}>
              <Form.Item label="BFS 深度 (depth)" name="depth">
                <Slider
                  min={1}
                  max={3}
                  marks={{ 1: '1', 2: '2', 3: '3' }}
                  step={1}
                  tooltip={{ open: true }}
                />
              </Form.Item>
            </Col>
            <Col xs={24} sm={12} md={6}>
              <Form.Item label="节点上限 (max_nodes)" name="max_nodes">
                <InputNumber min={10} max={500} step={10} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col xs={24} md={10} style={{ display: 'flex', alignItems: 'flex-end' }}>
              <Space>
                <Button
                  type="primary"
                  htmlType="submit"
                  loading={submitting}
                  icon={<ApartmentOutlined />}
                >
                  生成邻居图
                </Button>
                <Button
                  icon={<ReloadOutlined />}
                  onClick={() => {
                    form.resetFields();
                    setSubgraph(null);
                    setIndexes([]);
                  }}
                >
                  重置
                </Button>
              </Space>
            </Col>
          </Row>
        </Form>
      </Card>

      <Row gutter={16}>
        <Col xs={24} lg={18}>
          <Card title="子图可视化" loading={submitting}>
            {subgraph ? (
              <PlotlyChart
                data={plotData}
                height={520}
                layout={{
                  xaxis: { visible: false, scaleanchor: 'y', scaleratio: 1 },
                  yaxis: { visible: false },
                  margin: { l: 20, r: 20, t: 20, b: 60 },
                  hovermode: 'closest',
                  showlegend: true,
                  legend: { orientation: 'h', y: -0.05 },
                }}
              />
            ) : (
              <Empty description="提交表单生成子图" />
            )}
          </Card>
        </Col>
        <Col xs={24} lg={6}>
          <Card title="子图统计" style={{ marginBottom: 16 }}>
            {stats && subgraph ? (
              <Descriptions column={1} size="small">
                <Descriptions.Item label="节点数">{stats.nodes}</Descriptions.Item>
                <Descriptions.Item label="边数">{stats.edges}</Descriptions.Item>
                <Descriptions.Item label="后端">
                  <Tag color="blue">{subgraph.backend}</Tag>
                </Descriptions.Item>
                <Descriptions.Item label="Entry">
                  <Text code>{subgraph.entry_cell_id}</Text>
                </Descriptions.Item>
                <Descriptions.Item label="BFS 深度">{subgraph.depth}</Descriptions.Item>
                <Descriptions.Item label="HNSW 层">{subgraph.layer}</Descriptions.Item>
                {Object.entries(stats.depthCount).map(([d, c]) => (
                  <Descriptions.Item key={d} label={`depth=${d}`}>
                    <Tag color={depthColor(Number(d))} style={{ color: '#1f1f1f' }}>
                      {c}
                    </Tag>
                  </Descriptions.Item>
                ))}
              </Descriptions>
            ) : (
              <Empty description="-" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            )}
          </Card>
          {subgraph?.truncated && (
            <Alert
              type="warning"
              showIcon
              message="子图已被截断"
              description="节点数已触达 max_nodes 上限，可调大该值后重试，但渲染体积也会同步增大。"
            />
          )}
        </Col>
      </Row>
    </div>
  );
};

export default IndexGraphPage;
