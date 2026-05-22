import { Spin } from 'antd';

interface LoadingSpinnerProps {
  tip?: string;
  fullscreen?: boolean;
}

// 通用加载指示器
const LoadingSpinner = ({ tip = '加载中...', fullscreen = false }: LoadingSpinnerProps) => {
  if (fullscreen) {
    return <Spin tip={tip} size="large" fullscreen />;
  }
  return (
    <div style={{ display: 'flex', justifyContent: 'center', padding: 48 }}>
      <Spin tip={tip} size="large">
        <div style={{ minHeight: 80, minWidth: 80 }} />
      </Spin>
    </div>
  );
};

export default LoadingSpinner;
