"""ANN 后端单元测试。

针对 :mod:`app.services.ann` 下 4 个具体后端，验证：
    1. ``build → search → save → load → search`` 流程的一致性；
    2. 与 :class:`~app.services.ann.brute_backend.BruteBackend` ground truth
       对比的 Recall@10 表现：图索引（hnswlib / faiss-hnsw）> 0.9，
       PQ 量化索引（faiss-ivfpq）> 0.5（允许的压缩损失）。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.services.ann.brute_backend import BruteBackend
from app.services.ann.factory import create_backend, list_backends
from app.services.ann.faiss_backend import FaissHnswBackend, FaissIvfPqBackend
from app.services.ann.hnswlib_backend import HnswlibBackend

N = 1000
DIM = 16
TOP_K = 10
SEED = 42


@pytest.fixture(scope="module")
def vectors() -> np.ndarray:
    """构造 ``(N, DIM)`` 的可复现随机基底向量。"""
    rng = np.random.default_rng(SEED)
    return rng.standard_normal(size=(N, DIM)).astype(np.float32)


@pytest.fixture(scope="module")
def queries(vectors: np.ndarray) -> np.ndarray:
    """构造 32 条查询向量。"""
    rng = np.random.default_rng(SEED + 1)
    return rng.standard_normal(size=(32, DIM)).astype(np.float32)


@pytest.fixture(scope="module")
def ground_truth(vectors: np.ndarray, queries: np.ndarray) -> np.ndarray:
    """暴力法的 Top-K 真值。"""
    brute = BruteBackend(dim=DIM, metric="l2")
    brute.build(vectors)
    idx, _ = brute.search(queries, top_k=TOP_K)
    return idx


def _recall(pred: np.ndarray, gt: np.ndarray) -> float:
    """计算逐查询 Recall@K 的平均值。"""
    assert pred.shape == gt.shape
    hits = [len(set(p.tolist()) & set(g.tolist())) for p, g in zip(pred, gt, strict=True)]
    return float(np.mean(hits) / pred.shape[1])


def test_factory_lists_backends() -> None:
    """factory 应注册全部 5 个后端（4 基础 + 1 改进版 adaptive-hnsw）。"""
    names = set(list_backends())
    assert names == {"hnswlib", "faiss-hnsw", "faiss-ivfpq", "brute", "adaptive-hnsw"}


def test_factory_unknown_backend_raises() -> None:
    """未知后端名称应抛 :class:`ValueError`。"""
    with pytest.raises(ValueError):
        create_backend("nope", dim=DIM)


def test_brute_consistency(vectors: np.ndarray, queries: np.ndarray, tmp_path: Path) -> None:
    """BruteBackend 自身做 save/load 一致性。"""
    brute = BruteBackend(dim=DIM, metric="l2")
    brute.build(vectors)
    idx1, dist1 = brute.search(queries, top_k=TOP_K)

    path = tmp_path / "brute.npy"
    brute.save(str(path))
    loaded = BruteBackend(dim=DIM, metric="l2")
    loaded.load(str(path))
    idx2, dist2 = loaded.search(queries, top_k=TOP_K)

    np.testing.assert_array_equal(idx1, idx2)
    np.testing.assert_allclose(dist1, dist2, rtol=1e-5)
    assert brute.memory_mb() > 0


def test_hnswlib_build_search_recall(
    vectors: np.ndarray, queries: np.ndarray, ground_truth: np.ndarray, tmp_path: Path
) -> None:
    """hnswlib 后端：构建、检索、save/load 与 Recall@10 > 0.9。"""
    backend = HnswlibBackend(dim=DIM, metric="l2")
    backend.build(vectors, M=16, ef_construction=200, ef_search=100)
    idx1, _ = backend.search(queries, top_k=TOP_K)
    assert idx1.shape == (queries.shape[0], TOP_K)

    path = tmp_path / "hnswlib.bin"
    backend.save(str(path))

    reloaded = HnswlibBackend(dim=DIM, metric="l2")
    reloaded.set_ef(100)
    reloaded.load(str(path))
    idx2, _ = reloaded.search(queries, top_k=TOP_K)
    np.testing.assert_array_equal(idx1, idx2)

    recall = _recall(idx1, ground_truth)
    assert recall > 0.9, f"hnswlib recall@10={recall:.3f}"
    assert backend.memory_mb() > 0


def test_faiss_hnsw_recall(
    vectors: np.ndarray, queries: np.ndarray, ground_truth: np.ndarray, tmp_path: Path
) -> None:
    """FAISS HNSW 后端：基本一致性 + Recall@10 > 0.9。"""
    backend = FaissHnswBackend(dim=DIM, metric="l2")
    backend.build(vectors, M=32, ef_construction=200, ef_search=128)
    idx1, _ = backend.search(queries, top_k=TOP_K)
    assert idx1.shape == (queries.shape[0], TOP_K)

    path = tmp_path / "faiss_hnsw.bin"
    backend.save(str(path))
    reloaded = FaissHnswBackend(dim=DIM, metric="l2")
    reloaded.load(str(path))
    idx2, _ = reloaded.search(queries, top_k=TOP_K)
    np.testing.assert_array_equal(idx1, idx2)

    recall = _recall(idx1, ground_truth)
    assert recall > 0.9, f"faiss-hnsw recall@10={recall:.3f}"


def test_faiss_ivfpq_basic(
    vectors: np.ndarray, queries: np.ndarray, ground_truth: np.ndarray, tmp_path: Path
) -> None:
    """FAISS IVF-PQ 后端：基本可用 + Recall@10 > 0.5。"""
    backend = FaissIvfPqBackend(dim=DIM, metric="l2")
    backend.build(vectors, nlist=16, m=8, nbits=8, nprobe=16)
    idx1, _ = backend.search(queries, top_k=TOP_K)
    assert idx1.shape == (queries.shape[0], TOP_K)

    path = tmp_path / "ivfpq.bin"
    backend.save(str(path))
    reloaded = FaissIvfPqBackend(dim=DIM, metric="l2")
    reloaded.load(str(path))
    idx2, _ = reloaded.search(queries, top_k=TOP_K)
    np.testing.assert_array_equal(idx1, idx2)

    recall = _recall(idx1, ground_truth)
    assert recall > 0.5, f"faiss-ivfpq recall@10={recall:.3f}"


def test_factory_dispatch(vectors: np.ndarray, queries: np.ndarray) -> None:
    """工厂函数应返回正确的 :attr:`IndexBackend.name`。"""
    for name in ["hnswlib", "faiss-hnsw", "brute"]:
        backend = create_backend(name, dim=DIM, metric="l2")
        assert backend.name == name
        if name == "hnswlib":
            backend.build(vectors, M=8, ef_construction=80, ef_search=40)
        else:
            backend.build(vectors)
        idx, _ = backend.search(queries[:4], top_k=5)
        assert idx.shape == (4, 5)


def test_brute_mmap_load_consistency(
    vectors: np.ndarray, queries: np.ndarray, tmp_path: Path
) -> None:
    """BruteBackend ``load`` 启用 mmap 后检索结果应与原内存索引完全一致。"""
    brute = BruteBackend(dim=DIM, metric="l2")
    brute.build(vectors)
    idx1, dist1 = brute.search(queries, top_k=TOP_K)

    path = tmp_path / "brute_mmap.npy"
    brute.save(str(path))
    reloaded = BruteBackend(dim=DIM, metric="l2")
    reloaded.load(str(path))
    assert isinstance(reloaded._vectors, np.memmap)
    idx2, dist2 = reloaded.search(queries, top_k=TOP_K)

    np.testing.assert_array_equal(idx1, idx2)
    np.testing.assert_allclose(dist1, dist2, rtol=1e-5)


def test_brute_numba_l2_smoke(vectors: np.ndarray, queries: np.ndarray) -> None:
    """P2 smoke：当 numba 可用时，``BruteBackend`` 的 l2 检索结果应与 numpy 路径一致。

    通过对比 ``use_numba=True`` 与 ``use_numba=False`` 的两个实例，验证：
        1. numba 路径被实际启用（``numba_active`` 为 ``True``）；
        2. 返回的 ``indices`` 完全一致（squared L2 严格有序）；
        3. 返回的 ``distances`` 与 numpy 路径在 ``rtol=1e-5`` 内吻合
           （fastmath 允许 IEEE 浮点重排序，可能产生极小误差）。
    若 numba 不可用，``numba_active`` 为 ``False``，等价于跑两次 numpy 路径，
    测试自动降级仍然通过。
    """
    from app.services.ann import brute_backend as _bb

    numba_be = BruteBackend(dim=DIM, metric="l2", use_numba=True)
    numba_be.build(vectors)
    if _bb._NUMBA:
        assert numba_be.numba_active is True
    idx_numba, dist_numba = numba_be.search(queries, top_k=TOP_K)

    numpy_be = BruteBackend(dim=DIM, metric="l2", use_numba=False)
    numpy_be.build(vectors)
    assert numpy_be.numba_active is False
    idx_numpy, dist_numpy = numpy_be.search(queries, top_k=TOP_K)

    np.testing.assert_array_equal(idx_numba, idx_numpy)
    np.testing.assert_allclose(dist_numba, dist_numpy, rtol=1e-5, atol=1e-5)


def test_brute_numba_top1_full_match(vectors: np.ndarray, queries: np.ndarray) -> None:
    """numba 加速路径下，top-1 应与暴力 argmin 完全一致（极端情况下兜底校验）。"""
    backend = BruteBackend(dim=DIM, metric="l2", use_numba=True)
    backend.build(vectors)
    idx, _ = backend.search(queries, top_k=1)

    diff = vectors[None, :, :] - queries[:, None, :]
    sq = (diff * diff).sum(axis=2)
    expected = np.argmin(sq, axis=1)
    np.testing.assert_array_equal(idx[:, 0], expected)


def test_hnswlib_mmap_load_consistency(
    vectors: np.ndarray, queries: np.ndarray, tmp_path: Path
) -> None:
    """HnswlibBackend 新版 ``load`` 走 ``allow_replace_deleted`` 分支应保持一致。"""
    backend = HnswlibBackend(dim=DIM, metric="l2")
    backend.build(vectors, M=16, ef_construction=200, ef_search=100)
    idx1, _ = backend.search(queries, top_k=TOP_K)

    path = tmp_path / "hnswlib_mmap.bin"
    backend.save(str(path))

    reloaded = HnswlibBackend(dim=DIM, metric="l2")
    reloaded.set_ef(100)
    reloaded.load(str(path))
    idx2, _ = reloaded.search(queries, top_k=TOP_K)
    np.testing.assert_array_equal(idx1, idx2)


def test_cosine_metric_consistency(vectors: np.ndarray, queries: np.ndarray) -> None:
    """cosine 度量下，hnswlib / faiss-hnsw 与 brute 的 Top-1 应高度一致。"""
    brute = BruteBackend(dim=DIM, metric="cosine")
    brute.build(vectors)
    gt_top1, _ = brute.search(queries, top_k=1)

    hnsw = HnswlibBackend(dim=DIM, metric="cosine")
    hnsw.build(vectors, M=16, ef_construction=200, ef_search=128)
    idx, _ = hnsw.search(queries, top_k=1)
    hit = float(np.mean(idx[:, 0] == gt_top1[:, 0]))
    assert hit > 0.9, f"hnswlib cosine top1 hit={hit:.3f}"

    faiss_hnsw = FaissHnswBackend(dim=DIM, metric="cosine")
    faiss_hnsw.build(vectors, M=32, ef_construction=200, ef_search=128)
    idx2, _ = faiss_hnsw.search(queries, top_k=1)
    hit2 = float(np.mean(idx2[:, 0] == gt_top1[:, 0]))
    assert hit2 > 0.9, f"faiss-hnsw cosine top1 hit={hit2:.3f}"
