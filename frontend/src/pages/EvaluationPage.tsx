// 性能评测页面：对比不同索引的召回率、响应延迟、QPS 等性能指标（占位待实现）

import { Typography, Empty } from 'antd';

const { Title, Paragraph } = Typography;

const EvaluationPage = () => {
  return (
    <div>
      <Title level={3}>性能评测</Title>
      <Paragraph type="secondary">
        对比不同 ANN 索引的召回率（Recall@K）、平均/尾部延迟、QPS、构建时间与内存占用。
      </Paragraph>
      <Empty description="评测报表将在评测模块实现后填充" />
    </div>
  );
};

export default EvaluationPage;
