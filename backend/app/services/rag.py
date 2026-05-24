"""RAG 服务：LLM Function Calling Agent loop + v1.1 兼容版单轮 RAG。

模块组织:
    - :class:`LLMClient`         : 客户端协议，含 v1.1 ``parse_query``/``summarize``
      与 v1.2 ``chat_with_tools`` 三个方法。
    - :class:`MockLLMClient`     : 纯规则解析 + 规则 tool_call 模拟，默认启用、无外部依赖。
    - :class:`AnthropicClient`   : 基于 ``anthropic>=0.40`` SDK 的 Claude Opus 4.7 客户端；
      调用 function calling 失败时统一回退到 :class:`MockLLMClient`。
    - :func:`get_llm_client`     : 根据 :data:`settings.LLM_PROVIDER` 返回对应实例。
    - :func:`rag_answer`         : v1.1 单轮主流程，供旧版接口保留兼容。
    - :func:`chat_with_tools`    : v1.2 多轮 agent loop 主流程，供新版 ``/rag/query``。

agent loop 设计:
    1. ``session_id`` 缺省则新建 :class:`RagSession`，并把 ``query`` 持久化为首条
       ``user`` 消息；否则附加到既有会话。
    2. 组装 ``messages``：``[system, *history, user]``，转 OpenAI 风格供 LLM 调用。
    3. 迭代调用 ``llm_client.chat_with_tools(messages, TOOLS_SCHEMA)``：
        - ``finish_reason='tool_calls'``：把 assistant tool_call 持久化，执行所有
          工具，工具结果以 ``role='tool'`` 消息落库并追加到 ``messages``，继续下一轮；
        - ``finish_reason='stop'``：把最终回答持久化为 ``assistant`` 消息，返回。
    4. 达到 ``max_iterations`` 仍未 ``stop`` 时强制收尾，避免死循环消耗 token。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np
from fastapi import HTTPException, status
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.dataset import Dataset
from app.models.index_record import IndexRecord
from app.models.rag import RagMessage, RagSession
from app.models.user import User
from app.schemas.rag import (
    ParsedQuery,
    RagChatRequest,
    RagChatResponse,
    RagCitation,
    RagQueryRequest,
    RagResponse,
    RagSessionDetail,
    RagSessionOut,
    ToolTraceItem,
)
from app.services import search as search_service
from app.services.rag_tools import TOOLS_SCHEMA, execute_tool

logger = get_logger(__name__)


# =============================================================================
# 关键词词典（v1.1 旧版 parse_query 仍依赖；mock chat_with_tools 也复用）
# =============================================================================

_KEYWORD_HINTS: dict[str, dict[str, list[str]]] = {
    "cell_type": {
        "hepatocyte": ["hepatocyte", "肝细胞"],
        "endothelial": ["endothelial", "内皮"],
        "macrophage": ["macrophage", "巨噬"],
        "kupffer": ["kupffer", "库普弗"],
        "t cell": ["t cell", "t-cell", "t 细胞"],
        "b cell": ["b cell", "b-cell", "b 细胞"],
        "fibroblast": ["fibroblast", "成纤维"],
        "cholangiocyte": ["cholangiocyte", "胆管"],
        "nk cell": ["nk cell", "nk-cell", "nk 细胞", "自然杀伤"],
    },
    "tissue": {
        "liver": ["liver", "肝脏", "肝"],
        "lung": ["lung", "肺"],
        "heart": ["heart", "心脏"],
        "kidney": ["kidney", "肾"],
        "blood": ["blood", "血液"],
    },
    "disease": {
        "normal": ["healthy", "normal", "健康", "正常"],
        "tumor": ["tumor", "cancer", "肿瘤", "癌"],
        "fibrosis": ["fibrosis", "纤维化"],
        "cirrhosis": ["cirrhosis", "肝硬化"],
    },
}


def _match_keyword(text: str, available_filters: list[str]) -> dict[str, str]:
    """按关键词词典在自然语言中匹配可用的 metadata 过滤值。"""
    matched: dict[str, str] = {}
    allowed = {c.lower() for c in available_filters}
    for column, mapping in _KEYWORD_HINTS.items():
        if column not in allowed:
            continue
        for value, hints in mapping.items():
            if any(hint in text for hint in hints):
                matched[column] = value
                break
    return matched


def _extract_top_k(text: str, default: int) -> int:
    """从自然语言中粗略抽取 ``top_k``（如 “前 20 个”、“top 5”）。"""
    patterns = [
        r"top\s*(\d{1,3})",
        r"前\s*(\d{1,3})\s*[个条]?",
        r"(\d{1,3})\s*个相似",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            value = int(m.group(1))
            if 1 <= value <= 100:
                return value
    return default


def _extract_cell_id(text: str) -> str | None:
    """识别用户是否显式提供 ``cell_id``。"""
    m = re.search(
        r"cell[_ ]?id\s*[:=]?\s*[\"']?([A-Za-z0-9][\w\-:.]*)[\"']?",
        text,
        flags=re.IGNORECASE,
    )
    if m:
        return m.group(1).strip(" \"'")
    return None


def _extract_dataset_id(text: str) -> int | None:
    """识别用户是否显式提供 ``dataset_id`` / ``数据集 #5``。"""
    m = re.search(
        r"(?:dataset[_ ]?id|数据集|dataset)\s*[#:=]?\s*(\d{1,6})",
        text,
        flags=re.IGNORECASE,
    )
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


