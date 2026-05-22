import { useMemo, type ComponentProps } from 'react';
import createPlotlyComponent from 'react-plotly.js/factory';
import PlotlyDist from 'plotly.js-dist-min';

// react-plotly.js 默认入口会 require 'plotly.js/dist/plotly'，
// 该路径在仅安装 dist-min 包时不可达，因此使用 factory 手动注入 plotly 引擎。
const Plot = createPlotlyComponent(PlotlyDist as object);

type PlotComponentProps = ComponentProps<typeof Plot>;

export type PlotlyData = PlotComponentProps['data'];
export type PlotlyLayout = PlotComponentProps['layout'];
export type PlotlyConfig = NonNullable<PlotComponentProps['config']>;

interface PlotlyChartProps {
  data: PlotlyData;
  layout?: Partial<PlotlyLayout>;
  config?: Partial<PlotlyConfig>;
  height?: number | string;
  loading?: boolean;
}

const DEFAULT_FONT = {
  family:
    '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Helvetica Neue", Arial, sans-serif',
  size: 12,
  color: '#1f1f1f',
};

const DEFAULT_MARGIN = { l: 48, r: 24, t: 48, b: 48 };
const DEFAULT_CONFIG: Partial<PlotlyConfig> = {
  responsive: true,
  displaylogo: false,
  modeBarButtonsToRemove: ['lasso2d', 'select2d'],
};

// react-plotly.js 的轻封装：统一字体、留白、响应式与 loading 态
const PlotlyChart = ({ data, layout, config, height = 360, loading }: PlotlyChartProps) => {
  const mergedLayout = useMemo<Partial<PlotlyLayout>>(
    () => ({
      autosize: true,
      font: DEFAULT_FONT,
      margin: DEFAULT_MARGIN,
      paper_bgcolor: 'transparent',
      plot_bgcolor: 'transparent',
      hovermode: 'closest',
      legend: { orientation: 'h', y: -0.2 },
      ...layout,
    }),
    [layout],
  );

  const mergedConfig = useMemo<Partial<PlotlyConfig>>(
    () => ({ ...DEFAULT_CONFIG, ...config }),
    [config],
  );

  return (
    <div style={{ width: '100%', height, opacity: loading ? 0.4 : 1, transition: 'opacity 0.2s' }}>
      <Plot
        data={data}
        layout={mergedLayout}
        config={mergedConfig}
        useResizeHandler
        style={{ width: '100%', height: '100%' }}
      />
    </div>
  );
};

export default PlotlyChart;
