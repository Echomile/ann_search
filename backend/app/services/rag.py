"""RAG 服务：将 ANN 检索结果与 LLM 推理串联。"""

from __future__ import annotations

from typing import Any


def rag_answer(question: str, dataset_id: int | None = None) -> dict[str, Any]:
    """对自然语言问题进行 RAG 回答。

    Args:
        question: 用户提问。
        dataset_id: 限定的数据集 ID，缺省时跨数据集检索。

    Returns:
        dict: 包含 ``answer``、``citations`` 等字段。
    """
    raise NotImplementedError
