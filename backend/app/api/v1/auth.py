"""用户认证路由。

提供注册、登录、获取当前用户三类核心端点：

- ``POST /api/v1/auth/register``：用户名 + 密码注册；
- ``POST /api/v1/auth/login``：OAuth2 Password Flow，返回 JWT 与用户信息；
- ``GET  /api/v1/auth/me``：基于 ``Authorization: Bearer`` 解析当前用户。

业务逻辑（用户名唯一性、密码哈希、口令校验）下沉到 :mod:`app.services.user_service`，
路由层仅负责将业务异常转换为合理的 HTTP 响应。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from app.api.deps import CurrentUser, DbSession
from app.core.security import create_access_token
from app.schemas.user import TokenOut, UserCreate, UserOut
from app.services import user_service

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post(
    "/register",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="用户注册",
    description=(
        "使用用户名与明文密码注册新账号。\n\n"
        "- 入参：``UserCreate``（``username`` 长度 3-32，``password`` 长度 6-128）；\n"
        "- 用户名重复返回 ``400``；首位注册的用户自动获得 ``admin`` 角色；\n"
        "- 返回：``UserOut``，不含密码哈希等敏感字段。"
    ),
)
async def register(payload: UserCreate, db: DbSession) -> UserOut:
    """注册新用户。

    Args:
        payload: 注册请求体（用户名 + 明文密码）。
        db: 异步数据库会话。

    Raises:
        HTTPException: 用户名已存在时返回 ``400``。

    Returns:
        UserOut: 新注册用户的公开信息。
    """
    try:
        user = await user_service.create_user(db, payload.username, payload.password)
    except user_service.UsernameAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return UserOut.model_validate(user)


@router.post(
    "/login",
    response_model=TokenOut,
    summary="用户登录",
    description=(
        "OAuth2 Password Flow 登录端点，请求体为 ``application/x-www-form-urlencoded``。\n\n"
        "- 入参：``username`` / ``password`` 表单字段（``OAuth2PasswordRequestForm``）；\n"
        "- 校验失败统一返回 ``401``，避免暴露账号存在性；\n"
        "- 返回：``TokenOut``，含 ``access_token``、``token_type=bearer`` 与当前用户公开信息。"
    ),
)
async def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: DbSession,
) -> TokenOut:
    """OAuth2 表单登录并签发 JWT。

    Args:
        form_data: ``OAuth2PasswordRequestForm`` 解析得到的表单（``username`` / ``password``）。
        db: 异步数据库会话。

    Raises:
        HTTPException: 用户名或密码错误时返回 ``401``。

    Returns:
        TokenOut: 访问令牌与当前用户公开信息。
    """
    user = await user_service.authenticate_user(db, form_data.username, form_data.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token(subject=user.id)
    return TokenOut(
        access_token=access_token,
        token_type="bearer",
        user=UserOut.model_validate(user),
    )


@router.get(
    "/me",
    response_model=UserOut,
    summary="获取当前用户",
    description=(
        "返回 ``Authorization: Bearer <token>`` 对应的当前用户信息。\n\n"
        "- 令牌缺失、无效或过期时返回 ``401``；\n"
        "- 响应不包含密码哈希等敏感字段。"
    ),
)
async def me(current_user: CurrentUser) -> UserOut:
    """读取当前已认证用户。

    Args:
        current_user: 由鉴权依赖注入的当前用户。

    Returns:
        UserOut: 当前用户的公开信息。
    """
    return UserOut.model_validate(current_user)