# =============================================================================
# v1.2 D4: Function Calling Agent 数据结构
# =============================================================================


@dataclass
class ToolCall:
    """LLM 单次 function call 决策。

    Attributes:
        id: 工具调用唯一 ID，由 LLM 给出（OpenAI tool_call.id）或本地生成。
        name: 工具名，必须在 :data:`TOOLS_SCHEMA` 中。
        arguments: 工具入参字典，已经被 JSON 解析为 dict。
    """

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatResponse:
    """LLM ``chat_with_tools`` 的统一响应结构。

    Attributes:
        finish_reason: ``tool_calls`` 表示还需要执行工具；``stop`` 表示已给出最终回答。
        tool_calls: ``finish_reason='tool_calls'`` 时给出的工具调用列表。
        content: ``finish_reason='stop'`` 时的自然语言答案；
            ``tool_calls`` 阶段也可能附带说明文字（OpenAI 协议允许）。
    """

    finish_reason: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    content: str = ""


# =============================================================================
# LLMClient 协议 + 2 个实现（mock / anthropic）
# =============================================================================


class LLMClient(Protocol):
    """LLM 客户端协议。

    必须实现：
        - v1.1 兼容: :meth:`parse_query` / :meth:`summarize`；
        - v1.2 D4 : :meth:`chat_with_tools` 用于 agent loop。
    """

    def parse_query(self, query: str, available_filters: list[str]) -> ParsedQuery:
        """将自然语言解析为结构化检索参数。"""
        ...

    def summarize(self, query: str, hits: list[dict[str, Any]]) -> str:
        """根据检索结果生成自然语言总结。"""
        ...

    def chat_with_tools(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ChatResponse:
        """带工具协议的 chat 调用。"""
        ...


class MockLLMClient:
    """纯规则 LLM 客户端：默认启用，无任何外部依赖。

    ``chat_with_tools`` 的决策策略:
        - 已存在 tool result：进入 “收尾” 模式，调用 :func:`tool_summarize_results`
          风格生成最终中文回答；
        - 用户提到 “列出/list/所有数据集/可用数据集”：决定调用 ``list_datasets``；
        - 用户给出 ``cell_id``：决定调用 ``search_by_cell_id``（dataset_id 优先
          从系统提示读取，否则从问题里抓 “数据集 #N”，否则默认 1）；
        - 用户出现可识别的 metadata 关键词且 ``dataset_id`` 可知：决定调用
          ``filter_cells``；
        - 其它情况：直接 ``stop`` 给出 hint 文案。
    """

    # ---------- v1.1 兼容方法 ----------

    def parse_query(self, query: str, available_filters: list[str]) -> ParsedQuery:
        """基于关键词词典的规则解析。"""
        text = query.lower()
        filters = _match_keyword(text, available_filters)
        top_k = _extract_top_k(text, default=10)
        cell_id = _extract_cell_id(query)
        intent_parts = []
        if cell_id:
            intent_parts.append(f"按 cell_id={cell_id} 检索相似细胞")
        elif filters:
            cond = ", ".join(f"{k}={v}" for k, v in filters.items())
            intent_parts.append(f"在 {cond} 子集中寻找代表样本")
        else:
            intent_parts.append("通用相似度检索")
        return ParsedQuery(
            cell_id=cell_id,
            filters=filters,
            top_k=top_k,
            intent="；".join(intent_parts),
        )

    def summarize(self, query: str, hits: list[dict[str, Any]]) -> str:
        """模板化总结：统计命中细胞的 ``cell_type`` 分布并拼接说明。"""
        if not hits:
            return f"未能为查询「{query}」找到匹配的细胞。"
        counts: Counter[str] = Counter()
        tissues: Counter[str] = Counter()
        for h in hits:
            meta = h.get("meta") or {}
            ct = meta.get("cell_type")
            if ct:
                counts[str(ct)] += 1
            ts = meta.get("tissue")
            if ts:
                tissues[str(ts)] += 1
        parts = [f"为您找到 {len(hits)} 个与「{query}」最相似的细胞"]
        if counts:
            top = "、".join(f"{k} ({v})" for k, v in counts.most_common(3))
            parts.append(f"主要细胞类型为 {top}")
        if tissues:
            top_t = "、".join(f"{k} ({v})" for k, v in tissues.most_common(2))
            parts.append(f"组织分布以 {top_t} 为主")
        parts.append(f"排名第一的 cell_id 为 {hits[0]['cell_id']}")
        return "；".join(parts) + "。"

    # ---------- v1.2 D4 ----------

    def chat_with_tools(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ChatResponse:
        """规则化模拟 LLM Function Calling。

        参考 :data:`TOOLS_SCHEMA`，但本实现仅识别其中四个关键工具
        （``list_datasets``、``search_by_cell_id``、``filter_cells``、收尾文案）。
        """
        del tools  # mock 不使用 schema，按本类规则决定

        # 1. 找 system 提示里的上下文 dataset_id（由 chat_with_tools 拼装时写入）
        context_dataset_id: int | None = None
        for m in messages:
            if m.get("role") == "system":
                content = str(m.get("content") or "")
                m_match = re.search(r"current_dataset_id\s*[:=]\s*(\d+)", content)
                if m_match:
                    try:
                        context_dataset_id = int(m_match.group(1))
                    except ValueError:
                        context_dataset_id = None
                break

        # 2. 定位最近一个 user 消息及其在 messages 列表中的索引
        user_query = ""
        last_user_idx = -1
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                user_query = str(messages[i].get("content") or "")
                last_user_idx = i
                break

        # 3. 只收集 "最近 user 消息之后" 的 tool result —— 历史 tool result 不影响新一轮决策。
        #    这样多轮对话中每一轮 user 提问都能触发新的 tool_call，模拟真实 LLM 行为。
        tool_results: list[dict[str, Any]] = []
        for m in messages[last_user_idx + 1 :] if last_user_idx >= 0 else []:
            if m.get("role") == "tool":
                content = m.get("content")
                if isinstance(content, str):
                    try:
                        parsed_result = json.loads(content)
                    except json.JSONDecodeError:
                        parsed_result = {"raw": content}
                else:
                    parsed_result = content if isinstance(content, dict) else {}
                tool_results.append({"name": m.get("name") or "", "result": parsed_result})

        # 4. 若本轮已经有 tool result，则进入 “收尾” 模式
        if tool_results:
            return ChatResponse(
                finish_reason="stop",
                content=self._compose_final_answer(user_query, tool_results),
            )

        # 5. 否则按规则决定第一轮 tool_call
        text_lower = user_query.lower()
        if any(
            kw in text_lower
            for kw in (
                "list dataset",
                "list datasets",
                "列出数据集",
                "所有数据集",
                "可用数据集",
                "有哪些数据集",
            )
        ):
            return ChatResponse(
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCall(id=f"call_{uuid.uuid4().hex[:8]}", name="list_datasets", arguments={})
                ],
            )

        cell_id = _extract_cell_id(user_query)
        dataset_id = _extract_dataset_id(user_query) or context_dataset_id
        top_k = _extract_top_k(user_query, default=10)
        if cell_id and dataset_id:
            return ChatResponse(
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCall(
                        id=f"call_{uuid.uuid4().hex[:8]}",
                        name="search_by_cell_id",
                        arguments={
                            "dataset_id": dataset_id,
                            "cell_id": cell_id,
                            "top_k": top_k,
                        },
                    )
                ],
            )

        # 没有 dataset_id 但用户问 cell_id 相似：先 list_datasets
        if cell_id:
            return ChatResponse(
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCall(
                        id=f"call_{uuid.uuid4().hex[:8]}",
                        name="list_datasets",
                        arguments={},
                    )
                ],
            )

        # 有 metadata 关键词且 dataset_id 可知：调 filter_cells
        keyword_filters = _match_keyword(text_lower, list(_KEYWORD_HINTS.keys()))
        if keyword_filters and dataset_id:
            return ChatResponse(
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCall(
                        id=f"call_{uuid.uuid4().hex[:8]}",
                        name="filter_cells",
                        arguments={
                            "dataset_id": dataset_id,
                            "filters": keyword_filters,
                            "limit": 20,
                        },
                    )
                ],
            )

        # fallback：直接给提示
        return ChatResponse(
            finish_reason="stop",
            content=(
                "我可以帮你列出数据集、按 cell_id 搜索相似细胞或按 metadata 过滤。"
                "请提供数据集 ID 或具体 cell_id。"
            ),
        )

    @staticmethod
    def _compose_final_answer(user_query: str, tool_results: list[dict[str, Any]]) -> str:
        """根据已有工具结果生成最终自然语言答案。

        策略:
            - 若任一工具结果含 ``hits``：复用 :meth:`summarize` 的统计模板；
            - 若工具结果含 ``datasets``：列出名字 + ID；
            - 若工具结果含 ``matched_count``：报告过滤匹配数。
        """
        parts: list[str] = []
        for item in tool_results:
            result = item["result"]
            if isinstance(result, dict) and result.get("error"):
                parts.append(f"工具 {item['name']} 报错: {result['error']}")
                continue
            if isinstance(result, dict) and "datasets" in result:
                ds = result["datasets"]
                names = "、".join(f"{d.get('name')}(#{d.get('id')})" for d in ds[:5])
                parts.append(
                    f"当前共有 {len(ds)} 个可用数据集" + (f"，主要包括 {names}" if names else "")
                )
            if isinstance(result, dict) and "hits" in result:
                hits = result.get("hits") or []
                if hits:
                    counts: Counter[str] = Counter()
                    for h in hits:
                        meta = h.get("meta") or h.get("metadata") or {}
                        ct = meta.get("cell_type") if isinstance(meta, dict) else None
                        if ct:
                            counts[str(ct)] += 1
                    seg = f"find top matches: 共 {len(hits)} 个相似细胞"
                    if counts:
                        top = "、".join(f"{k} ({v})" for k, v in counts.most_common(3))
                        seg += f"，主要细胞类型 {top}"
                    seg += f"，代表 cell_id {hits[0].get('cell_id')}"
                    parts.append(seg)
            if isinstance(result, dict) and "matched_count" in result:
                parts.append(
                    f"按过滤条件 {result.get('filters')} 命中 {result['matched_count']} 个细胞"
                )
        if not parts:
            return f"已根据「{user_query}」检索完毕，但没有可总结的信息。"
        return "；".join(parts) + "。"


