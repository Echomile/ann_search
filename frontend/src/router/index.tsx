import { lazy, Suspense } from 'react';
import { createBrowserRouter, Navigate } from 'react-router-dom';
import Layout from '@/components/Layout';
import LoadingSpinner from '@/components/LoadingSpinner';
import ProtectedRoute from '@/components/ProtectedRoute';
import LoginPage from '@/pages/LoginPage';
import RegisterPage from '@/pages/RegisterPage';
import DatasetsPage from '@/pages/DatasetsPage';
import IndexManagePage from '@/pages/IndexManagePage';
import IndexDetailPage from '@/pages/IndexDetailPage';
import SearchPage from '@/pages/SearchPage';
import RagChatPage from '@/pages/RagChatPage';
import AdminUsersPage from '@/pages/AdminUsersPage';

// 重 Plotly 依赖的页面单独懒加载，避免拖慢首屏
const VisualizationPage = lazy(() => import('@/pages/VisualizationPage'));
const EvaluationPage = lazy(() => import('@/pages/EvaluationPage'));

const withSuspense = (node: JSX.Element) => (
  <Suspense fallback={<LoadingSpinner />}>{node}</Suspense>
);

// 全局路由表
export const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  { path: '/register', element: <RegisterPage /> },
  {
    path: '/',
    element: (
      <ProtectedRoute>
        <Layout />
      </ProtectedRoute>
    ),
    children: [
      { index: true, element: <Navigate to="/datasets" replace /> },
      { path: 'datasets', element: <DatasetsPage /> },
      { path: 'indexes', element: <IndexManagePage /> },
      { path: 'indexes/:id', element: <IndexDetailPage /> },
      { path: 'search', element: <SearchPage /> },
      { path: 'visualization', element: withSuspense(<VisualizationPage />) },
      { path: 'evaluation', element: withSuspense(<EvaluationPage />) },
      { path: 'rag', element: <RagChatPage /> },
      { path: 'admin/users', element: <AdminUsersPage /> },
    ],
  },
  { path: '*', element: <Navigate to="/" replace /> },
]);
