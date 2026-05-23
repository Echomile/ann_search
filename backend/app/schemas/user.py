"""用户相关 Pydantic schema。

集中定义注册 / 用户公开信息 / JWT 令牌等数据契约，
以及管理员视角下的用户更新、密码重置请求/响应模型，
供 ``/api/v1/auth`` 与 ``/api/v1/admin/users`` 系列接口共用。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class UserBase(BaseModel):
    """用户公共字段。

    Attributes:
        username: 登录用户名，长度 3-32。
    """

    username: str = Field(..., min_length=3, max_length=32, description="用户名，长度 3-32")


class UserCreate(UserBase):
    """注册请求体。

    Attributes:
        username: 登录用户名。
        password: 明文密码，长度 6-128。
    """

    password: str = Field(..., min_length=6, max_length=128, description="明文密码，长度 6-128")


class UserOut(UserBase):
    """对外暴露的用户信息，不包含密码哈希等敏感字段。

    Attributes:
        id: 用户主键。
        username: 用户名（继承自 :class:`UserBase`）。
        role: 角色，``user`` 或 ``admin``。
        created_at: 创建时间。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="用户 ID")
    role: str = Field(..., description="角色：user 或 admin")
    created_at: datetime = Field(..., description="创建时间")


class TokenOut(BaseModel):
    """登录成功响应：含 JWT 与当前用户信息。

    Attributes:
        access_token: JWT 字符串。
        token_type: 令牌类型，固定为 ``bearer``。
        user: 当前已认证的用户公开信息。
    """

    access_token: str = Field(..., description="JWT 访问令牌")
    token_type: str = Field(default="bearer", description="令牌类型，固定为 bearer")
    user: UserOut = Field(..., description="当前用户的公开信息")


class AdminUserUpdate(BaseModel):
    """管理员更新用户字段（仅支持 ``role``）。

    Attributes:
        role: 目标角色，仅允许 ``admin`` 或 ``user``。
    """

    role: Literal["admin", "user"] = Field(..., description="目标角色：admin 或 user")


class PasswordResetRequest(BaseModel):
    """密码重置请求体（预留扩展字段）。

    当前接口不需要客户端传任何参数，但保留该 schema 便于以后扩展
    （如指定密码长度、是否邮件下发等）。
    """

    model_config = ConfigDict(extra="forbid")


class PasswordResetResponse(BaseModel):
    """密码重置响应：仅返回一次性明文新密码。

    Attributes:
        user_id: 被重置密码的用户 ID。
        temp_password: bcrypt 已入库的明文随机密码，仅本次返回。
    """

    user_id: int = Field(..., description="被重置密码的用户 ID")
    temp_password: str = Field(..., description="一次性明文新密码（已 bcrypt 入库）")


__all__ = [
    "UserBase",
    "UserCreate",
    "UserOut",
    "TokenOut",
    "AdminUserUpdate",
    "PasswordResetRequest",
    "PasswordResetResponse",
]
