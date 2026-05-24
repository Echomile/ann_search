// 管理员视图下的用户相关类型，与后端 schemas/user.py 严格对齐。

/** 后端 `UserOut` 的管理员视角别名，便于在管理页区分语义。 */
export interface AdminUser {
  id: number;
  username: string;
  role: 'admin' | 'user';
  created_at: string;
}

/** 后端 `PasswordResetResponse`：一次性返回明文新密码。 */
export interface PasswordResetResponse {
  user_id: number;
  temp_password: string;
}