# =============================================================================
# 真实 LLM 客户端
# =============================================================================


_SYSTEM_PROMPT = (
    "你是一个生物信息助手，负责把用户的自然语言查询翻译成单细胞数据集检索参数。"
    "必须以严格 JSON 返回，键包括 cell_id (string|null)、filters (object)、top_k (int)、intent (string)。"
    "filters 的键只能来自给定的 available_filters 列表，值为字符串字面量。"
)


_AGENT_SYSTEM_PROMPT = (
    "你是一个单细胞数据助手。你可以调用以下工具完成任务："
    "list_datasets / search_by_cell_id / search_by_vector / filter_cells / summarize_results。"
    "请按用户问题自主决策调用顺序，必要时多轮调用；当信息足够时直接回答用户。"
    "回答需简洁中文，包含命中数量、主要细胞类型、代表 cell_id 等关键事实。"
)


def _safe_json_loads(payload: str) -> dict[str, Any]:
    """从 LLM 返回中解析 JSON；容忍包裹的代码块或前后噪声。"""
    payload = payload.strip()
    if payload.startswith("```"):
        payload = re.sub(r"^```(?:json)?", "", payload).strip()
        payload = re.sub(r"```$", "", payload).strip()
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", payload, flags=re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise


def _coerce_parsed(
    raw: dict[str, Any], available_filters: list[str], default_top_k: int
) -> ParsedQuery:
    """把 LLM 返回的 dict 规范化为 :class:`ParsedQuery`。"""
    filters_raw = raw.get("filters") or {}
    if not isinstance(filters_raw, dict):
        filters_raw = {}
    allowed = set(available_filters)
    filters = {
        k: v for k, v in filters_raw.items() if k in allowed and isinstance(v, (str, int, float))
    }
    top_k_raw = raw.get("top_k") or default_top_k
    try:
        top_k = max(1, min(int(top_k_raw), 100))
    except (TypeError, ValueError):
        top_k = default_top_k
    cell_id = raw.get("cell_id")
    if cell_id is not None and not isinstance(cell_id, str):
        cell_id = str(cell_id)
    return ParsedQuery(
        cell_id=cell_id,
        filters=filters,
        top_k=top_k,
        intent=str(raw.get("intent") or ""),
    )


