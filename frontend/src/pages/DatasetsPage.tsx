// 数据集页面：列出已上传的数据集、支持上传与删除（占位待实现）

import { Typography, Empty } from 'antd';

const { Title, Paragraph } = Typography;

const DatasetsPage = () => {
  return (
    <div>
      <Title level={3}>数据集</Title>
      <Paragraph type="secondary">
        管理 .h5ad 单细胞数据集：支持上传、查看元信息（细胞数、基因数、维度）以及删除。
      </Paragraph>
      <Empty description="数据集列表将在数据管理模块实现后填充" />
    </div>
  );
};

export default DatasetsPage;
