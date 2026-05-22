import { httpClient } from './client';
import type { LoginRequest, LoginResponse, RegisterRequest, User } from '@/types/auth';

// 用户认证 API
export const authApi = {
  login: async (payload: LoginRequest): Promise<LoginResponse> => {
    const { data } = await httpClient.post<LoginResponse>('/auth/login', payload);
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
