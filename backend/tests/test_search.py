"""检索服务核心逻辑测试。

通过暴力后端在一个 100×8 的合成数据集上验证：

- ``search_with_backend`` 在无过滤时返回 ``top_k`` 个最近邻，且距离单调递增；
- 以查询点自身向量发起检索时可正确排除自身；
- ``filters`` 能够按 metadata 字段缩窄候选集合；
- ``load_dataset_artifacts`` 能加载 ``vectors.npy`` + ``cell_ids.json`` + ``metadata.csv``。
"""

from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd
import pytest

from app.services import search as search_service
from app.services.ann.brute_backend import BruteBackend

DIM = 8
N = 100


@pytest.fixture
def brute_index() -> tuple[BruteBackend, np.ndarray, list[str], pd.DataFrame]:
    """构建用于检索测试的 brute 后端与配套元信息。"""
    rng = np.random.default_rng(0)
    vectors = rng.normal(size=(N, DIM)).astype(np.float32)
    backend = BruteBackend(dim=DIM, metric="l2")
    backend.build(vectors)
    cell_ids = [f"cell_{i:03d}" for i in range(N)]
    metadata = pd.DataFrame(
        {
            "cell_type": ["T" if i % 2 == 0 else "B" for i in range(N)],
            "donor": [f"d{i % 5}" for i in range(N)],
        }
    )
    return backend, vectors, cell_ids, metadata


def test_search_with_backend_returns_top_k_in_order(brute_index) -> None:
    """无过滤情况下应返回 ``top_k`` 个结果，距离非递减。"""
    backend, vectors, cell_ids, metadata = brute_index
    result = search_service.search_with_backend(
        backend=backend,
        cell_ids=cell_ids,
        metadata=metadata,
        query_vector=vectors[0],
        top_k=5,
    )
    assert len(result["results"]) == 5
    distances = [hit["distance"] for hit in result["results"]]
    assert distances == sorted(distances)
    assert all(d >= 0 for d in distances)
    assert result["index_backend"] == "brute"
    assert result["query_time_ms"] >= 0.0


def test_search_with_backend_excludes_query_self(brute_index) -> None:
    """以自身向量发起检索时应排除查询点自身。"""
    backend, vectors, cell_ids, metadata = brute_index
    result = search_service.search_with_backend(
        backend=backend,
        cell_ids=cell_ids,
        metadata=metadata,
        query_vector=vectors[0],
        top_k=5,
        exclude_indices={0},
    )
    returned_ids = {hit["cell_id"] for hit in result["results"]}
    assert "cell_000" not in returned_ids
    assert len(result["results"]) == 5


def test_search_with_backend_applies_filters(brute_index) -> None:
    """``filters`` 应该限制返回结果在指定 metadata 子集合中。"""
    backend, vectors, cell_ids, metadata = brute_index
    result = search_service.search_with_backend(
        backend=backend,
        cell_ids=cell_ids,
        metadata=metadata,
        query_vector=vectors[0],
        top_k=5,
        filters={"cell_type": "B"},
        over_fetch_factor=20,
    )
    assert len(result["results"]) > 0
    for hit in result["results"]:
        assert hit["meta"]["cell_type"] == "B"


def test_search_with_backend_filter_list(brute_index) -> None:
    """list 形式的过滤值应当走 isin 路径。"""
    backend, vectors, cell_ids, metadata = brute_index
    result = search_service.search_with_backend(
        backend=backend,
        cell_ids=cell_ids,
        metadata=metadata,
        query_vector=vectors[0],
        top_k=5,
        filters={"donor": ["d0", "d1"]},
        over_fetch_factor=20,
    )
    assert all(hit["meta"]["donor"] in {"d0", "d1"} for hit in result["results"])


def test_load_dataset_artifacts(tmp_path) -> None:
    """``load_dataset_artifacts`` 应能正确读取制品并构建 ``cell_id_to_index``。"""
    rng = np.random.default_rng(7)
    vectors = rng.normal(size=(20, DIM)).astype(np.float32)
    cell_ids = [f"c{i}" for i in range(20)]
    metadata = pd.DataFrame({"cell_type": ["T"] * 20})

    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    np.save(dataset_dir / "vectors.npy", vectors)
    with open(dataset_dir / "cell_ids.json", "w", encoding="utf-8") as fp:
        json.dump(cell_ids, fp)
    metadata.to_csv(dataset_dir / "metadata.csv", index=False)

    search_service.clear_dataset_cache()
    artifacts = search_service.load_dataset_artifacts(str(dataset_dir))
    assert artifacts["vectors"].shape == (20, DIM)
    assert artifacts["cell_ids"] == cell_ids
    assert artifacts["cell_id_to_index"]["c0"] == 0
    assert artifacts["metadata"].shape[0] == 20


def test_search_by_vector_uses_artifacts(tmp_path) -> None:
    """``search_by_vector`` 顶层函数应能配合 brute 后端工作。"""
    rng = np.random.default_rng(2)
    vectors = rng.normal(size=(30, DIM)).astype(np.float32)
    cell_ids = [f"x{i:02d}" for i in range(30)]

    dataset_dir = tmp_path / "ds"
    dataset_dir.mkdir()
    np.save(dataset_dir / "vectors.npy", vectors)
    with open(dataset_dir / "cell_ids.json", "w", encoding="utf-8") as fp:
        json.dump(cell_ids, fp)

    backend = BruteBackend(dim=DIM, metric="l2")
    backend.build(vectors)

    search_service.clear_dataset_cache()
    out = search_service.search_by_vector(
        query_vector=vectors[3],
        dataset_dir=str(dataset_dir),
        backend=backend,
        top_k=4,
        exclude_cell_id="x03",
    )
    assert len(out["results"]) == 4
    assert all(hit["cell_id"] != "x03" for hit in out["results"])
    assert os.path.isdir(str(dataset_dir))


def test_merge_multi_dataset_results() -> None:
    """多数据集合并按归一化距离升序排列并填充 source_dataset_id。"""
    payload_a = {
        "results": [
            {"rank": 1, "cell_id": "a1", "distance": 0.1, "meta": {}},
            {"rank": 2, "cell_id": "a2", "distance": 0.5, "meta": {}},
        ]
    }
    payload_b = {
        "results": [
            {"rank": 1, "cell_id": "b1", "distance": 10.0, "meta": {}},
            {"rank": 2, "cell_id": "b2", "distance": 12.0, "meta": {}},
        ]
    }
    merged = search_service.merge_multi_dataset_results(
        per_dataset_results=[payload_a, payload_b],
        dataset_ids=[1, 2],
        top_k=3,
    )
    assert len(merged) == 3
    assert {m["source_dataset_id"] for m in merged} == {1, 2}
    norms = [m["normalized_distance"] for m in merged]
    assert norms == sorted(norms)
