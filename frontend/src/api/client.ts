import axios, { AxiosError, type AxiosInstance, type InternalAxiosRequestConfig } from 'axios';
import { message } from 'antd';

const TOKEN_KEY = 'ann_search_token';

// 全局 axios 实例：baseURL 指向后端 /api/v1，统一处理鉴权与错误
export const httpClient: AxiosInstance = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});

httpClient.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    const token = localStorage.getItem(TOKEN_KEY);
    if (token && config.headers) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    // FormData / URLSearchParams 等 body 必须由浏览器自动生成 Content-Type
    // （含 multipart boundary），不能用 instance 默认的 application/json。
    if (
      config.headers &&
      (config.data instanceof FormData || config.data instanceof URLSearchParams)
    ) {
      delete config.headers['Content-Type'];
    }
    return config;
  },
  (error) => Promise.reject(error),
);

httpClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError<{ detail?: string; message?: string }>) => {
    const status = error.response?.status;
    const detail = error.response?.data?.detail ?? error.response?.data?.message ?? error.message;

    if (status === 401) {
      localStorage.removeItem(TOKEN_KEY);
      if (window.location.pathname !== '/login') {
        message.warning('登录已失效，请重新登录');
        window.location.href = '/login';
      }
    } else if (status && status >= 500) {
      message.error(`服务器错误：${detail}`);
    } else if (detail) {
      message.error(detail);
    }
    return Promise.reject(error);
  },
);

export const tokenStorage = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (token: string) => localStorage.setItem(TOKEN_KEY, token),
  clear: () => localStorage.removeItem(TOKEN_KEY),
};
