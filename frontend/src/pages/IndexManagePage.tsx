// 索引管理页面：针对选定数据集构建、查看、删除 ANN 索引（占位待实现）

import { Typography, Empty } from 'antd';

const { Title, Paragraph } = Typography;

const IndexManagePage = () => {
  return (
    <div>
      <Title level={3}>索引管理</Title>
      <Paragraph type="secondary">
        支持基于 Flat / HNSW / IVF / IVF-PQ / LSH 等算法构建 ANN 索引，并管理索引生命周期。
      </Paragraph>
      <Empty description="索引管理功能将在索引构建模块实现后填充" />
    </div>
  );
};

export default IndexManagePage;
