/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

// Vite 配置：开发端口 5173，将 /api 代理到后端，配置 @ 别名指向 src
// 构建产物按 plotly / antd / vendor 三个 vendor chunk 拆分，降低首屏体积
//
// 代理目标可由 VITE_PROXY_TARGET 覆盖：本地原生开发默认 localhost:8000；
// 容器内开发须由 docker-compose.dev.yml 注入 http://backend:8000（容器内 localhost 指向自身）。
const proxyTarget = process.env.VITE_PROXY_TARGET ?? 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  server: {
    port: 5173,
    host: true,
    proxy: {
      '/api': {
        target: proxyTarget,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 2000,
    rollupOptions: {
      output: {
        manualChunks: {
          plotly: ['plotly.js-basic-dist', 'react-plotly.js'],
          'antd-icons': ['@ant-design/icons'],
          antd: ['antd'],
          vendor: ['react', 'react-dom', 'react-router-dom', 'zustand', 'axios'],
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    setupFiles: ['./src/test-utils/setup.ts'],
    coverage: {
      reporter: ['text', 'html'],
      exclude: ['src/**/*.test.*', 'src/**/index.ts'],
    },
  },
});
