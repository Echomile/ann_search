// 可视化页面：在 UMAP/PCA 二维降维图上高亮查询细胞与 Top-K 结果（占位待实现）

import { Typography, Empty } from 'antd';

const { Title, Paragraph } = Typography;

const VisualizationPage = () => {
  return (
    <div>
      <Title level={3}>结果可视化</Title>
      <Paragraph type="secondary">
        基于 Plotly 渲染 UMAP/PCA 二维降维散点图，高亮查询细胞与 Top-K 检索结果。
      </Paragraph>
      <Empty description="可视化图表将在可视化模块实现后填充" />
    </div>
  );
};

export default VisualizationPage;
