"""RAG（自然语言查询单细胞数据）路由。

属于 v1.2 D4 扩展功能：从 v1.1 的 ``parse → search → summarize`` 三段固定流程，
升级到 **LLM Function Calling Agent** 多轮工具调用模式。

接口列表：
    - ``POST /rag/query``                  : 多轮 chat 入口，LLM 自主决定 tool_call；
    - ``GET  /rag/sessions``               : 列出当前用户的所有 RAG 会话；
    - ``GET  /rag/sessions/{session_id}``  : 拉取会话详情含全部消息（含 tool_trace 历史）；
    - ``DELETE /rag/sessions/{session_id}``: 删除一个会话（级联清空消息）。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.models.rag import RagSession
from app.schemas.rag import (
    RagChatRequest,
    RagChatResponse,
    RagSessionDetail,
    RagSessionOut,
)
from app.services.rag import (
    chat_with_tools,
    get_session_detail,
    list_user_sessions,
)

router = APIRouter(prefix="/rag", tags=["rag"])


@router.post(
    "/query",
    response_model=RagChatResponse,
    summary="LLM Function Calling 多轮聊天",
    description=(
        "v1.2 D4 扩展功能主入口：LLM 自主调用 ``list_datasets`` / ``search_by_cell_id`` /"
        " ``search_by_vector`` / ``filter_cells`` / ``summarize_results`` 五个工具，"
        "通过多轮 tool_call → tool_result 循环直到给出最终回答。\n\n"
        "- 不传 ``session_id`` 时自动新建会话，``title`` 取首条 query 前 50 字；\n"
        "- 传 ``dataset_id`` 作为上下文 hint，LLM 可直接复用而无需先调 list_datasets；\n"
        "- 返回 ``tool_trace`` 暴露 LLM 调用过哪些工具（透明度），"
        "``citations`` 给出被引用的 ``cell_id``+``dataset_id``；\n"
        "- ``LLM_PROVIDER=mock`` 时全程零外部依赖，"
        "真实 LLM (openai / dashscope / anthropic) 失败自动回退 mock。"
    ),
)
async def chat_query(
    payload: RagChatRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> RagChatResponse:
    """LLM Function Calling Agent loop 多轮聊天入口。"""
    return await chat_with_tools(db=db, user=current_user, request=payload)


@router.get(
    "/sessions",
    response_model=list[RagSessionOut],
    summary="列出 RAG 会话",
    description=(
        "返回当前用户的所有 RAG 会话，按 ``updated_at`` 倒序。"
        " 每条会话附带 ``message_count`` 便于前端列表展示历史轮次。"
    ),
)
async def list_sessions(current_user: CurrentUser, db: DbSession) -> list[RagSessionOut]:
    """列出当前用户全部会话。"""
    return await list_user_sessions(db, current_user.id)


@router.get(
    "/sessions/{session_id}",
    response_model=RagSessionDetail,
    summary="拉取 RAG 会话详情",
    description=(
        "返回指定会话的完整消息列表（含 user / assistant / tool 三类角色），"
        "前端可据此重建对话气泡 + 折叠工具调用历史。"
        " 会话不存在或非本人时返回 ``404``。"
    ),
)
async def get_session(
    session_id: int,
    current_user: CurrentUser,
    db: DbSession,
) -> RagSessionDetail:
    """获取会话详情。"""
    return await get_session_detail(db, current_user.id, session_id)


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除 RAG 会话",
    description=(
        "级联删除会话与其下全部消息（依赖 DB ``ON DELETE CASCADE``）。"
        " 会话不存在或非本人时返回 ``404``。"
    ),
)
async def delete_session(
    session_id: int,
    current_user: CurrentUser,
    db: DbSession,
) -> None:
    """删除指定会话。"""
    stmt = select(RagSession).where(
        RagSession.id == session_id, RagSession.user_id == current_user.id
    )
    rag_session = (await db.execute(stmt)).scalar_one_or_none()
    if rag_session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在或无权访问")
    await db.delete(rag_session)
    await db.commit()
