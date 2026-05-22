// 检索页面：根据细胞 ID 或自定义向量执行 Top-K 检索，支持条件过滤（占位待实现）

import { Typography, Empty } from 'antd';

const { Title, Paragraph } = Typography;

const SearchPage = () => {
  return (
    <div>
      <Title level={3}>相似细胞检索</Title>
      <Paragraph type="secondary">
        支持基于细胞编号或自定义向量的 Top-K 检索，可附加 cell_type 等条件过滤。
      </Paragraph>
      <Empty description="检索表单与结果将在查询模块实现后填充" />
    </div>
  );
};

export default SearchPage;
