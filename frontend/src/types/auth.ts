// 用户认证相关类型（与后端 Pydantic schema 严格对齐 snake_case）

export interface User {
  id: number;
  username: string;
  role: 'admin' | 'user';
  created_at: string;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface RegisterRequest {
  username: string;
  password: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  user: User;
}