class AnthropicClient:
    """基于 anthropic SDK 的 Claude Opus 客户端。

    使用官方 ``anthropic.Anthropic.messages.create`` 接口：

        - ``parse_query``        : 通过 ``system`` prompt 约束模型返回严格 JSON；
        - ``summarize``          : 基于命中 JSON 生成 3-5 句中文总结；
        - ``chat_with_tools``    : 走 Anthropic 原生 ``tools`` 协议，
          解析 ``stop_reason='tool_use'`` 与 ``content`` 中的 ``tool_use`` block。

    任意阶段失败（API 异常、JSON 解析失败、SDK 缺失）均回退到 :class:`MockLLMClient`。
    """

    def __init__(self, model: str, api_key: str, max_tokens: int = 1024) -> None:
        """初始化 Anthropic 客户端。

        Args:
            model: Claude 模型名，例如 ``claude-opus-4-7``（最新 GA flagship）。
            api_key: Anthropic API Key。
            max_tokens: 单次响应最大生成 token 数；总结场景 1024 通常足够。

        Raises:
            RuntimeError: 缺少 API Key 或 SDK 不可用时抛出。
        """
        if not api_key:
            raise RuntimeError("AnthropicClient 需要 ANTHROPIC_API_KEY 或 LLM_API_KEY")
        try:
            from anthropic import Anthropic  # type: ignore  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("缺少 anthropic SDK，请先安装 anthropic>=0.40") from exc
        self.model = model
        self._max_tokens = max_tokens
        self._client = Anthropic(api_key=api_key)

    def _call(self, prompt: str, *, system: str = _SYSTEM_PROMPT) -> str:
        """同步调用 ``messages.create`` 并返回首个 text block 的纯文本。"""
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self._max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        for block in getattr(response, "content", []) or []:
            text = getattr(block, "text", None)
            if isinstance(text, str) and text:
                return text
        raise RuntimeError(f"Anthropic 返回为空: {response}")

    def parse_query(self, query: str, available_filters: list[str]) -> ParsedQuery:
        """让 Claude 把自然语言解析为结构化检索参数，强制 JSON 输出。"""
        prompt = (
            f"available_filters = {available_filters}\n"
            f"用户问题: {query}\n"
            '请仅返回 JSON，例如 {"cell_id": null, "filters": {"cell_type": "hepatocyte"}, '
            '"top_k": 10, "intent": "..."}'
        )
        try:
            return _coerce_parsed(
                _safe_json_loads(self._call(prompt)),
                available_filters,
                default_top_k=10,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Anthropic parse_query 失败，回退 mock: %s", exc)
            return MockLLMClient().parse_query(query, available_filters)

    def summarize(self, query: str, hits: list[dict[str, Any]]) -> str:
        """让 Claude 基于命中 JSON 用中文生成 3-5 句总结。"""
        digest = json.dumps(hits[:10], ensure_ascii=False, default=str)
        prompt = (
            f"用户原始问题: {query}\n"
            f"以下是相似细胞检索结果（JSON，至多 10 条）:\n{digest}\n"
            "请用中文给出 3-5 句总结，包含命中数量、主要细胞类型、组织/疾病分布与代表 cell_id。"
        )
        try:
            return self._call(
                prompt,
                system="你是一个生物信息助手，用简洁中文回答用户问题。",
            ).strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Anthropic summarize 失败，回退 mock: %s", exc)
            return MockLLMClient().summarize(query, hits)

    def chat_with_tools(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ChatResponse:
        """调用 Anthropic 原生 function calling 协议。

        Anthropic 的 ``messages.create`` 接受 ``tools=[{name, description, input_schema}]``
        参数，命中时返回 ``stop_reason='tool_use'`` 且 ``content`` 包含 ``tool_use``
        block。本实现：

        - 把 OpenAI 风格 ``messages`` 转 Anthropic 风格（system 提取，role 保留，
          tool result 转为 ``user`` + ``tool_result`` block）；
        - 解析 ``tool_use`` block 转 :class:`ToolCall`，``text`` block 转 ``content``。
        """
        try:
            system_text = _AGENT_SYSTEM_PROMPT
            anthropic_messages: list[dict[str, Any]] = []
            for m in messages:
                role = m.get("role")
                if role == "system":
                    system_text = str(m.get("content") or system_text)
                    continue
                if role == "tool":
                    # 转为 user content block: type=tool_result
                    anthropic_messages.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": str(m.get("tool_call_id") or ""),
                                    "content": str(m.get("content") or ""),
                                }
                            ],
                        }
                    )
                    continue
                if role == "assistant" and m.get("tool_calls"):
                    blocks: list[dict[str, Any]] = []
                    if m.get("content"):
                        blocks.append({"type": "text", "text": str(m["content"])})
                    for tc in m["tool_calls"]:
                        fn = tc.get("function", {})
                        arguments = fn.get("arguments")
                        if isinstance(arguments, str):
                            try:
                                arguments = json.loads(arguments)
                            except json.JSONDecodeError:
                                arguments = {}
                        blocks.append(
                            {
                                "type": "tool_use",
                                "id": str(tc.get("id") or ""),
                                "name": str(fn.get("name", "")),
                                "input": arguments or {},
                            }
                        )
                    anthropic_messages.append({"role": "assistant", "content": blocks})
                    continue
                anthropic_messages.append(
                    {"role": role or "user", "content": str(m.get("content") or "")}
                )

            response = self._client.messages.create(
                model=self.model,
                max_tokens=self._max_tokens,
                system=system_text,
                tools=tools,
                messages=anthropic_messages,
            )
            tool_calls: list[ToolCall] = []
            text_parts: list[str] = []
            for block in getattr(response, "content", []) or []:
                btype = getattr(block, "type", None) or (
                    block.get("type") if isinstance(block, dict) else None
                )
                if btype == "tool_use":
                    name = getattr(block, "name", None) or block.get("name")
                    input_ = getattr(block, "input", None) or block.get("input") or {}
                    block_id = (
                        getattr(block, "id", None)
                        or block.get("id")
                        or f"call_{uuid.uuid4().hex[:8]}"
                    )
                    tool_calls.append(
                        ToolCall(
                            id=str(block_id),
                            name=str(name),
                            arguments=dict(input_),
                        )
                    )
                elif btype == "text":
                    text = getattr(block, "text", None) or block.get("text") or ""
                    if text:
                        text_parts.append(str(text))
            if tool_calls:
                return ChatResponse(
                    finish_reason="tool_calls",
                    tool_calls=tool_calls,
                    content="\n".join(text_parts),
                )
            return ChatResponse(finish_reason="stop", content="\n".join(text_parts))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Anthropic chat_with_tools 失败，回退 mock: %s", exc)
            return MockLLMClient().chat_with_tools(messages, tools)


