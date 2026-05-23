"""用户业务服务层。

封装与 :class:`app.models.user.User` 相关的纯业务逻辑，
不耦合 FastAPI / HTTP 语义，仅向上抛出业务异常或返回 ``None``。

约定：
    - 首个注册的用户自动获得 ``admin`` 角色，便于后续接口管理；
    - 用户名重复时抛出 :class:`ValueError`，由路由层转换为 HTTP 400；
    - 校验失败统一返回 ``None``，由路由层转换为 HTTP 401；
    - 管理员相关函数（``update_user_role`` / ``reset_user_password``）
      仅完成业务变更，权限校验由路由层 ``get_current_admin`` 负责。
"""

from __future__ import annotations

import secrets
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.models.dataset import Dataset
from app.models.user import User
from app.services import dataset_service

TEMP_PASSWORD_BYTES = 9


class UsernameAlreadyExistsError(ValueError):
    """用户名已存在异常。"""


class UserNotFoundError(LookupError):
    """目标用户不存在异常。"""


async def get_user_by_id(db: AsyncSession, user_id: int) -> User | None:
    """根据主键查询用户。

    Args:
        db: 异步数据库会话。
        user_id: 用户主键。

    Returns:
        User | None: 命中则返回对象，未命中返回 ``None``。
    """
    return await db.get(User, user_id)


async def get_user_by_username(db: AsyncSession, username: str) -> User | None:
    """根据用户名查询用户。

    Args:
        db: 异步数据库会话。
        username: 用户名（区分大小写）。

    Returns:
        User | None: 命中则返回对象，未命中返回 ``None``。
    """
    stmt = select(User).where(User.username == username)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def list_users(db: AsyncSession) -> list[User]:
    """列出全部用户（管理员视图）。

    Args:
        db: 异步数据库会话。

    Returns:
        list[User]: 按 ID 升序的用户列表。
    """
    stmt = select(User).order_by(User.id.asc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def _count_users(db: AsyncSession) -> int:
    """统计用户总数。

    Args:
        db: 异步数据库会话。

    Returns:
        int: 当前用户表中的记录数。
    """
    stmt = select(func.count()).select_from(User)
    result = await db.execute(stmt)
    return int(result.scalar_one())


async def create_user(db: AsyncSession, username: str, password: str) -> User:
    """创建新用户。

    Args:
        db: 异步数据库会话。
        username: 新用户名。
        password: 明文密码，由本函数负责哈希。

    Raises:
        UsernameAlreadyExistsError: 用户名已存在时抛出（``ValueError`` 子类）。

    Returns:
        User: 已落库的用户对象（已 refresh，可读取自增主键与时间戳）。
    """
    existing = await get_user_by_username(db, username)
    if existing is not None:
        raise UsernameAlreadyExistsError(f"用户名已存在: {username}")

    total = await _count_users(db)
    role = "admin" if total == 0 else "user"

    user = User(
        username=username,
        password_hash=hash_password(password),
        role=role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def authenticate_user(
    db: AsyncSession, username: str, password: str
) -> User | None:
    """校验用户名 / 密码。

    Args:
        db: 异步数据库会话。
        username: 用户名。
        password: 明文密码。

    Returns:
        User | None: 校验通过返回用户对象，否则返回 ``None``。
    """
    user = await get_user_by_username(db, username)
    if user is None:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


async def update_user_role(
    db: AsyncSession,
    user_id: int,
    role: Literal["admin", "user"],
) -> User:
    """更新指定用户的角色。

    Args:
        db: 异步数据库会话。
        user_id: 目标用户 ID。
        role: 目标角色，``admin`` 或 ``user``。

    Raises:
        UserNotFoundError: 目标用户不存在。

    Returns:
        User: 已 refresh 的用户对象。
    """
    user = await get_user_by_id(db, user_id)
    if user is None:
        raise UserNotFoundError(f"用户不存在: {user_id}")
    user.role = role
    await db.commit()
    await db.refresh(user)
    return user


async def delete_user(db: AsyncSession, user_id: int) -> None:
    """按 ID 删除用户，并主动清理其名下数据集的磁盘文件。

    Datasets / SearchLog 等关联表通过 ``ON DELETE CASCADE`` 自动随用户删除；
    但磁盘上的 ``h5ad`` / 向量 / 处理目录 / 索引目录不会被数据库级联清掉，
    因此本函数会先复用 :func:`dataset_service.delete_dataset` 主动清理。

    Args:
        db: 异步数据库会话。
        user_id: 要删除的用户 ID。

    Raises:
        UserNotFoundError: 目标用户不存在。
    """
    user = await get_user_by_id(db, user_id)
    if user is None:
        raise UserNotFoundError(f"用户不存在: {user_id}")

    stmt = select(Dataset).where(Dataset.owner_id == user_id)
    result = await db.execute(stmt)
    datasets = list(result.scalars().all())
    for ds in datasets:
        await dataset_service.delete_dataset(db, ds)

    await db.delete(user)
    await db.commit()


async def reset_user_password(db: AsyncSession, user_id: int) -> str:
    """为指定用户重置一个随机密码并落库（明文仅本次返回）。

    使用 :func:`secrets.token_urlsafe(9)` 生成 12 字符 URL-safe 随机串，
    经 bcrypt 哈希后写入 ``users.password_hash``。

    Args:
        db: 异步数据库会话。
        user_id: 目标用户 ID。

    Raises:
        UserNotFoundError: 目标用户不存在。

    Returns:
        str: 新生成的明文随机密码，调用方需立即返回给前端并不再持久化。
    """
    user = await get_user_by_id(db, user_id)
    if user is None:
        raise UserNotFoundError(f"用户不存在: {user_id}")
    temp_password = secrets.token_urlsafe(TEMP_PASSWORD_BYTES)
    user.password_hash = hash_password(temp_password)
    await db.commit()
    await db.refresh(user)
    return temp_password
