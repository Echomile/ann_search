"""RAG 自然语言查询相关 schema。

历史兼容：
    - ``RagQueryRequest`` / ``ParsedQuery`` / ``RagResponse``：v1.1 旧版
      ``parse → search → summarize`` 单轮流程的 schema，仍保留以兼容
      可能存在的离线脚本与文档示例。

v1.2 D4 LLM Function Calling Agent:
    - ``ToolCallSchema``      : 单次 LLM 决策的工具调用。
    - ``ToolTraceItem``       : agent loop 中执行过的工具及其简要结果。
    - ``RagCitation``         : 引用追溯（cell_id + dataset_id）。
    - ``RagChatRequest``      : 多轮 chat 请求；``session_id`` 缺省时新建。
    - ``RagChatResponse``     : agent 最终答案 + tool_trace + citations + session_id。
    - ``RagSessionOut``       : 会话列表条目。
    - ``RagMessageOut``       : 单条消息详情。
    - ``RagSessionDetail``    : 会话详情含全部消息。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RagQueryRequest(BaseModel):
    """用户自然语言查询请求（v1.1 旧版字段，向后兼容）。"""

    dataset_id: int = Field(..., description="目标数据集 ID")
    index_id: int | None = Field(
        None, description="指定使用的索引 ID；为 ``None`` 时自动选取最新 ready 索引"
    )
    query: str = Field(..., min_length=1, description="自然语言提问")
    top_k: int = Field(10, ge=1, le=100, description="返回近邻数量")


class ParsedQuery(BaseModel):
    """LLM 解析后的结构化检索参数（v1.1 旧版）。"""

    cell_id: str | None = Field(None, description="若用户明确给出 cell_id，则优先按 ID 检索")
    filters: dict[str, Any] = Field(
        default_factory=dict,
        description="metadata 过滤条件，如 ``cell_type``、``disease``、``tissue``",
    )
    top_k: int = Field(10, ge=1, le=100, description="返回近邻数量")
    intent: str = Field("", description="检索意图描述，用于回答生成")


class RagResponse(BaseModel):
    """RAG 返回结构（v1.1 旧版）。"""

    parsed: ParsedQuery = Field(..., description="LLM 解析得到的结构化检索参数")
    hits: list[dict[str, Any]] = Field(
        default_factory=list, description="命中条目列表，与 ``SearchHit`` 同构"
    )
    answer: str = Field(..., description="LLM 生成的自然语言总结")
    query_time_ms: float = Field(..., description="检索 + 解析 + 总结总耗时（毫秒）")


class ToolCallSchema(BaseModel):
    """LLM 单次 function call 决策的 schema 形式。"""

    id: str = Field(..., description="工具调用 ID，由 LLM 给出或本地生成")
    name: str = Field(..., description="工具名，必须在 :data:`TOOLS_SCHEMA` 中")
    arguments: dict[str, Any] = Field(default_factory=dict, description="工具入参，JSON 对象")


class ToolTraceItem(BaseModel):
    """agent loop 中执行过的一次工具调用快照（响应可观察性字段）。"""

    name: str = Field(..., description="工具名")
    arguments: dict[str, Any] = Field(default_factory=dict, description="LLM 给出的入参")
    summary: str = Field(
        "",
        description=(
            "结果摘要：例如 ``hits=5`` / ``datasets=3`` / ``matched=120``，"
            "便于前端在状态条上简短展示。"
        ),
    )
    ok: bool = Field(True, description="工具是否成功执行（无 ``error`` 字段视为成功）")


class RagCitation(BaseModel):
    """引用追溯条目。"""

    cell_id: str = Field(..., description="被引用的 cell ID")
    dataset_id: int | None = Field(None, description="所属数据集 ID")


class RagChatRequest(BaseModel):
    """多轮 RAG 聊天请求（v1.2 D4 主流程）。

    - ``session_id`` 为空时新建一个 :class:`RagSession`，并以 ``query`` 前 50 字作为标题；
    - 提供 ``session_id`` 时追加到该会话历史中；
    - ``dataset_id`` 是上下文提示，便于 LLM 直接使用而无需调用 list_datasets；
    - ``max_iterations`` 上限 5，超过强制收尾，避免 LLM 死循环消耗 token。
    """

    query: str = Field(..., min_length=1, description="本轮用户自然语言提问")
    session_id: int | None = Field(None, description="会话 ID；为空时新建。新建后返回新生成的 ID。")
    dataset_id: int | None = Field(
        None,
        description="上下文数据集 ID 提示；LLM 可选择性使用，前端在『当前数据集』下推荐传入",
    )
    max_iterations: int = Field(
        5,
        ge=1,
        le=10,
        description="agent loop 最大 tool_call 轮数，达到后强制收尾",
    )


class RagChatResponse(BaseModel):
    """多轮 RAG 聊天响应（v1.2 D4 主流程）。"""

    session_id: int = Field(..., description="本轮使用的会话 ID（新建或沿用）")
    answer: str = Field(..., description="LLM 最终自然语言回答")
    tool_trace: list[ToolTraceItem] = Field(
        default_factory=list, description="本轮执行过的 tool_call 链路（透明度）"
    )
    citations: list[RagCitation] = Field(
        default_factory=list, description="本轮回答引用的 cell_id + dataset_id"
    )
    iterations: int = Field(..., description="实际执行的 agent loop 迭代次数")
    finish_reason: str = Field(
        ...,
        description="收尾原因：``stop`` 正常结束，``max_iterations`` 达上限强制收尾",
    )
    query_time_ms: float = Field(..., description="本轮总耗时（毫秒）")


class RagSessionOut(BaseModel):
    """会话列表条目，仅含摘要信息。"""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="会话 ID")
    user_id: int = Field(..., description="拥有者用户 ID")
    title: str = Field(..., description="会话标题")
    created_at: datetime = Field(..., description="创建时间")
    updated_at: datetime = Field(..., description="最近更新时间")
    message_count: int = Field(0, description="会话中消息数量（含 system 之外的全部）")


class RagMessageOut(BaseModel):
    """单条消息详情。"""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="消息 ID")
    session_id: int = Field(..., description="所属会话 ID")
    role: str = Field(..., description="角色：user | assistant | tool | system")
    content: str | None = Field(None, description="文本内容；可为空")
    tool_calls: list[ToolCallSchema] = Field(
        default_factory=list, description="assistant 决定的 tool_call 列表"
    )
    tool_results: list[dict[str, Any]] = Field(
        default_factory=list,
        description="role=tool 时的工具执行结果列表（含 tool_call_id + result）",
    )
    created_at: datetime = Field(..., description="创建时间")


class RagSessionDetail(BaseModel):
    """会话详情含全部消息。"""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="会话 ID")
    user_id: int = Field(..., description="拥有者用户 ID")
    title: str = Field(..., description="会话标题")
    created_at: datetime = Field(..., description="创建时间")
    updated_at: datetime = Field(..., description="最近更新时间")
    messages: list[RagMessageOut] = Field(default_factory=list, description="按时间顺序的所有消息")