def get_llm_client() -> LLMClient:
    """根据 :data:`settings.LLM_PROVIDER` 返回对应的 LLM 客户端实例。

    Returns:
        LLMClient: 满足 :class:`LLMClient` 协议的具体客户端。
        构造真实客户端失败（缺 Key/SDK）时自动回退到 :class:`MockLLMClient`。
    """
    provider = settings.LLM_PROVIDER
    try:
        if provider == "anthropic":
            api_key = settings.ANTHROPIC_API_KEY or settings.LLM_API_KEY
            model = settings.LLM_MODEL or "claude-opus-4-7"
            return AnthropicClient(model=model, api_key=api_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("初始化 LLM 客户端失败（provider=%s），回退 mock: %s", provider, exc)
    return MockLLMClient()


# =============================================================================
# v1.1 兼容: rag_answer 主流程（保留以便其它模块/测试调用）
# =============================================================================


def _resolve_dataset_dir(dataset: Dataset) -> str:
    """从 :class:`Dataset` 中解析数据集制品目录。"""
    if dataset.vectors_path:
        path = dataset.vectors_path
        if os.path.isdir(path):
            return path
        return os.path.dirname(path) or path
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"数据集 {dataset.id} 缺少预处理向量路径",
    )


async def _pick_index_record(
    db: AsyncSession, dataset_id: int, index_id: int | None
) -> IndexRecord:
    """选取索引记录：指定 ID 或最新 ready。"""
    if index_id is not None:
        record = await db.get(IndexRecord, index_id)
        if record is None or record.dataset_id != dataset_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"索引不存在: {index_id}"
            )
    else:
        stmt = (
            select(IndexRecord)
            .where(IndexRecord.dataset_id == dataset_id, IndexRecord.status == "ready")
            .order_by(desc(IndexRecord.created_at))
            .limit(1)
        )
        record = (await db.execute(stmt)).scalar_one_or_none()
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"数据集 {dataset_id} 暂无可用索引",
            )
    if record.status != "ready":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"索引尚未 ready: {record.status}",
        )
    return record


