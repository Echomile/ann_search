"""用户相关 Pydantic schema。

集中定义注册 / 用户公开信息 / JWT 令牌等数据契约，
供 ``/api/v1/auth`` 系列接口以及鉴权依赖使用。
"""

from __future__ import annotations

from datetime import datetime

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


__all__ = ["UserBase", "UserCreate", "UserOut", "TokenOut"]
