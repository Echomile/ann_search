"""用户认证 / 管理员路由。

提供：

- ``POST /api/v1/auth/register``：用户名 + 密码注册；
- ``POST /api/v1/auth/login``：OAuth2 Password Flow，返回 JWT 与用户信息；
- ``GET  /api/v1/auth/me``：基于 ``Authorization: Bearer`` 解析当前用户；
- ``GET    /api/v1/admin/users``：管理员列出全部用户；
- ``PATCH  /api/v1/admin/users/{user_id}``：管理员更新指定用户角色；
- ``DELETE /api/v1/admin/users/{user_id}``：管理员删除用户（含级联与磁盘清理）；
- ``POST   /api/v1/admin/users/{user_id}/reset-password``：管理员一次性重置密码。

业务逻辑（用户名唯一性、密码哈希、口令校验、级联清理等）下沉到
:mod:`app.services.user_service`，路由层只负责把业务异常翻译成 HTTP 响应。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from app.api.deps import CurrentAdmin, CurrentUser, DbSession
from app.core.security import create_access_token
from app.schemas.common import Message
from app.schemas.user import (
    AdminUserUpdate,
    PasswordResetResponse,
    TokenOut,
    UserCreate,
    UserOut,
)
from app.services import user_service

router = APIRouter(prefix="/auth", tags=["认证"])
admin_router = APIRouter(prefix="/admin/users", tags=["管理员-用户"])


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


@admin_router.get(
    "",
    response_model=list[UserOut],
    summary="管理员-列出全部用户",
    description=(
        "返回数据库中全部用户（按 ID 升序）。\n\n"
        "- 需 ``Authorization: Bearer`` 且当前用户 ``role='admin'``；\n"
        "- 非管理员返回 ``403``，未登录返回 ``401``。"
    ),
)
async def admin_list_users(
    db: DbSession,
    _admin: CurrentAdmin,
) -> list[UserOut]:
    """列出全部用户（管理员视图）。

    Args:
        db: 异步数据库会话。
        _admin: 当前管理员（依赖触发权限校验）。

    Returns:
        list[UserOut]: 全部用户的公开信息。
    """
    rows = await user_service.list_users(db)
    return [UserOut.model_validate(u) for u in rows]


@admin_router.patch(
    "/{user_id}",
    response_model=UserOut,
    summary="管理员-更新用户角色",
    description=(
        "把指定用户的 ``role`` 改为 ``admin`` 或 ``user``。\n\n"
        "- 不允许修改自己的角色（返回 ``403`` 防止误降权）；\n"
        "- 目标用户不存在返回 ``404``；\n"
        "- 仅 ``admin`` 可调用。"
    ),
)
async def admin_update_user(
    user_id: int,
    payload: AdminUserUpdate,
    db: DbSession,
    admin: CurrentAdmin,
) -> UserOut:
    """更新指定用户的角色。

    Args:
        user_id: 目标用户 ID。
        payload: 角色更新请求体。
        db: 异步数据库会话。
        admin: 当前管理员。

    Raises:
        HTTPException: 修改自己时返回 ``403``；目标不存在返回 ``404``。

    Returns:
        UserOut: 更新后的用户公开信息。
    """
    if admin.id == user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="不能修改自己的角色",
        )
    try:
        user = await user_service.update_user_role(db, user_id, payload.role)
    except user_service.UserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return UserOut.model_validate(user)


@admin_router.delete(
    "/{user_id}",
    response_model=Message,
    summary="管理员-删除用户",
    description=(
        "删除指定用户。\n\n"
        "- 不允许删除自己（返回 ``403``）；\n"
        "- 通过外键 ``ON DELETE CASCADE`` 自动清理 ``datasets`` / ``search_logs`` "
        "等关联记录，并主动清理用户名下数据集对应的磁盘文件与索引目录；\n"
        "- 目标用户不存在返回 ``404``。"
    ),
)
async def admin_delete_user(
    user_id: int,
    db: DbSession,
    admin: CurrentAdmin,
) -> Message:
    """删除指定用户。

    Args:
        user_id: 目标用户 ID。
        db: 异步数据库会话。
        admin: 当前管理员。

    Raises:
        HTTPException: 删除自己时返回 ``403``；目标不存在返回 ``404``。

    Returns:
        Message: ``detail`` 形式的提示信息。
    """
    if admin.id == user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="不能删除自己",
        )
    try:
        await user_service.delete_user(db, user_id)
    except user_service.UserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return Message(detail=f"已删除用户 {user_id}")


@admin_router.post(
    "/{user_id}/reset-password",
    response_model=PasswordResetResponse,
    summary="管理员-重置用户密码",
    description=(
        "为指定用户生成一个 12 位 URL-safe 随机密码，bcrypt 入库并返回一次性明文。\n\n"
        "- 仅本次响应返回明文，无法再次查询；\n"
        "- 目标用户不存在返回 ``404``；\n"
        "- 仅 ``admin`` 可调用。"
    ),
)
async def admin_reset_user_password(
    user_id: int,
    db: DbSession,
    _admin: CurrentAdmin,
) -> PasswordResetResponse:
    """重置指定用户的密码。

    Args:
        user_id: 目标用户 ID。
        db: 异步数据库会话。
        _admin: 当前管理员（依赖触发权限校验）。

    Raises:
        HTTPException: 目标不存在返回 ``404``。

    Returns:
        PasswordResetResponse: 仅含一次性明文新密码与目标用户 ID。
    """
    try:
        temp_password = await user_service.reset_user_password(db, user_id)
    except user_service.UserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return PasswordResetResponse(user_id=user_id, temp_password=temp_password)
