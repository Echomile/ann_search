"""ANN 后端抽象基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import numpy as np


class IndexBackend(ABC):
    """ANN 索引后端抽象接口。

    所有具体后端（hnswlib、faiss、brute-force 等）需实现本接口，
    以便上层服务通过统一的 API 进行索引构建、检索、持久化与度量。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """返回后端名称标识，例如 ``hnswlib``、``faiss-hnsw``。"""
        raise NotImplementedError

    @abstractmethod
    def build(self, vectors: np.ndarray, **params: Any) -> None:
        """构建索引。

        Args:
            vectors: 形状为 ``(N, D)`` 的向量矩阵，``dtype=float32``。
            **params: 后端特定的构建参数，例如 ``M``、``ef_construction``。
        """
        raise NotImplementedError

    @abstractmethod
    def search(self, query: np.ndarray, top_k: int) -> tuple[np.ndarray, np.ndarray]:
        """检索 Top-K 近邻。

        Args:
            query: 形状为 ``(M, D)`` 或 ``(D,)`` 的查询向量。
            top_k: 返回近邻数量。

        Returns:
            tuple[np.ndarray, np.ndarray]: ``(indices, distances)``，
                ``indices`` 形状 ``(M, top_k)``，``distances`` 形状 ``(M, top_k)``。
        """
        raise NotImplementedError

    @abstractmethod
    def save(self, path: str) -> None:
        """将索引持久化到磁盘。

        Args:
            path: 索引文件落盘路径。
        """
        raise NotImplementedError

    @abstractmethod
    def load(self, path: str) -> None:
        """从磁盘加载索引。

        Args:
            path: 索引文件路径。
        """
        raise NotImplementedError

    @abstractmethod
    def memory_mb(self) -> float:
        """返回当前索引的内存占用（MB）估计值。"""
        raise NotImplementedError
