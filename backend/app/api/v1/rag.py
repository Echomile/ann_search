"""RAG（自然语言查询单细胞数据）路由。

属于加分项：使用 LLM + 向量检索辅助进行细胞分析与自然语言问答。
完整流程见 :func:`app.services.rag.rag_answer`。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.schemas.rag import RagQueryRequest, RagResponse
from app.services.rag import rag_answer

router = APIRouter(prefix="/rag", tags=["rag"])


@router.post(
    "/query",
    response_model=RagResponse,
    summary="自然语言检索",
    description=(
        "用自然语言提问，LLM 解析为结构化检索参数（``cell_id`` 或 metadata 过滤条件），"
        "调用 ANN 检索后再由 LLM 生成自然语言回答。"
        " 当 ``LLM_PROVIDER=mock`` 时采用关键词规则解析与模板化总结，"
        "无需任何外部 API Key 即可工作。"
    ),
)
async def rag_query(
    payload: RagQueryRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> RagResponse:
    """自然语言检索入口。"""
    return await rag_answer(db=db, user_id=current_user.id, request=payload)