def _first_filter_match_index(metadata: Any, filters: dict[str, Any]) -> int | None:
    """返回 metadata 中首条命中 ``filters`` 的行索引，没有则返回 ``None``。"""
    if metadata is None or len(metadata) == 0 or not filters:
        return None
    mask = np.ones(len(metadata), dtype=bool)
    for k, v in filters.items():
        if k not in metadata.columns:
            return None
        mask &= (metadata[k] == v).to_numpy()
    indices = np.flatnonzero(mask)
    if len(indices) == 0:
        return None
    return int(indices[0])


def _run_search(
    dataset: Dataset,
    record: IndexRecord,
    parsed: ParsedQuery,
) -> dict[str, Any]:
    """根据解析参数执行实际检索（v1.1 兼容路径）。"""
    dataset_dir = _resolve_dataset_dir(dataset)
    backend = search_service.get_index_backend(
        index_id=record.id,
        dataset_dir=dataset_dir,
        backend_name=record.backend,
        metric=record.metric,
        dim=dataset.vector_dim,
        index_path=record.index_path,
    )

    if parsed.cell_id:
        try:
            return search_service.search_by_cell_id(
                query_cell_id=parsed.cell_id,
                dataset_dir=dataset_dir,
                backend=backend,
                top_k=parsed.top_k,
                filters=parsed.filters or None,
                metric=record.metric,
            )
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    artifacts = search_service.load_dataset_artifacts(dataset_dir)
    metadata = artifacts["metadata"]
    vectors: np.ndarray = artifacts["vectors"]

    representative_idx = _first_filter_match_index(metadata, parsed.filters)
    if representative_idx is None:
        representative_idx = 0
        exclude_cell_id = None
    else:
        exclude_cell_id = artifacts["cell_ids"][representative_idx]

    query_vector = vectors[representative_idx]
    return search_service.search_by_vector(
        query_vector=query_vector,
        dataset_dir=dataset_dir,
        backend=backend,
        top_k=parsed.top_k,
        filters=parsed.filters or None,
        exclude_cell_id=exclude_cell_id,
        metric=record.metric,
    )


async def rag_answer(
    db: AsyncSession,
    user_id: int,
    request: RagQueryRequest,
    llm: LLMClient | None = None,
) -> RagResponse:
    """v1.1 兼容 RAG 流程：parse → search → summarize。"""
    start = time.perf_counter()
    dataset = await db.get(Dataset, request.dataset_id)
    if dataset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"数据集不存在: {request.dataset_id}"
        )
    if dataset.owner_id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该数据集")
    if dataset.status not in {"ready", "preprocessing"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"数据集尚未就绪，当前状态: {dataset.status}",
        )

    record = await _pick_index_record(db, request.dataset_id, request.index_id)

    available_filters: list[str] = list(dataset.meta_columns or [])
    if not available_filters:
        dataset_dir = _resolve_dataset_dir(dataset)
        try:
            artifacts = search_service.load_dataset_artifacts(dataset_dir)
            available_filters = [str(c) for c in artifacts["metadata"].columns]
        except Exception as exc:  # noqa: BLE001
            logger.warning("无法从制品推断 available_filters: %s", exc)
            available_filters = []

    llm_client = llm or get_llm_client()
    parsed = llm_client.parse_query(request.query, available_filters)
    if request.top_k and not parsed.top_k:
        parsed = parsed.model_copy(update={"top_k": request.top_k})
    parsed = parsed.model_copy(update={"top_k": min(parsed.top_k, request.top_k or parsed.top_k)})

    payload = await asyncio.to_thread(_run_search, dataset, record, parsed)
    hits: list[dict[str, Any]] = list(payload.get("results", []))
    answer = llm_client.summarize(request.query, hits)
    total_ms = (time.perf_counter() - start) * 1000.0

    return RagResponse(
        parsed=parsed,
        hits=hits,
        answer=answer,
        query_time_ms=float(total_ms),
    )


# =============================================================================
# v1.2 D4: chat_with_tools agent loop
# =============================================================================


def _tool_summary(name: str, result: dict[str, Any]) -> str:
    """从工具执行结果生成简短摘要，供 ``tool_trace`` 给前端展示。"""
    if "error" in result and result["error"]:
        return f"error: {result['error']}"
    if name == "list_datasets":
        ds = result.get("datasets") or []
        return f"datasets={len(ds)}"
    if name in {"search_by_cell_id", "search_by_vector"}:
        hits = result.get("hits") or []
        return f"hits={len(hits)}"
    if name == "filter_cells":
        return f"matched={result.get('matched_count', 0)}"
    if name == "summarize_results":
        return f"summary_chars={len(str(result.get('summary', '')))}"
    return "ok"


