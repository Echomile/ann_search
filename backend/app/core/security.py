"""安全工具模块。

集中提供：
    - bcrypt 密码哈希与校验；
    - JWT 访问令牌的签发与解码（HS256）。

所有解码失败由调用方按需捕获 :class:`jose.JWTError`，
避免将认证细节散落到业务路由中。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings

ALGORITHM = "HS256"


def hash_password(plain: str) -> str:
    """使用 bcrypt 对明文密码进行哈希。

    Args:
        plain: 待哈希的明文密码。

    Returns:
        str: bcrypt 哈希结果（UTF-8 字符串）。
    """
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """校验明文密码与已存储的 bcrypt 哈希是否一致。

    Args:
        plain: 用户提交的明文密码。
        hashed: 数据库中存储的 bcrypt 哈希。

    Returns:
        bool: 校验通过返回 ``True``，否则 ``False``。
    """
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(
    subject: str | int,
    expires_minutes: int | None = None,
) -> str:
    """生成 JWT 访问令牌。

    payload 仅包含 ``sub`` 与 ``exp`` 两个标准字段，
    保持令牌精简、可被任何标准 OAuth2 客户端解析。

    Args:
        subject: 令牌主体，一般为用户 ID；落到 ``sub`` 字段时强制转为字符串。
        expires_minutes: 自定义过期时间（分钟）。
            为 ``None`` 时回退到 ``settings.ACCESS_TOKEN_EXPIRE_MINUTES``。

    Returns:
        str: 编码后的 JWT 字符串。
    """
    minutes = (
        expires_minutes if expires_minutes is not None else settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    expire = datetime.now(UTC) + timedelta(minutes=minutes)
    payload: dict[str, Any] = {"sub": str(subject), "exp": expire}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """解码并校验 JWT 访问令牌。

    Args:
        token: 待解码的 JWT。

    Raises:
        jose.JWTError: 令牌缺失签名、过期、或被篡改时由 ``python-jose`` 抛出。

    Returns:
        dict[str, Any]: 解码后的 payload，至少含 ``sub`` 与 ``exp``。
    """
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])


__all__ = [
    "ALGORITHM",
    "hash_password",
    "verify_password",
    "create_access_token",
    "decode_access_token",
    "JWTError",
]
