// RAG 对话页面：以自然语言对话方式查询细胞与分析结果（占位待实现）

import { Typography, Empty } from 'antd';

const { Title, Paragraph } = Typography;

const RagChatPage = () => {
  return (
    <div>
      <Title level={3}>RAG 对话</Title>
      <Paragraph type="secondary">
        基于检索增强生成（RAG），结合单细胞数据库提供自然语言交互式分析。
      </Paragraph>
      <Empty description="RAG 对话窗口将在 RAG 模块实现后填充" />
    </div>
  );
};

export default RagChatPage;