async def _load_history_messages(db: AsyncSession, session_id: int) -> list[RagMessage]:
    """加载 session 已有 messages，按 id 升序。"""
    stmt = (
        select(RagMessage).where(RagMessage.session_id == session_id).order_by(RagMessage.id.asc())
    )
    return list((await db.execute(stmt)).scalars().all())


def _history_to_llm_messages(history: list[RagMessage]) -> list[dict[str, Any]]:
    """把 DB 中的历史 RagMessage 转为 OpenAI 风格 LLM messages 列表。"""
    out: list[dict[str, Any]] = []
    for m in history:
        if m.role == "user":
            out.append({"role": "user", "content": m.content or ""})
        elif m.role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": m.content or ""}
            if m.tool_calls_json:
                try:
                    raw = json.loads(m.tool_calls_json)
                    entry["tool_calls"] = [
                        {
                            "id": tc.get("id"),
                            "type": "function",
                            "function": {
                                "name": tc.get("name"),
                                "arguments": json.dumps(
                                    tc.get("arguments") or {}, ensure_ascii=False
                                ),
                            },
                        }
                        for tc in raw
                    ]
                except json.JSONDecodeError:
                    pass
            out.append(entry)
        elif m.role == "tool":
            if not m.tool_results_json:
                continue
            try:
                results = json.loads(m.tool_results_json)
            except json.JSONDecodeError:
                continue
            for r in results:
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": r.get("tool_call_id") or "",
                        "name": r.get("name") or "",
                        "content": json.dumps(r.get("result") or {}, ensure_ascii=False),
                    }
                )
        elif m.role == "system":
            out.append({"role": "system", "content": m.content or ""})
    return out


def _collect_citations(
    tool_name: str, arguments: dict[str, Any], result: dict[str, Any]
) -> list[RagCitation]:
    """从工具结果中抽取引用，仅当结果含 ``hits``/``cell_ids`` 时。"""
    ds_id = arguments.get("dataset_id")
    try:
        ds_id_int = int(ds_id) if ds_id is not None else None
    except (TypeError, ValueError):
        ds_id_int = None
    citations: list[RagCitation] = []
    if "hits" in result:
        for h in result.get("hits", []) or []:
            cid = h.get("cell_id")
            if cid:
                citations.append(RagCitation(cell_id=str(cid), dataset_id=ds_id_int))
    if tool_name == "filter_cells":
        for cid in result.get("cell_ids", []) or []:
            citations.append(RagCitation(cell_id=str(cid), dataset_id=ds_id_int))
    return citations


async def chat_with_tools(
    db: AsyncSession,
    user: User,
    request: RagChatRequest,
    llm: LLMClient | None = None,
) -> RagChatResponse:
    """LLM Function Calling Agent loop 主流程。

    Args:
        db: 异步数据库会话。
        user: 当前请求用户。
        request: 用户请求。
        llm: 可选的 LLM 客户端注入，默认由 :func:`get_llm_client` 提供。

    Returns:
        RagChatResponse: 含 session_id / answer / tool_trace / citations / iterations。

    Raises:
        HTTPException: ``session_id`` 提供但不属于当前用户时返回 ``404``。
    """
    start = time.perf_counter()
    llm_client = llm or get_llm_client()

    # 1. 加载或创建 session
    if request.session_id is not None:
        rag_session = await db.get(RagSession, request.session_id)
        if rag_session is None or rag_session.user_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在或无权访问"
            )
        history = await _load_history_messages(db, rag_session.id)
    else:
        title = request.query.strip()[:50] or "新对话"
        rag_session = RagSession(user_id=user.id, title=title)
        db.add(rag_session)
        await db.flush()
        history = []

    # 2. 持久化本轮 user 消息
    user_msg = RagMessage(session_id=rag_session.id, role="user", content=request.query)
    db.add(user_msg)
    await db.flush()

    # 3. 组装 LLM messages
    system_prompt_parts = [_AGENT_SYSTEM_PROMPT]
    if request.dataset_id is not None:
        system_prompt_parts.append(f"current_dataset_id={request.dataset_id}")
    llm_messages: list[dict[str, Any]] = [
        {"role": "system", "content": "\n".join(system_prompt_parts)}
    ]
    llm_messages.extend(_history_to_llm_messages(history))
    llm_messages.append({"role": "user", "content": request.query})

    tool_trace: list[ToolTraceItem] = []
    citations: list[RagCitation] = []
    final_answer = ""
    finish_reason = "stop"
    iterations = 0

    # 4. Agent loop
    for iteration in range(request.max_iterations):
        iterations = iteration + 1
        response: ChatResponse = await asyncio.to_thread(
            llm_client.chat_with_tools, llm_messages, TOOLS_SCHEMA
        )

        if response.finish_reason == "tool_calls" and response.tool_calls:
            # 4.1 持久化 assistant 决策
            tool_calls_payload = [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                for tc in response.tool_calls
            ]
            assistant_msg = RagMessage(
                session_id=rag_session.id,
                role="assistant",
                content=response.content or None,
                tool_calls_json=json.dumps(tool_calls_payload, ensure_ascii=False),
            )
            db.add(assistant_msg)
            await db.flush()

            llm_messages.append(
                {
                    "role": "assistant",
                    "content": response.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                            },
                        }
                        for tc in response.tool_calls
                    ],
                }
            )

            # 4.2 执行所有 tool calls
            tool_results_payload: list[dict[str, Any]] = []
            for tc in response.tool_calls:
                result = await execute_tool(tc.name, tc.arguments, db, user)
                tool_results_payload.append(
                    {"tool_call_id": tc.id, "name": tc.name, "result": result}
                )
                ok = not (isinstance(result, dict) and result.get("error"))
                tool_trace.append(
                    ToolTraceItem(
                        name=tc.name,
                        arguments=tc.arguments,
                        summary=_tool_summary(tc.name, result if isinstance(result, dict) else {}),
                        ok=ok,
                    )
                )
                if isinstance(result, dict):
                    citations.extend(_collect_citations(tc.name, tc.arguments, result))

                llm_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tc.name,
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    }
                )

            # 4.3 持久化 tool results
            tool_msg = RagMessage(
                session_id=rag_session.id,
                role="tool",
                tool_results_json=json.dumps(tool_results_payload, ensure_ascii=False, default=str),
            )
            db.add(tool_msg)
            await db.flush()
            continue

        # 4.4 收尾：finish_reason='stop' 或 tool_calls 为空
        final_answer = response.content or ""
        assistant_msg = RagMessage(
            session_id=rag_session.id,
            role="assistant",
            content=final_answer,
        )
        db.add(assistant_msg)
        await db.flush()
        finish_reason = "stop"
        break
    else:
        finish_reason = "max_iterations"
        final_answer = "已达到最大工具调用轮数，请缩小问题范围后重试。"
        assistant_msg = RagMessage(
            session_id=rag_session.id,
            role="assistant",
            content=final_answer,
        )
        db.add(assistant_msg)
        await db.flush()

    # 5. 触发 updated_at + commit
    rag_session.updated_at = func.now()  # type: ignore[assignment]
    await db.commit()

    # 6. citations 去重（按 (cell_id, dataset_id)）
    seen: set[tuple[str, int | None]] = set()
    dedup: list[RagCitation] = []
    for c in citations:
        key = (c.cell_id, c.dataset_id)
        if key in seen:
            continue
        seen.add(key)
        dedup.append(c)

    total_ms = (time.perf_counter() - start) * 1000.0
    return RagChatResponse(
        session_id=rag_session.id,
        answer=final_answer,
        tool_trace=tool_trace,
        citations=dedup,
        iterations=iterations,
        finish_reason=finish_reason,
        query_time_ms=float(total_ms),
    )


