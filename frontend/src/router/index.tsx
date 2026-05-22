import { createBrowserRouter, Navigate } from 'react-router-dom';
import Layout from '@/components/Layout';
import ProtectedRoute from '@/components/ProtectedRoute';
import LoginPage from '@/pages/LoginPage';
import RegisterPage from '@/pages/RegisterPage';
import DatasetsPage from '@/pages/DatasetsPage';
import IndexManagePage from '@/pages/IndexManagePage';
import SearchPage from '@/pages/SearchPage';
import VisualizationPage from '@/pages/VisualizationPage';
import EvaluationPage from '@/pages/EvaluationPage';
import RagChatPage from '@/pages/RagChatPage';

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
      { path: 'search', element: <SearchPage /> },
      { path: 'visualization', element: <VisualizationPage /> },
      { path: 'evaluation', element: <EvaluationPage /> },
      { path: 'rag', element: <RagChatPage /> },
    ],
  },
  { path: '*', element: <Navigate to="/" replace /> },
]);
