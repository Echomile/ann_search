"""索引评测核心逻辑测试。"""

from __future__ import annotations

import numpy as np
import pytest

from app.services.ann.brute_backend import BruteBackend
from app.services.evaluation import benchmark_index, compute_recall


def test_compute_recall_full_match() -> None:
    """approx == ground truth 时 Recall 应为 1.0。"""
    truth = np.array([[0, 1, 2, 3, 4], [5, 6, 7, 8, 9]])
    approx = truth.copy()
    assert compute_recall(approx, truth, k=5) == pytest.approx(1.0)


def test_compute_recall_zero_overlap() -> None:
    """完全无交集时 Recall 应为 0.0。"""
    truth = np.array([[0, 1, 2, 3, 4]])
    approx = np.array([[10, 11, 12, 13, 14]])
    assert compute_recall(approx, truth, k=5) == pytest.approx(0.0)


def test_compute_recall_partial_overlap_handcrafted() -> None:
    """手工构造交集大小，验证 Recall = 平均交集 / k。"""
    truth = np.array(
        [
            [0, 1, 2, 3, 4],
            [10, 11, 12, 13, 14],
        ]
    )
    approx = np.array(
        [
            [0, 1, 2, 99, 100],  # 交集 3
            [10, 11, 50, 51, 52],  # 交集 2
        ]
    )
    expected = (3 + 2) / (2 * 5)
    assert compute_recall(approx, truth, k=5) == pytest.approx(expected)


def test_compute_recall_truncates_k_when_arrays_shorter() -> None:
    """当 ``k`` 超过实际列数时应按可用宽度截断。"""
    truth = np.array([[0, 1, 2]])
    approx = np.array([[0, 1, 2]])
    assert compute_recall(approx, truth, k=10) == pytest.approx(1.0)


def test_compute_recall_handles_unordered_neighbors() -> None:
    """顺序不同但元素相同应判为完全匹配。"""
    truth = np.array([[0, 1, 2, 3, 4]])
    approx = np.array([[4, 3, 2, 1, 0]])
    assert compute_recall(approx, truth, k=5) == pytest.approx(1.0)


def test_benchmark_index_runs_end_to_end() -> None:
    """对 brute 后端自身评测时 Recall 必为 1.0，且各档位统计字段完整。"""
    rng = np.random.default_rng(11)
    vectors = rng.normal(size=(64, 6)).astype(np.float32)
    backend = BruteBackend(dim=6, metric="l2")
    backend.build(vectors)

    result = benchmark_index(
        backend=backend,
        vectors=vectors,
        index_id=42,
        dataset_id=7,
        metric="l2",
        num_queries=8,
        top_k_list=[5, 10],
        concurrency_list=[1, 2],
    )

    assert result["index_id"] == 42
    assert result["dataset_id"] == 7
    assert result["backend"] == "brute"
    assert set(result["recalls"].keys()) == {"5", "10"}
    for v in result["recalls"].values():
        assert v == pytest.approx(1.0)
    concurrencies = {entry["concurrency"] for entry in result["latencies"]}
    assert concurrencies == {1, 2}
    for entry in result["latencies"]:
        assert entry["p50_ms"] >= 0
        assert entry["p95_ms"] >= entry["p50_ms"]
        assert entry["p99_ms"] >= entry["p95_ms"]
        assert entry["qps"] >= 0.0
        assert entry["total_queries"] == 8
