"""RAG 自然语言查询相关 schema。

约定：
    - ``RagQueryRequest`` 描述用户输入；
    - ``ParsedQuery`` 描述 LLM 解析得到的结构化检索参数；
    - ``RagResponse`` 同时返回解析结果、命中条目与自然语言总结。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RagQueryRequest(BaseModel):
    """用户自然语言查询请求。"""

    dataset_id: int = Field(..., description="目标数据集 ID")
    index_id: int | None = Field(
        None, description="指定使用的索引 ID；为 ``None`` 时自动选取最新 ready 索引"
    )
    query: str = Field(..., min_length=1, description="自然语言提问")
    top_k: int = Field(10, ge=1, le=100, description="返回近邻数量")


class ParsedQuery(BaseModel):
    """LLM 解析后的结构化检索参数。"""

    cell_id: str | None = Field(None, description="若用户明确给出 cell_id，则优先按 ID 检索")
    filters: dict[str, Any] = Field(
        default_factory=dict,
        description="metadata 过滤条件，如 ``cell_type``、``disease``、``tissue``",
    )
    top_k: int = Field(10, ge=1, le=100, description="返回近邻数量")
    intent: str = Field("", description="检索意图描述，用于回答生成")


class RagResponse(BaseModel):
    """RAG 返回结构。"""

    parsed: ParsedQuery = Field(..., description="LLM 解析得到的结构化检索参数")
    hits: list[dict[str, Any]] = Field(
        default_factory=list, description="命中条目列表，与 ``SearchHit`` 同构"
    )
    answer: str = Field(..., description="LLM 生成的自然语言总结")
    query_time_ms: float = Field(..., description="检索 + 解析 + 总结总耗时（毫秒）")
