"""ANN 后端工厂。"""

from __future__ import annotations

from app.services.ann.base import IndexBackend
from app.services.ann.brute_backend import BruteBackend
from app.services.ann.faiss_backend import FaissHnswBackend, FaissIvfPqBackend
from app.services.ann.hnswlib_backend import HnswlibBackend

_BACKENDS: dict[str, type[IndexBackend]] = {
    "hnswlib": HnswlibBackend,
    "faiss-hnsw": FaissHnswBackend,
    "faiss-ivfpq": FaissIvfPqBackend,
    "brute": BruteBackend,
}


def create_backend(backend_name: str, dim: int, metric: str = "l2") -> IndexBackend:
    """根据后端名称创建 :class:`IndexBackend` 实例。

    Args:
        backend_name: 后端标识，取值 ``hnswlib | faiss-hnsw | faiss-ivfpq | brute``。
        dim: 向量维度。
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
