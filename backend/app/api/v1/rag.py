"""RAG（自然语言查询单细胞数据）路由骨架。

属于加分项：使用 LLM + 向量检索辅助进行细胞分析与自然语言问答。
"""

from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/rag", tags=["rag"])


@router.post(
    "/query",
    summary="自然语言查询",
    description="接收自然语言问题，结合 ANN 检索结果与 LLM 推理，返回带引用的答案。",
)
async def rag_query(payload: dict[str, Any]) -> dict[str, Any]:
    """自然语言查询骨架。"""
    raise NotImplementedError
