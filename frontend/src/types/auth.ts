// 用户认证相关类型

export interface User {
  id: number;
  username: string;
  email?: string;
  role: 'admin' | 'user';
  createdAt?: string;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface RegisterRequest {
  username: string;
  password: string;
  email?: string;
}

export interface LoginResponse {
  accessToken: string;
  tokenType: string;
  user: User;
}