# =============================================================================
# session 查询辅助函数（供 API 路由直接复用）
# =============================================================================


async def list_user_sessions(db: AsyncSession, user_id: int) -> list[RagSessionOut]:
    """列出当前用户全部 RAG 会话，按 ``updated_at`` 倒序。"""
    stmt = (
        select(
            RagSession.id,
            RagSession.user_id,
            RagSession.title,
            RagSession.created_at,
            RagSession.updated_at,
            func.count(RagMessage.id).label("message_count"),
        )
        .join(RagMessage, RagMessage.session_id == RagSession.id, isouter=True)
        .where(RagSession.user_id == user_id)
        .group_by(
            RagSession.id,
            RagSession.user_id,
            RagSession.title,
            RagSession.created_at,
            RagSession.updated_at,
        )
        .order_by(RagSession.updated_at.desc(), RagSession.id.desc())
    )
    rows = (await db.execute(stmt)).all()
    out: list[RagSessionOut] = []
    for row in rows:
        out.append(
            RagSessionOut(
                id=int(row.id),
                user_id=int(row.user_id),
                title=row.title,
                created_at=row.created_at,
                updated_at=row.updated_at,
                message_count=int(row.message_count or 0),
            )
        )
    return out


def _message_row_to_out(m: RagMessage) -> dict[str, Any]:
    """把 :class:`RagMessage` 转换为 :class:`RagMessageOut` 字段字典。"""
    tool_calls: list[dict[str, Any]] = []
    if m.tool_calls_json:
        try:
            tool_calls = json.loads(m.tool_calls_json) or []
        except json.JSONDecodeError:
            tool_calls = []
    tool_results: list[dict[str, Any]] = []
    if m.tool_results_json:
        try:
            tool_results = json.loads(m.tool_results_json) or []
        except json.JSONDecodeError:
            tool_results = []
    return {
        "id": m.id,
        "session_id": m.session_id,
        "role": m.role,
        "content": m.content,
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "created_at": m.created_at,
    }


async def get_session_detail(db: AsyncSession, user_id: int, session_id: int) -> RagSessionDetail:
    """获取会话详情含全部消息。

    Raises:
        HTTPException: 会话不存在或非本人时返回 ``404``。
    """
    rag_session = await db.get(RagSession, session_id)
    if rag_session is None or rag_session.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在或无权访问")
    messages = await _load_history_messages(db, session_id)
    return RagSessionDetail(
        id=rag_session.id,
        user_id=rag_session.user_id,
        title=rag_session.title,
        created_at=rag_session.created_at,
        updated_at=rag_session.updated_at,
        messages=[_message_row_to_out(m) for m in messages],  # type: ignore[arg-type]
    )
