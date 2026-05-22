"""FastAPI 依赖项集合。

集中提供：
    - :func:`get_db`：异步数据库会话；
    - :data:`oauth2_scheme`：从 ``Authorization: Bearer`` 头提取 JWT；
    - :func:`get_current_user`：基于 JWT 的当前用户解析；
    - :func:`get_current_admin`：在已认证用户基础上校验管理员角色。

将依赖统一收敛在 ``app.api.deps``，便于在测试中通过
``app.dependency_overrides`` 注入夹具实现。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.db.session import AsyncSessionLocal
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/login",
    description="使用 /api/v1/auth/login 端点以 OAuth2 password flow 获取 Bearer 令牌。",
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """提供一个请求作用域的异步数据库会话。

    使用 ``async with AsyncSessionLocal()`` 自动管理事务/连接的关闭，
    保证即便路由抛出异常也能释放底层连接。

    Yields:
        AsyncSession: 异步数据库会话。
    """
    async with AsyncSessionLocal() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: DbSession,
) -> User:
    """解析 ``Authorization: Bearer`` 中的 JWT 并返回当前用户。

    Args:
        token: 由 :data:`oauth2_scheme` 自动从请求头提取的 JWT 字符串。
        db: 异步数据库会话。

    Raises:
        HTTPException: 当令牌缺失、无效、过期或对应用户不存在时返回 ``401``。

    Returns:
        User: 当前已认证用户。
    """
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无效或缺失的访问令牌",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
    except JWTError as exc:
        raise credentials_error from exc

    sub = payload.get("sub")
    if sub is None:
        raise credentials_error
    try:
        user_id = int(sub)
    except (TypeError, ValueError) as exc:
        raise credentials_error from exc

    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_error
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_current_admin(user: CurrentUser) -> User:
    """要求当前用户具备管理员角色。

    Args:
        user: 由 :func:`get_current_user` 注入的当前用户。

    Raises:
        HTTPException: 非管理员返回 ``403``。

    Returns:
        User: 已认证的管理员用户。
    """
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return user


CurrentAdmin = Annotated[User, Depends(get_current_admin)]


__all__ = [
    "oauth2_scheme",
    "get_db",
    "DbSession",
    "get_current_user",
    "CurrentUser",
    "get_current_admin",
    "CurrentAdmin",
]
