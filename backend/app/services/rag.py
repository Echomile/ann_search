"""RAG 服务：将自然语言查询解析为结构化参数，调用 ANN 检索，再用 LLM 总结。

模块组织：
    - :class:`LLMClient`：客户端协议，定义 ``parse_query`` 与 ``summarize``。
    - :class:`MockLLMClient`：纯规则解析实现，默认启用，便于无外网测试。
    - :class:`DashScopeLLMClient` / :class:`OpenAILLMClient` / :class:`AnthropicClient`：
      真实 LLM 实现，覆盖通义千问、OpenAI 兼容与 Claude Opus。
    - :func:`get_llm_client`：根据 :data:`settings.LLM_PROVIDER` 返回对应实例，失败回退 Mock。
    - :func:`rag_answer`：完整 RAG 主流程，供路由层 ``await`` 调用。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections import Counter
from typing import Any, Protocol

import numpy as np
from fastapi import HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.dataset import Dataset
from app.models.index_record import IndexRecord
from app.schemas.rag import ParsedQuery, RagQueryRequest, RagResponse
from app.services import search as search_service

logger = get_logger(__name__)


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
    """按关键词词典在自然语言中匹配可用的 metadata 过滤值。

    Args:
        text: 用户自然语言，已转为小写。
        available_filters: 当前数据集允许的 metadata 列名列表。

    Returns:
        dict[str, str]: ``{column: value}`` 形式的命中映射。
    """
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
    """识别用户是否显式提供 ``cell_id``。

    支持 ``cell_id=xxx``、``cell_id: "xxx"``、``cell_id "xxx"`` 等形式。
    """
    m = re.search(
        r"cell[_ ]?id\s*[:=]?\s*[\"']?([A-Za-z0-9][\w\-:.]*)[\"']?",
        text,
        flags=re.IGNORECASE,
    )
    if m:
        return m.group(1).strip(" \"'")
    return None


class LLMClient(Protocol):
    """LLM 客户端协议。

    实现需要保证 :meth:`parse_query` 返回合法的 :class:`ParsedQuery`，
    :meth:`summarize` 返回非空字符串。
    """

    def parse_query(self, query: str, available_filters: list[str]) -> ParsedQuery:
        """将自然语言解析为结构化检索参数。"""
        ...

    def summarize(self, query: str, hits: list[dict[str, Any]]) -> str:
        """根据检索结果生成自然语言总结。"""
        ...


class MockLLMClient:
    """纯规则 LLM 客户端，默认启用，便于测试不依赖外部 API。"""

    def parse_query(self, query: str, available_filters: list[str]) -> ParsedQuery:
        """基于关键词词典的规则解析。

        Args:
            query: 用户自然语言。
            available_filters: 数据集中可作为过滤条件的 metadata 列名。

        Returns:
            ParsedQuery: 结构化检索参数；当未命中任何关键词时 ``filters`` 为空。
        """
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


_SYSTEM_PROMPT = (
    "你是一个生物信息助手，负责把用户的自然语言查询翻译成单细胞数据集检索参数。"
    "必须以严格 JSON 返回，键包括 cell_id (string|null)、filters (object)、top_k (int)、intent (string)。"
    "filters 的键只能来自给定的 available_filters 列表，值为字符串字面量。"
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


class DashScopeLLMClient:
    """基于 dashscope SDK 的通义千问客户端。"""

    def __init__(self, model: str, api_key: str) -> None:
        """初始化客户端。

        Args:
            model: 模型名，例如 ``qwen-plus``。
            api_key: DashScope API Key。

        Raises:
            RuntimeError: 缺少 API Key 或 SDK 不可用时抛出。
        """
        if not api_key:
            raise RuntimeError("DashScopeLLMClient 需要 LLM_API_KEY")
        try:
            import dashscope  # type: ignore  # noqa: F401
        except ImportError as exc:  # pragma: no cover - 仅在缺包时触发
            raise RuntimeError("缺少 dashscope SDK，请先安装") from exc
        self.model = model
        self.api_key = api_key

    def _call(self, prompt: str) -> str:
        """同步调用 DashScope generation 接口，返回模型文本输出。"""
        import dashscope  # type: ignore

        response = dashscope.Generation.call(
            model=self.model,
            api_key=self.api_key,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            result_format="message",
        )
        try:
            return response.output.choices[0].message.content  # type: ignore[no-any-return]
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"DashScope 返回解析失败: {response}") from exc

    def parse_query(self, query: str, available_filters: list[str]) -> ParsedQuery:
        """调用大模型解析查询为 JSON 参数。"""
        prompt = (
            f"available_filters = {available_filters}\n"
            f"用户问题: {query}\n"
            '请仅返回 JSON，例如 {"cell_id": null, "filters": {"cell_type": "hepatocyte"}, "top_k": 10, "intent": "..."}'
        )
        try:
            raw_text = self._call(prompt)
            return _coerce_parsed(_safe_json_loads(raw_text), available_filters, default_top_k=10)
        except Exception as exc:  # noqa: BLE001
            logger.warning("DashScope parse_query 失败，回退 mock: %s", exc)
            return MockLLMClient().parse_query(query, available_filters)

    def summarize(self, query: str, hits: list[dict[str, Any]]) -> str:
        """调用大模型对检索结果生成自然语言总结。"""
        digest = json.dumps(hits[:10], ensure_ascii=False, default=str)
        prompt = (
            f"用户原始问题: {query}\n"
            f"以下是相似细胞检索结果（JSON，至多 10 条）:\n{digest}\n"
            "请用中文给出 3-5 句总结，包含命中数量、主要细胞类型、组织/疾病分布与代表 cell_id。"
        )
        try:
            return self._call(prompt).strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("DashScope summarize 失败，回退 mock: %s", exc)
            return MockLLMClient().summarize(query, hits)


class OpenAILLMClient:
    """基于 openai SDK 的客户端，兼容 DashScope 的 OpenAI 接口。"""

    def __init__(self, model: str, api_key: str, base_url: str = "") -> None:
        """初始化 OpenAI 兼容客户端。

        Args:
            model: 模型名，例如 ``gpt-4o-mini``。
            api_key: API Key。
            base_url: OpenAI 兼容 endpoint；为空时使用 SDK 默认值。

        Raises:
            RuntimeError: 缺少 API Key 或 SDK 不可用时抛出。
        """
        if not api_key:
            raise RuntimeError("OpenAILLMClient 需要 LLM_API_KEY")
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as exc:  # pragma: no cover - 仅在缺包时触发
            raise RuntimeError("缺少 openai SDK，请先安装") from exc
        self.model = model
        self._client = OpenAI(api_key=api_key, base_url=base_url or None)

    def _call(self, prompt: str) -> str:
        """同步调用 OpenAI ChatCompletion 接口。"""
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
        )
        return resp.choices[0].message.content or ""

    def parse_query(self, query: str, available_filters: list[str]) -> ParsedQuery:
        """调用大模型解析查询。"""
        prompt = (
            f"available_filters = {available_filters}\n"
            f"用户问题: {query}\n"
            '请仅返回 JSON，例如 {"cell_id": null, "filters": {"cell_type": "hepatocyte"}, "top_k": 10, "intent": "..."}'
        )
        try:
            return _coerce_parsed(
                _safe_json_loads(self._call(prompt)), available_filters, default_top_k=10
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("OpenAI parse_query 失败，回退 mock: %s", exc)
            return MockLLMClient().parse_query(query, available_filters)

    def summarize(self, query: str, hits: list[dict[str, Any]]) -> str:
        """调用大模型生成自然语言总结。"""
        digest = json.dumps(hits[:10], ensure_ascii=False, default=str)
        prompt = (
            f"用户原始问题: {query}\n"
            f"以下是相似细胞检索结果（JSON，至多 10 条）:\n{digest}\n"
            "请用中文给出 3-5 句总结，包含命中数量、主要细胞类型、组织/疾病分布与代表 cell_id。"
        )
        try:
            return self._call(prompt).strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("OpenAI summarize 失败，回退 mock: %s", exc)
            return MockLLMClient().summarize(query, hits)


class AnthropicClient:
    """基于 anthropic SDK 的 Claude Opus 客户端。

    使用官方 ``anthropic.Anthropic.messages.create`` 接口：

        - ``parse_query``：通过 ``system`` prompt 约束模型返回严格 JSON，再转 :class:`ParsedQuery`；
        - ``summarize``：要求模型基于命中 JSON 生成 3-5 句中文总结。

    任意阶段失败（API 异常、JSON 解析失败、SDK 缺失）均回退到 :class:`MockLLMClient`，
    确保 RAG 主流程不被外部依赖阻断。
    """

    def __init__(self, model: str, api_key: str, max_tokens: int = 1024) -> None:
        """初始化 Anthropic 客户端。

        Args:
            model: Claude 模型名，例如 ``claude-opus-4-20250514``。
            api_key: Anthropic API Key；为空时由 SDK 读取 ``ANTHROPIC_API_KEY`` 环境变量。
            max_tokens: 单次响应最大生成 token 数；总结场景 1024 通常足够。

        Raises:
            RuntimeError: 缺少 API Key 或 SDK 不可用时抛出，工厂层会捕获并回退 Mock。
        """
        if not api_key:
            raise RuntimeError("AnthropicClient 需要 ANTHROPIC_API_KEY 或 LLM_API_KEY")
        try:
            from anthropic import Anthropic  # type: ignore  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - 仅在缺包时触发
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


def get_llm_client() -> LLMClient:
    """根据 :data:`settings.LLM_PROVIDER` 返回对应的 LLM 客户端实例。

    Returns:
        LLMClient: 满足 :class:`LLMClient` 协议的具体客户端。
        构造真实客户端失败（缺 Key/SDK）时自动回退到 :class:`MockLLMClient`。
    """
    provider = settings.LLM_PROVIDER
    try:
        if provider == "dashscope":
            return DashScopeLLMClient(model=settings.LLM_MODEL, api_key=settings.LLM_API_KEY)
        if provider == "openai":
            return OpenAILLMClient(
                model=settings.LLM_MODEL,
                api_key=settings.LLM_API_KEY,
                base_url=settings.LLM_BASE_URL,
            )
        if provider == "anthropic":
            api_key = settings.ANTHROPIC_API_KEY or settings.LLM_API_KEY
            model = settings.LLM_MODEL or "claude-opus-4-20250514"
            return AnthropicClient(model=model, api_key=api_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("初始化 LLM 客户端失败（provider=%s），回退 mock: %s", provider, exc)
    return MockLLMClient()


def _resolve_dataset_dir(dataset: Dataset) -> str:
    """从 :class:`Dataset` 中解析数据集制品目录。

    与 :func:`app.api.v1.search._resolve_dataset_dir` 行为一致，
    但抛 :class:`HTTPException` 以便路由层直接透传。
    """
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
    """根据解析参数执行实际检索。

    策略：
        1. 若 ``parsed.cell_id`` 存在，调用 :func:`search_by_cell_id`；
        2. 否则在 ``filters`` 命中的子集里取第一条 cell 的向量作为查询向量，
           调用 :func:`search_by_vector`，从而实现 “找代表样本” 的语义；
        3. 上述均不可行时，使用全库第一条向量兜底，等价于一次随机相似查询。
    """
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
    """RAG 完整流程：parse → search → summarize。

    Args:
        db: 异步数据库会话。
        user_id: 当前登录用户 ID，用于权限校验。
        request: 用户请求。
        llm: 可注入的 LLM 客户端，缺省由 :func:`get_llm_client` 提供。

    Returns:
        RagResponse: 结构化解析、命中列表与自然语言总结。

    Raises:
        HTTPException: 数据集/索引不存在、不属于当前用户或未就绪时抛出。
    """
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
