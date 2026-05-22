import { RouterProvider } from 'react-router-dom';
import { router } from './router';

// 应用根组件：通过 RouterProvider 装载路由表
const App = () => {
  return <RouterProvider router={router} />;
};

export default App;
