import { httpClient } from './client';
import type { LoginRequest, LoginResponse, RegisterRequest, User } from '@/types/auth';
import type { AdminUser, PasswordResetResponse } from '@/types/admin';

// 用户认证 API
export const authApi = {
  login: async (payload: LoginRequest): Promise<LoginResponse> => {
    const body = new URLSearchParams();
    body.set('username', payload.username);
    body.set('password', payload.password);
    const { data } = await httpClient.post<LoginResponse>('/auth/login', body, {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    });
    return data;
  },

  register: async (payload: RegisterRequest): Promise<User> => {
    const { data } = await httpClient.post<User>('/auth/register', payload);
    return data;
  },

  me: async (): Promise<User> => {
    const { data } = await httpClient.get<User>('/auth/me');
    return data;
  },

  logout: async (): Promise<void> => {
    await httpClient.post('/auth/logout');
  },
};

// 管理员-用户管理 API（与后端 /api/v1/admin/users 对齐）
export const adminApi = {
  listUsers: async (): Promise<AdminUser[]> => {
    const { data } = await httpClient.get<AdminUser[]>('/admin/users');
    return data;
  },

  updateRole: async (id: number, role: 'admin' | 'user'): Promise<AdminUser> => {
    const { data } = await httpClient.patch<AdminUser>(`/admin/users/${id}`, { role });
    return data;
  },

  deleteUser: async (id: number): Promise<{ detail: string }> => {
    const { data } = await httpClient.delete<{ detail: string }>(`/admin/users/${id}`);
    return data;
  },

  resetPassword: async (id: number): Promise<PasswordResetResponse> => {
    const { data } = await httpClient.post<PasswordResetResponse>(
      `/admin/users/${id}/reset-password`,
    );
    return data;
  },
};
