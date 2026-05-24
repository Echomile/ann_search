"""ANN 后端工厂。"""

from __future__ import annotations

from app.services.ann.adaptive_hnsw_backend import AdaptiveHnswBackend
from app.services.ann.base import IndexBackend
from app.services.ann.brute_backend import BruteBackend
from app.services.ann.faiss_backend import FaissHnswBackend, FaissIvfPqBackend
from app.services.ann.hnswlib_backend import HnswlibBackend
from app.services.ann.sparse_backend import SparseBruteBackend

_BACKENDS: dict[str, type[IndexBackend]] = {
    "hnswlib": HnswlibBackend,
    "faiss-hnsw": FaissHnswBackend,
    "faiss-ivfpq": FaissIvfPqBackend,
    "brute": BruteBackend,
    "adaptive-hnsw": AdaptiveHnswBackend,
    "sparse-brute": SparseBruteBackend,
}


def create_backend(backend_name: str, dim: int, metric: str = "l2") -> IndexBackend:
    """根据后端名称创建 :class:`IndexBackend` 实例。

    Args:
        backend_name: 后端标识，取值 ``hnswlib | faiss-hnsw | faiss-ivfpq |
            brute | adaptive-hnsw | sparse-brute``。
        dim: 向量维度。``sparse-brute`` 下对应 HVG 数量（如 5000）。
        metric: 距离度量，``l2 | cosine | ip``。

    Returns:
        IndexBackend: 对应后端实例。

    Raises:
        ValueError: ``backend_name`` 未注册。
    """
    if backend_name not in _BACKENDS:
        raise ValueError(f"未知 ANN 后端: {backend_name}; 可选: {list(_BACKENDS)}")
    cls = _BACKENDS[backend_name]
    return cls(dim=int(dim), metric=metric)


def list_backends() -> list[str]:
    """返回已注册的后端名称列表。"""
    return list(_BACKENDS.keys())
