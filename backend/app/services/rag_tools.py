"""RAG Agent 工具集（v1.2 D4 扩展功能）。

设计目标:
    LLM Function Calling Agent 风格下，LLM 自主决定调用哪个工具完成用户任务。
    本模块定义 5 个工具，覆盖 “列数据集 → 找细胞 → 过滤 → 总结” 的典型流程：

    - ``list_datasets``       : 列出当前用户可访问的全部 ready 数据集；
    - ``search_by_cell_id``   : 在指定数据集中以已知 cell_id 检索 Top-K 相似细胞；
    - ``search_by_vector``    : 在指定数据集中以查询向量检索 Top-K 相似细胞；
    - ``filter_cells``        : 按 metadata 等值条件过滤数据集，返回命中样本概览；
    - ``summarize_results``   : 对一组命中 cell 做模板化自然语言总结，
      在 LLM API 出错或需要轻量收尾时使用。

约定:
    - 所有工具函数都返回 **可 JSON 序列化的 dict**，便于直接作为 tool_result 写回
      LLM 上下文；错误以 ``{"error": "..."}`` 表示，绝不抛 :class:`HTTPException`，
      避免一次工具失败把整个 agent loop 打断。
    - 工具函数全部 ``async``（即使内部纯 numpy 计算也用 :func:`asyncio.to_thread`
      卸载到线程池），统一被 :func:`execute_tool` 调度。
    - 工具 schema 采用 Anthropic 原生 function calling 协议：
      ``{name, description, input_schema}``。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.dataset import Dataset
from app.models.index_record import IndexRecord
from app.models.user import User
from app.services import search as search_service

logger = get_logger(__name__)


TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "name": "list_datasets",
        "description": (
            "列出当前用户可访问的所有 ready 数据集，"
            "返回每个数据集的 id、name、cell_count、vector_dim 与 meta_columns。"
            "在不知道 dataset_id 时优先调用本工具。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "search_by_cell_id",
        "description": (
            "在指定数据集中根据细胞 ID 检索 Top-K 相似细胞。"
            "适合用户已知某个 cell_id 想找邻居的场景。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dataset_id": {
                    "type": "integer",
                    "description": "目标数据集 ID",
                },
                "cell_id": {
                    "type": "string",
                    "description": "查询细胞 ID",
                },
                "top_k": {
                    "type": "integer",
                    "description": "返回近邻数量，1~100",
                    "default": 10,
                },
            },
            "required": ["dataset_id", "cell_id"],
        },
    },
    {
        "name": "search_by_vector",
        "description": (
            "在指定数据集中以原始向量检索 Top-K 相似细胞。"
            "通常仅在工具链中由 LLM 显式提供向量时调用。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dataset_id": {
                    "type": "integer",
                    "description": "目标数据集 ID",
                },
                "query_vector": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "查询向量，长度需与数据集 vector_dim 一致",
                },
                "top_k": {
                    "type": "integer",
                    "description": "返回近邻数量，1~100",
                    "default": 10,
                },
            },
            "required": ["dataset_id", "query_vector"],
        },
    },
    {
        "name": "filter_cells",
        "description": (
            "按 metadata 等值条件过滤数据集，"
            "返回命中样本数量、首批 cell_id 列表以及 cell_type 等关键字段直方图。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dataset_id": {
                    "type": "integer",
                    "description": "目标数据集 ID",
                },
                "filters": {
                    "type": "object",
                    "description": '等值过滤条件，例如 {"cell_type": "T cell"}',
                    "additionalProperties": True,
                },
                "limit": {
                    "type": "integer",
                    "description": "返回 cell_id 数量上限，默认 20",
                    "default": 20,
                },
            },
            "required": ["dataset_id", "filters"],
        },
    },
    {
        "name": "summarize_results",
        "description": (
            "对一组检索命中条目（含 cell_id + metadata）做自然语言总结，"
            "包含数量、主要细胞类型、组织/疾病分布与代表 cell_id。"
            "当 LLM 想用结构化数据生成最终回答又不希望额外调用大模型时使用。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "hits": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "命中条目列表，每项至少含 cell_id 与 meta",
                },
                "query": {
                    "type": "string",
                    "description": "用户原始问题，用于装饰总结句式",
                    "default": "",
                },
            },
            "required": ["hits"],
        },
    },
]


def _resolve_dataset_dir(dataset: Dataset) -> str | None:
    """从 :class:`Dataset` 解析数据集制品目录。

    与 :mod:`app.services.rag` 中的同名辅助函数行为一致，
    但返回 ``None`` 表示路径缺失（工具层不抛 HTTPException）。
    """
    if not dataset.vectors_path:
        return None
    path = dataset.vectors_path
    if os.path.isdir(path):
        return path
    parent = os.path.dirname(path)
    return parent or path


async def _pick_index_record(session: AsyncSession, dataset_id: int) -> IndexRecord | None:
    """选取数据集最新的 ready 索引；找不到返回 ``None``。"""
    stmt = (
        select(IndexRecord)
        .where(IndexRecord.dataset_id == dataset_id, IndexRecord.status == "ready")
        .order_by(desc(IndexRecord.created_at))
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def tool_list_datasets(session: AsyncSession, user: User) -> dict[str, Any]:
    """列出当前用户可访问的所有 ready 数据集。

    Args:
        session: 异步数据库会话。
        user: 当前请求用户，用于权限过滤。

    Returns:
        dict[str, Any]: ``{"datasets": [{id, name, cell_count, vector_dim,
        vector_source, meta_columns}, ...]}``。
    """
    stmt = (
        select(Dataset)
        .where(Dataset.owner_id == user.id, Dataset.status == "ready")
        .order_by(Dataset.created_at.desc(), Dataset.id.desc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    return {
        "datasets": [
            {
                "id": int(d.id),
                "name": d.name,
                "cell_count": d.cell_count,
                "vector_dim": d.vector_dim,
                "vector_source": d.vector_source,
                "meta_columns": list(d.meta_columns or []),
            }
            for d in rows
        ]
    }


async def _load_dataset_for_user(
    session: AsyncSession, user: User, dataset_id: int
) -> tuple[Dataset | None, str | None]:
    """加载并校验数据集；返回 (dataset, error_msg)，二者必有其一为 ``None``。"""
    dataset = await session.get(Dataset, dataset_id)
    if dataset is None:
        return None, f"数据集不存在: {dataset_id}"
    if dataset.owner_id != user.id:
        return None, "无权访问该数据集"
    if dataset.status not in {"ready", "preprocessing"}:
        return None, f"数据集尚未就绪，当前状态: {dataset.status}"
    return dataset, None


async def tool_search_by_cell_id(
    session: AsyncSession,
    user: User,
    dataset_id: int,
    cell_id: str,
    top_k: int = 10,
) -> dict[str, Any]:
    """在指定数据集中按 cell_id 检索 Top-K 相似细胞。

    Args:
        session: 异步数据库会话。
        user: 当前请求用户。
        dataset_id: 目标数据集 ID。
        cell_id: 查询细胞 ID。
        top_k: 返回近邻数量，越界自动截断到 ``[1, 100]``。

    Returns:
        dict[str, Any]: ``{"hits": [...]}``；出错返回 ``{"error": ..., "hits": []}``。
    """
    top_k = max(1, min(int(top_k), 100))
    dataset, err = await _load_dataset_for_user(session, user, dataset_id)
    if err is not None or dataset is None:
        return {"error": err, "hits": []}
    record = await _pick_index_record(session, dataset_id)
    if record is None:
        return {"error": f"数据集 {dataset_id} 暂无可用索引", "hits": []}
    dataset_dir = _resolve_dataset_dir(dataset)
    if dataset_dir is None:
        return {"error": f"数据集 {dataset_id} 缺少预处理向量路径", "hits": []}

    try:
        backend = search_service.get_index_backend(
            index_id=record.id,
            dataset_dir=dataset_dir,
            backend_name=record.backend,
            metric=record.metric,
            dim=dataset.vector_dim,
            index_path=record.index_path,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("tool_search_by_cell_id 加载后端失败: %s", exc)
        return {"error": f"加载索引失败: {exc}", "hits": []}

    try:
        payload = await asyncio.to_thread(
            search_service.search_by_cell_id,
            query_cell_id=cell_id,
            dataset_dir=dataset_dir,
            backend=backend,
            top_k=top_k,
            metric=record.metric,
        )
    except KeyError as exc:
        return {"error": f"cell_id 不存在: {exc}", "hits": []}
    except Exception as exc:  # noqa: BLE001
        logger.warning("tool_search_by_cell_id 检索失败: %s", exc)
        return {"error": f"检索失败: {exc}", "hits": []}
    hits = list(payload.get("results", []))
    return {
        "dataset_id": dataset_id,
        "cell_id": cell_id,
        "top_k": top_k,
        "hits": hits,
    }


async def tool_search_by_vector(
    session: AsyncSession,
    user: User,
    dataset_id: int,
    query_vector: list[float],
    top_k: int = 10,
) -> dict[str, Any]:
    """在指定数据集中按向量检索 Top-K 相似细胞。

    Args:
        session: 异步数据库会话。
        user: 当前请求用户。
        dataset_id: 目标数据集 ID。
        query_vector: 查询向量，长度需与数据集 ``vector_dim`` 一致。
        top_k: 返回近邻数量，越界自动截断到 ``[1, 100]``。

    Returns:
        dict[str, Any]: ``{"hits": [...]}``；出错返回 ``{"error": ..., "hits": []}``。
    """
    top_k = max(1, min(int(top_k), 100))
    dataset, err = await _load_dataset_for_user(session, user, dataset_id)
    if err is not None or dataset is None:
        return {"error": err, "hits": []}
    if not query_vector:
        return {"error": "query_vector 不能为空", "hits": []}
    if dataset.vector_dim and len(query_vector) != dataset.vector_dim:
        return {
            "error": (
                f"query_vector 长度 {len(query_vector)} 与 vector_dim {dataset.vector_dim} 不一致"
            ),
            "hits": [],
        }
    record = await _pick_index_record(session, dataset_id)
    if record is None:
        return {"error": f"数据集 {dataset_id} 暂无可用索引", "hits": []}
    dataset_dir = _resolve_dataset_dir(dataset)
    if dataset_dir is None:
        return {"error": f"数据集 {dataset_id} 缺少预处理向量路径", "hits": []}

    try:
        backend = search_service.get_index_backend(
            index_id=record.id,
            dataset_dir=dataset_dir,
            backend_name=record.backend,
            metric=record.metric,
            dim=dataset.vector_dim,
            index_path=record.index_path,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("tool_search_by_vector 加载后端失败: %s", exc)
        return {"error": f"加载索引失败: {exc}", "hits": []}

    try:
        payload = await asyncio.to_thread(
            search_service.search_by_vector,
            query_vector=query_vector,
            dataset_dir=dataset_dir,
            backend=backend,
            top_k=top_k,
            metric=record.metric,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("tool_search_by_vector 检索失败: %s", exc)
        return {"error": f"检索失败: {exc}", "hits": []}
    return {
        "dataset_id": dataset_id,
        "top_k": top_k,
        "hits": list(payload.get("results", [])),
    }


async def tool_filter_cells(
    session: AsyncSession,
    user: User,
    dataset_id: int,
    filters: dict[str, Any],
    limit: int = 20,
) -> dict[str, Any]:
    """按 metadata 等值条件过滤数据集，返回命中样本概览。

    Args:
        session: 异步数据库会话。
        user: 当前请求用户。
        dataset_id: 目标数据集 ID。
        filters: 等值过滤条件字典，例如 ``{"cell_type": "T cell"}``。
        limit: 返回 cell_id 列表数量上限，默认 20，越界自动截断到 ``[1, 200]``。

    Returns:
        dict[str, Any]: 含 ``matched_count`` / ``cell_ids`` / ``cell_type_counts``
        / ``tissue_counts`` 等字段；出错时返回 ``{"error": ..., "matched_count": 0}``。
    """
    limit = max(1, min(int(limit), 200))
    dataset, err = await _load_dataset_for_user(session, user, dataset_id)
    if err is not None or dataset is None:
        return {"error": err, "matched_count": 0, "cell_ids": []}
    if not isinstance(filters, dict) or not filters:
        return {
            "error": "filters 不能为空",
            "matched_count": 0,
            "cell_ids": [],
        }
    dataset_dir = _resolve_dataset_dir(dataset)
    if dataset_dir is None:
        return {
            "error": f"数据集 {dataset_id} 缺少预处理向量路径",
            "matched_count": 0,
            "cell_ids": [],
        }

    def _do_filter() -> dict[str, Any]:
        artifacts = search_service.load_dataset_artifacts(dataset_dir)
        metadata = artifacts["metadata"]
        cell_ids: list[str] = artifacts["cell_ids"]
        if metadata is None or len(metadata) == 0:
            return {"matched_count": 0, "cell_ids": [], "filters": filters}
        import numpy as np  # noqa: PLC0415  延迟以避免模块顶层重复导入

        mask = np.ones(len(metadata), dtype=bool)
        for col, value in filters.items():
            if col not in metadata.columns:
                return {
                    "error": f"过滤字段不在 metadata: {col}",
                    "matched_count": 0,
                    "cell_ids": [],
                    "filters": filters,
                }
            mask &= (metadata[col] == value).to_numpy()
        indices = np.flatnonzero(mask)
        matched_count = int(indices.size)
        head = indices[:limit].tolist()
        sample_ids = [cell_ids[int(i)] for i in head]

        def _value_counts(col: str) -> dict[str, int]:
            if col not in metadata.columns:
                return {}
            sub = metadata.loc[indices, col].astype(str).value_counts()
            return {str(k): int(v) for k, v in sub.head(5).items()}

        return {
            "dataset_id": dataset_id,
            "filters": filters,
            "matched_count": matched_count,
            "cell_ids": sample_ids,
            "cell_type_counts": _value_counts("cell_type"),
            "tissue_counts": _value_counts("tissue"),
        }

    try:
        return await asyncio.to_thread(_do_filter)
    except Exception as exc:  # noqa: BLE001
        logger.warning("tool_filter_cells 失败: %s", exc)
        return {
            "error": f"过滤失败: {exc}",
            "matched_count": 0,
            "cell_ids": [],
            "filters": filters,
        }


def tool_summarize_results(hits: list[dict[str, Any]], query: str = "") -> dict[str, Any]:
    """对命中条目做模板化自然语言总结。

    Args:
        hits: 命中条目列表，每项至少含 ``cell_id`` 与 ``meta``/``metadata``。
        query: 用户原始问题，用于装饰总结句式；可为空。

    Returns:
        dict[str, Any]: ``{"summary": str, "n_hits": int}``。无命中时给出兜底文案。
    """
    from collections import Counter  # noqa: PLC0415  延迟以避免顶层重复 import

    if not hits:
        return {
            "summary": f"未能为查询「{query or '当前条件'}」找到匹配的细胞。",
            "n_hits": 0,
        }
    ct_counter: Counter[str] = Counter()
    ts_counter: Counter[str] = Counter()
    for h in hits:
        meta = h.get("meta") or h.get("metadata") or {}
        ct = meta.get("cell_type") if isinstance(meta, dict) else None
        if ct:
            ct_counter[str(ct)] += 1
        ts = meta.get("tissue") if isinstance(meta, dict) else None
        if ts:
            ts_counter[str(ts)] += 1
    parts = [f"共找到 {len(hits)} 个相关细胞"]
    if query:
        parts[0] = f"为查询「{query}」共找到 {len(hits)} 个相关细胞"
    if ct_counter:
        top = "、".join(f"{k} ({v})" for k, v in ct_counter.most_common(3))
        parts.append(f"主要细胞类型为 {top}")
    if ts_counter:
        top_t = "、".join(f"{k} ({v})" for k, v in ts_counter.most_common(2))
        parts.append(f"组织分布以 {top_t} 为主")
    first = hits[0].get("cell_id")
    if first:
        parts.append(f"排名第一的 cell_id 为 {first}")
    summary = "；".join(parts) + "。"
    return {"summary": summary, "n_hits": len(hits)}


async def execute_tool(
    name: str,
    arguments: dict[str, Any],
    session: AsyncSession,
    user: User,
) -> dict[str, Any]:
    """统一的工具分发执行器。

    Args:
        name: 工具名，必须在 :data:`TOOLS_SCHEMA` 中。
        arguments: LLM 给出的入参字典；缺失字段在工具内部按各自语义处理。
        session: 异步数据库会话。
        user: 当前请求用户。

    Returns:
        dict[str, Any]: 工具执行结果（JSON-serializable）。未知工具名返回
        ``{"error": "unknown tool: ..."}``，绝不抛异常。
    """
    args = dict(arguments or {})
    try:
        if name == "list_datasets":
            return await tool_list_datasets(session, user)
        if name == "search_by_cell_id":
            return await tool_search_by_cell_id(
                session,
                user,
                dataset_id=int(args.get("dataset_id", 0)),
                cell_id=str(args.get("cell_id", "")),
                top_k=int(args.get("top_k", 10) or 10),
            )
        if name == "search_by_vector":
            return await tool_search_by_vector(
                session,
                user,
                dataset_id=int(args.get("dataset_id", 0)),
                query_vector=list(args.get("query_vector", []) or []),
                top_k=int(args.get("top_k", 10) or 10),
            )
        if name == "filter_cells":
            return await tool_filter_cells(
                session,
                user,
                dataset_id=int(args.get("dataset_id", 0)),
                filters=dict(args.get("filters", {}) or {}),
                limit=int(args.get("limit", 20) or 20),
            )
        if name == "summarize_results":
            return tool_summarize_results(
                hits=list(args.get("hits", []) or []),
                query=str(args.get("query", "") or ""),
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("execute_tool 异常 name=%s args=%s", name, args)
        return {"error": f"工具执行异常: {exc}"}
    return {"error": f"unknown tool: {name}"}
