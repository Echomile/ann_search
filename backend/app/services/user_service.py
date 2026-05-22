"""用户业务服务层。

封装与 :class:`app.models.user.User` 相关的纯业务逻辑，
不耦合 FastAPI / HTTP 语义，仅向上抛出业务异常或返回 ``None``。

约定：
    - 首个注册的用户自动获得 ``admin`` 角色，便于后续接口管理；
    - 用户名重复时抛出 :class:`ValueError`，由路由层转换为 HTTP 400；
    - 校验失败统一返回 ``None``，由路由层转换为 HTTP 401。
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.models.user import User


class UsernameAlreadyExistsError(ValueError):
    """用户名已存在异常。"""


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


async def delete_user(db: AsyncSession, user_id: int) -> bool:
    """按 ID 删除用户。

    Args:
        db: 异步数据库会话。
        user_id: 要删除的用户 ID。

    Returns:
        bool: 命中并删除返回 ``True``，未命中返回 ``False``。
    """
    user = await get_user_by_id(db, user_id)
    if user is None:
        return False
    await db.delete(user)
    await db.commit()
    return True
