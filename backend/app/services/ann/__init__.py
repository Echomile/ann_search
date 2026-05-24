"""ANN 后端实现集合：hnswlib、faiss、brute-force、自适应 HNSW、稀疏感知等。"""

from app.services.ann.adaptive_hnsw_backend import AdaptiveHnswBackend
from app.services.ann.base import IndexBackend
from app.services.ann.cache import IndexCache, get_index_cache
from app.services.ann.factory import create_backend, list_backends
from app.services.ann.sparse_backend import SparseBruteBackend

__all__ = [
    "AdaptiveHnswBackend",
    "IndexBackend",
    "IndexCache",
    "SparseBruteBackend",
    "create_backend",
    "get_index_cache",
    "list_backends",
]
