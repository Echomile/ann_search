"""检索服务核心逻辑测试。

通过暴力后端在一个 100×8 的合成数据集上验证：

- ``search_with_backend`` 在无过滤时返回 ``top_k`` 个最近邻，且距离单调递增；
- 以查询点自身向量发起检索时可正确排除自身；
- ``filters`` 能够按 metadata 字段缩窄候选集合；
- ``load_dataset_artifacts`` 能加载 ``vectors.npy`` + ``cell_ids.json`` + ``metadata.csv``。

F1 批量检索端到端测试：
    覆盖 ``POST /api/v1/search/batch`` 的成功路径、混合查询、空与超限边界。
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncGenerator

import numpy as np
import pandas as pd
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  确保所有模型注册到 metadata
from app.api.deps import get_db
from app.db.base import Base
from app.main import app
from app.models.dataset import Dataset
from app.models.index_record import IndexRecord
from app.models.user import User
from app.services import search as search_service
from app.services import search_cache
from app.services.ann.brute_backend import BruteBackend
from app.services.ann.cache import IndexCache

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


# ---------------------------------------------------------------------------
# F1 批量检索 API 端到端测试
# ---------------------------------------------------------------------------

_BATCH_DIM = 8
_BATCH_N = 40


@pytest_asyncio.fixture
async def batch_env(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[tuple[AsyncClient, dict[str, str], int, int], None]:
    """搭建批量检索端到端环境：内存 SQLite + brute 索引 + 注册登录。

    Yields:
        ``(client, headers, dataset_id, index_id)``：测试可直接用其发起 ``/search/batch`` 请求。
    """
    monkeypatch.setattr(search_cache, "_get_client", lambda: None)
    search_cache.reset_cache_metrics()
    IndexCache.instance().clear()
    search_service.clear_dataset_cache()

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    dataset_dir = tmp_path / "ds_batch"
    dataset_dir.mkdir()
    rng = np.random.default_rng(0)
    vectors = rng.normal(size=(_BATCH_N, _BATCH_DIM)).astype(np.float32)
    np.save(dataset_dir / "vectors.npy", vectors)
    cell_ids = [f"c{i:03d}" for i in range(_BATCH_N)]
    with open(dataset_dir / "cell_ids.json", "w", encoding="utf-8") as fp:
        json.dump(cell_ids, fp)
    pd.DataFrame({"cell_type": ["T"] * _BATCH_N}).to_csv(dataset_dir / "metadata.csv", index=False)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            reg = await ac.post(
                "/api/v1/auth/register",
                json={"username": "batch_user", "password": "batch_pw_123"},
            )
            assert reg.status_code == 201, reg.text
            login = await ac.post(
                "/api/v1/auth/login",
                data={"username": "batch_user", "password": "batch_pw_123"},
            )
            assert login.status_code == 200, login.text
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

            async with session_maker() as session:
                user = (
                    await session.execute(select(User).where(User.username == "batch_user"))
                ).scalar_one()
                dataset = Dataset(
                    owner_id=user.id,
                    name="batch_ds",
                    h5ad_path=str(tmp_path / "fake.h5ad"),
                    vectors_path=str(dataset_dir),
                    status="ready",
                    vector_dim=_BATCH_DIM,
                    cell_count=_BATCH_N,
                )
                session.add(dataset)
                await session.commit()
                await session.refresh(dataset)

                record = IndexRecord(
                    dataset_id=dataset.id,
                    backend="brute",
                    metric="l2",
                    params={},
                    index_path=None,
                    status="ready",
                )
                session.add(record)
                await session.commit()
                await session.refresh(record)
                dataset_id = int(dataset.id)
                record_id = int(record.id)

            yield ac, headers, dataset_id, record_id
    finally:
        app.dependency_overrides.pop(get_db, None)
        IndexCache.instance().clear()
        search_service.clear_dataset_cache()
        await engine.dispose()


async def test_batch_search_by_vector(
    batch_env: tuple[AsyncClient, dict[str, str], int, int],
) -> None:
    """3 个随机向量批量查询应返回 3 组 hits，每组 ``top_k=5`` 且距离非递减。"""
    ac, headers, dataset_id, index_id = batch_env
    rng = np.random.default_rng(7)
    queries = [
        {"vector": rng.normal(size=_BATCH_DIM).astype(np.float32).tolist()} for _ in range(3)
    ]
    resp = await ac.post(
        "/api/v1/search/batch",
        headers=headers,
        json={
            "dataset_id": dataset_id,
            "index_id": index_id,
            "queries": queries,
            "top_k": 5,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dataset_id"] == dataset_id
    assert body["top_k"] == 5
    assert body["total_queries"] == 3
    assert body["index_backend"] == "brute"
    assert body["metric"] == "l2"
    assert body["total_latency_ms"] >= 0.0
    assert len(body["groups"]) == 3
    for i, group in enumerate(body["groups"]):
        assert group["query_index"] == i
        assert group["query_cell_id"] is None
        assert len(group["hits"]) == 5
        distances = [hit["distance"] for hit in group["hits"]]
        assert distances == sorted(distances)
        assert isinstance(group["cache_hit"], bool)
        assert group["latency_ms"] >= 0.0


async def test_batch_search_mixed(
    batch_env: tuple[AsyncClient, dict[str, str], int, int],
) -> None:
    """混合 cell_id + vector 各 1 个：每组返回 ``top_k`` 命中且 ``query_cell_id`` 正确回填。"""
    ac, headers, dataset_id, index_id = batch_env
    rng = np.random.default_rng(3)
    queries = [
        {"cell_id": "c001"},
        {"vector": rng.normal(size=_BATCH_DIM).astype(np.float32).tolist()},
    ]
    resp = await ac.post(
        "/api/v1/search/batch",
        headers=headers,
        json={
            "dataset_id": dataset_id,
            "index_id": index_id,
            "queries": queries,
            "top_k": 4,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_queries"] == 2
    groups = body["groups"]
    assert groups[0]["query_cell_id"] == "c001"
    assert groups[1]["query_cell_id"] is None
    for group in groups:
        assert len(group["hits"]) == 4
    cell_id_hits = {hit["cell_id"] for hit in groups[0]["hits"]}
    assert "c001" not in cell_id_hits


async def test_batch_search_empty_returns_400(
    batch_env: tuple[AsyncClient, dict[str, str], int, int],
) -> None:
    """空 ``queries`` 应被 schema/端点拦截（422 或 400 均可）。"""
    ac, headers, dataset_id, index_id = batch_env
    resp = await ac.post(
        "/api/v1/search/batch",
        headers=headers,
        json={
            "dataset_id": dataset_id,
            "index_id": index_id,
            "queries": [],
            "top_k": 5,
        },
    )
    assert resp.status_code in {400, 422}, resp.text


async def test_batch_search_too_many_returns_400(
    batch_env: tuple[AsyncClient, dict[str, str], int, int],
) -> None:
    """超过上限的 51 条查询应返回 400 并提示数量上限。"""
    ac, headers, dataset_id, index_id = batch_env
    rng = np.random.default_rng(1)
    queries = [
        {"vector": rng.normal(size=_BATCH_DIM).astype(np.float32).tolist()} for _ in range(51)
    ]
    resp = await ac.post(
        "/api/v1/search/batch",
        headers=headers,
        json={
            "dataset_id": dataset_id,
            "index_id": index_id,
            "queries": queries,
            "top_k": 5,
        },
    )
    assert resp.status_code == 400, resp.text
    assert "queries" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# F6 SSE 流式 by-vector 检索 API 端到端测试
# ---------------------------------------------------------------------------


def _parse_sse_events(raw: str) -> list[dict[str, str]]:
    """将 ``\\n\\n`` / ``\\r\\n\\r\\n`` 分隔的 SSE 文本解析为 ``{event, data}`` 列表。

    简化实现：先把 CRLF 归一为 LF，再按空行切块；每块内仅识别 ``event:`` 与
    ``data:`` 字段，忽略 ``id:`` / ``retry:`` 与以 ``:`` 开头的注释行。同一个块
    内多行 ``data:`` 按 SSE 规范用 ``\\n`` 拼接。
    """
    normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
    events: list[dict[str, str]] = []
    blocks = [block for block in normalized.split("\n\n") if block.strip()]
    for block in blocks:
        event_name: str | None = None
        data_lines: list[str] = []
        for line in block.splitlines():
            if not line or line.startswith(":"):
                continue
            if line.startswith("event:"):
                event_name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].lstrip(" "))
        events.append({"event": event_name or "message", "data": "\n".join(data_lines)})
    return events


async def test_by_vector_stream(
    batch_env: tuple[AsyncClient, dict[str, str], int, int],
) -> None:
    """SSE 流式 by-vector：应推送 ``top_k`` 个 ``event: hit`` 并以 ``event: done`` 结束。"""
    ac, headers, dataset_id, index_id = batch_env
    rng = np.random.default_rng(11)
    top_k = 5
    payload = {
        "dataset_id": dataset_id,
        "index_id": index_id,
        "vector": rng.normal(size=_BATCH_DIM).astype(np.float32).tolist(),
        "top_k": top_k,
    }

    async with ac.stream(
        "POST",
        "/api/v1/search/by-vector-stream",
        headers=headers,
        json=payload,
    ) as resp:
        assert resp.status_code == 200, await resp.aread()
        ctype = resp.headers.get("content-type", "")
        assert "text/event-stream" in ctype, ctype
        chunks: list[bytes] = []
        async for chunk in resp.aiter_bytes():
            chunks.append(chunk)
    raw = b"".join(chunks).decode("utf-8", errors="replace")
    events = _parse_sse_events(raw)

    hit_events = [ev for ev in events if ev["event"] == "hit"]
    done_events = [ev for ev in events if ev["event"] == "done"]
    assert len(hit_events) == top_k, f"期望 {top_k} 条 hit，实际 {len(hit_events)} | raw={raw!r}"
    assert len(done_events) == 1, f"期望 1 条 done，实际 {len(done_events)} | raw={raw!r}"
    assert len(events) >= top_k + 1

    ranks: list[int] = []
    for ev in hit_events:
        item = json.loads(ev["data"])
        assert {"rank", "cell_id", "distance"}.issubset(item.keys())
        ranks.append(int(item["rank"]))
    assert ranks == sorted(ranks)
    assert ranks[0] == 1
    assert ranks[-1] == top_k

    summary = json.loads(done_events[0]["data"])
    assert summary["dataset_id"] == dataset_id
    assert summary["top_k"] == top_k
    assert summary["latency_ms"] >= 0.0
    assert summary["total_candidates"] >= top_k
    assert summary["index_backend"] == "brute"


async def test_by_vector_stream_dim_mismatch(
    batch_env: tuple[AsyncClient, dict[str, str], int, int],
) -> None:
    """SSE 流式接口在向量维度不匹配时应返回 422。"""
    ac, headers, dataset_id, index_id = batch_env
    bad_payload = {
        "dataset_id": dataset_id,
        "index_id": index_id,
        "vector": [0.1, 0.2, 0.3],
        "top_k": 5,
    }
    resp = await ac.post(
        "/api/v1/search/by-vector-stream",
        headers=headers,
        json=bad_payload,
    )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# F7 多后端 ensemble 检索端到端测试
# ---------------------------------------------------------------------------


def test_merge_ensemble_results_zscore_and_voting() -> None:
    """ensemble 合并：z-score 归一化 + voted_by 聚合 + 去重排序。

    构造 2 个索引的命中：``a1`` 同时被两个索引命中，``a2/a3`` 各只命中一次；
    合并后应按 z-score 最小值排序，且 ``a1`` 的 ``voted_by`` 长度为 2。
    """
    payload_a = {
        "results": [
            {"rank": 1, "cell_id": "a1", "distance": 0.1, "meta": {"x": 1}},
            {"rank": 2, "cell_id": "a2", "distance": 0.5, "meta": {}},
            {"rank": 3, "cell_id": "a3", "distance": 0.9, "meta": {}},
        ]
    }
    payload_b = {
        "results": [
            {"rank": 1, "cell_id": "a1", "distance": 10.0, "meta": {}},
            {"rank": 2, "cell_id": "a3", "distance": 12.0, "meta": {"y": 2}},
            {"rank": 3, "cell_id": "b9", "distance": 14.0, "meta": {}},
        ]
    }
    merged = search_service.merge_ensemble_results(
        per_index_results=[payload_a, payload_b],
        index_ids=[101, 202],
        top_k=4,
    )
    by_cid = {item["cell_id"]: item for item in merged}
    assert set(by_cid) == {"a1", "a2", "a3", "b9"}
    assert by_cid["a1"]["voted_by"] == [101, 202]
    assert by_cid["a2"]["voted_by"] == [101]
    assert by_cid["b9"]["voted_by"] == [202]
    scores = [item["score"] for item in merged]
    assert scores == sorted(scores)
    assert [item["rank"] for item in merged] == [1, 2, 3, 4]


@pytest_asyncio.fixture
async def ensemble_env(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[tuple[AsyncClient, dict[str, str], int, list[int], int], None]:
    """ensemble 端到端环境：1 个数据集 + 2 个 brute 索引 + 1 个跨数据集索引。

    Yields:
        ``(client, headers, dataset_id, [idx_a, idx_b], other_index_id)``。
        ``other_index_id`` 属于另一个数据集，用于跨数据集校验测试。
    """
    monkeypatch.setattr(search_cache, "_get_client", lambda: None)
    search_cache.reset_cache_metrics()
    IndexCache.instance().clear()
    search_service.clear_dataset_cache()

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    dim = 8
    n = 40
    rng = np.random.default_rng(0)

    ds_main = tmp_path / "ds_ensemble"
    ds_main.mkdir()
    vectors = rng.normal(size=(n, dim)).astype(np.float32)
    np.save(ds_main / "vectors.npy", vectors)
    cell_ids = [f"c{i:03d}" for i in range(n)]
    with open(ds_main / "cell_ids.json", "w", encoding="utf-8") as fp:
        json.dump(cell_ids, fp)
    pd.DataFrame({"cell_type": ["T"] * n}).to_csv(ds_main / "metadata.csv", index=False)

    ds_other = tmp_path / "ds_other"
    ds_other.mkdir()
    vectors_other = rng.normal(size=(n, dim)).astype(np.float32)
    np.save(ds_other / "vectors.npy", vectors_other)
    with open(ds_other / "cell_ids.json", "w", encoding="utf-8") as fp:
        json.dump([f"o{i:03d}" for i in range(n)], fp)
    pd.DataFrame({"cell_type": ["B"] * n}).to_csv(ds_other / "metadata.csv", index=False)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            reg = await ac.post(
                "/api/v1/auth/register",
                json={"username": "ens_user", "password": "ens_pw_123"},
            )
            assert reg.status_code == 201, reg.text
            login = await ac.post(
                "/api/v1/auth/login",
                data={"username": "ens_user", "password": "ens_pw_123"},
            )
            assert login.status_code == 200, login.text
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

            async with session_maker() as session:
                user = (
                    await session.execute(select(User).where(User.username == "ens_user"))
                ).scalar_one()
                main_ds = Dataset(
                    owner_id=user.id,
                    name="ensemble_ds",
                    h5ad_path=str(tmp_path / "main.h5ad"),
                    vectors_path=str(ds_main),
                    status="ready",
                    vector_dim=dim,
                    cell_count=n,
                )
                other_ds = Dataset(
                    owner_id=user.id,
                    name="other_ds",
                    h5ad_path=str(tmp_path / "other.h5ad"),
                    vectors_path=str(ds_other),
                    status="ready",
                    vector_dim=dim,
                    cell_count=n,
                )
                session.add_all([main_ds, other_ds])
                await session.commit()
                await session.refresh(main_ds)
                await session.refresh(other_ds)

                rec_a = IndexRecord(
                    dataset_id=main_ds.id,
                    backend="brute",
                    metric="l2",
                    params={},
                    index_path=None,
                    status="ready",
                )
                rec_b = IndexRecord(
                    dataset_id=main_ds.id,
                    backend="brute",
                    metric="cosine",
                    params={},
                    index_path=None,
                    status="ready",
                )
                rec_other = IndexRecord(
                    dataset_id=other_ds.id,
                    backend="brute",
                    metric="l2",
                    params={},
                    index_path=None,
                    status="ready",
                )
                session.add_all([rec_a, rec_b, rec_other])
                await session.commit()
                await session.refresh(rec_a)
                await session.refresh(rec_b)
                await session.refresh(rec_other)
                main_ds_id = int(main_ds.id)
                index_ids = [int(rec_a.id), int(rec_b.id)]
                other_index_id = int(rec_other.id)

            yield ac, headers, main_ds_id, index_ids, other_index_id
    finally:
        app.dependency_overrides.pop(get_db, None)
        IndexCache.instance().clear()
        search_service.clear_dataset_cache()
        await engine.dispose()


async def test_ensemble_min_2_indexes(
    ensemble_env: tuple[AsyncClient, dict[str, str], int, list[int], int],
) -> None:
    """只传 1 个 index 应返回 400 并提示数量区间。"""
    ac, headers, dataset_id, index_ids, _ = ensemble_env
    resp = await ac.post(
        "/api/v1/search/ensemble",
        headers=headers,
        json={
            "dataset_id": dataset_id,
            "index_ids": [index_ids[0]],
            "query": {"cell_id": "c000"},
            "top_k": 5,
        },
    )
    assert resp.status_code == 400, resp.text
    assert "index_ids" in resp.json()["detail"]


async def test_ensemble_merge_dedup(
    ensemble_env: tuple[AsyncClient, dict[str, str], int, list[int], int],
) -> None:
    """2 个 brute 索引（l2 + cosine）合并：hits 去重 + voted_by 长度合理。"""
    ac, headers, dataset_id, index_ids, _ = ensemble_env
    resp = await ac.post(
        "/api/v1/search/ensemble",
        headers=headers,
        json={
            "dataset_id": dataset_id,
            "index_ids": index_ids,
            "query": {"cell_id": "c000"},
            "top_k": 6,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dataset_id"] == dataset_id
    assert body["top_k"] == 6
    assert body["latency_ms"] >= 0.0
    assert set(body["per_index_latency_ms"].keys()) == {str(i) for i in index_ids}

    hits = body["hits"]
    assert 1 <= len(hits) <= 6
    cell_ids_seen = [hit["cell_id"] for hit in hits]
    assert len(cell_ids_seen) == len(set(cell_ids_seen))
    assert "c000" not in cell_ids_seen
    scores = [hit["score"] for hit in hits]
    assert scores == sorted(scores)
    ranks = [hit["rank"] for hit in hits]
    assert ranks == list(range(1, len(hits) + 1))

    for hit in hits:
        voted = hit["voted_by"]
        assert isinstance(voted, list)
        assert 1 <= len(voted) <= len(index_ids)
        assert set(voted).issubset(set(index_ids))
        assert sorted(voted) == voted
    assert any(len(hit["voted_by"]) == len(index_ids) for hit in hits)


async def test_ensemble_different_dataset_rejected(
    ensemble_env: tuple[AsyncClient, dict[str, str], int, list[int], int],
) -> None:
    """index_ids 包含跨数据集索引时应返回 400。"""
    ac, headers, dataset_id, index_ids, other_index_id = ensemble_env
    resp = await ac.post(
        "/api/v1/search/ensemble",
        headers=headers,
        json={
            "dataset_id": dataset_id,
            "index_ids": [index_ids[0], other_index_id],
            "query": {"cell_id": "c000"},
            "top_k": 5,
        },
    )
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert str(other_index_id) in detail
    assert "数据集" in detail


# ---------------------------------------------------------------------------
# D1 交互式参数仪表盘端到端测试：POST /search/with_params
# ---------------------------------------------------------------------------

from contextlib import asynccontextmanager  # noqa: E402

from app.services.ann.factory import create_backend  # noqa: E402


@asynccontextmanager
async def _make_param_env(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    backend_name: str,
    dim: int,
    n: int,
    metric: str = "l2",
    build_params: dict | None = None,
    seed: int = 0,
):
    """构建带指定 backend 的 ``/with_params`` 测试环境。

    Yields:
        ``(client, headers, dataset_id, index_id, backend)``。
        ``backend`` 直接暴露给测试用于 ``IndexCache.peek`` 后的状态断言。
    """
    monkeypatch.setattr(search_cache, "_get_client", lambda: None)
    search_cache.reset_cache_metrics()
    IndexCache.instance().clear()
    search_service.clear_dataset_cache()

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    rng = np.random.default_rng(seed)
    vectors = rng.normal(size=(n, dim)).astype(np.float32)
    dataset_dir = tmp_path / f"ds_param_{backend_name}_{seed}"
    dataset_dir.mkdir()
    np.save(dataset_dir / "vectors.npy", vectors)
    cell_ids = [f"c{i:04d}" for i in range(n)]
    with open(dataset_dir / "cell_ids.json", "w", encoding="utf-8") as fp:
        json.dump(cell_ids, fp)
    pd.DataFrame({"cell_type": ["T"] * n}).to_csv(dataset_dir / "metadata.csv", index=False)

    backend = create_backend(backend_name, dim=dim, metric=metric)
    backend.build(vectors, **(build_params or {}))

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            reg = await ac.post(
                "/api/v1/auth/register",
                json={"username": f"param_{backend_name}_{seed}", "password": "param_pw_123"},
            )
            assert reg.status_code == 201, reg.text
            login = await ac.post(
                "/api/v1/auth/login",
                data={"username": f"param_{backend_name}_{seed}", "password": "param_pw_123"},
            )
            assert login.status_code == 200, login.text
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

            async with session_maker() as session:
                user = (
                    await session.execute(
                        select(User).where(User.username == f"param_{backend_name}_{seed}")
                    )
                ).scalar_one()
                dataset = Dataset(
                    owner_id=user.id,
                    name=f"param_ds_{backend_name}",
                    h5ad_path=str(tmp_path / f"fake_{backend_name}.h5ad"),
                    vectors_path=str(dataset_dir),
                    status="ready",
                    vector_dim=dim,
                    cell_count=n,
                )
                session.add(dataset)
                await session.commit()
                await session.refresh(dataset)

                record = IndexRecord(
                    dataset_id=dataset.id,
                    backend=backend_name,
                    metric=metric,
                    params=build_params or {},
                    index_path=None,
                    status="ready",
                )
                session.add(record)
                await session.commit()
                await session.refresh(record)
                dataset_id = int(dataset.id)
                record_id = int(record.id)

            # 把已构建的后端预热进 IndexCache，省去 path 加载流程
            IndexCache.instance()._cache[record_id] = backend

            yield ac, headers, dataset_id, record_id, backend
    finally:
        app.dependency_overrides.pop(get_db, None)
        IndexCache.instance().clear()
        search_service.clear_dataset_cache()
        await engine.dispose()


async def test_search_with_params_hnswlib_ef_search(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """hnswlib：不同 ``ef_search`` 都能成功返回 Top-K，且 ``effective_params`` 正确回填。

    采用对比断言：小 ef_search 与大 ef_search 同步得到 top_k 个 hits，
    距离非递减；``effective_params`` 与请求一致，``ignored_params`` 为空；
    至少其中一种 ef 设置会改变 Top-K 命中顺序或距离（数据足够大时极大概率成立）。
    """
    async with _make_param_env(
        tmp_path,
        monkeypatch,
        backend_name="hnswlib",
        dim=32,
        n=600,
        metric="l2",
        build_params={"M": 8, "ef_construction": 50, "ef_search": 16},
        seed=7,
    ) as (ac, headers, dataset_id, index_id, _backend):
        rng = np.random.default_rng(123)
        query_vec = rng.normal(size=32).astype(np.float32).tolist()

        results: dict[int, list[dict]] = {}
        for ef in (8, 256):
            resp = await ac.post(
                "/api/v1/search/with_params",
                headers=headers,
                json={
                    "dataset_id": dataset_id,
                    "index_id": index_id,
                    "vector": query_vec,
                    "top_k": 10,
                    "runtime_params": {"ef_search": ef},
                },
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["dataset_id"] == dataset_id
            assert body["top_k"] == 10
            assert len(body["hits"]) == 10
            assert body["index_backend"] == "hnswlib"
            assert body["effective_params"] == {"ef_search": ef}
            assert body["ignored_params"] == []
            distances = [hit["distance"] for hit in body["hits"]]
            assert distances == sorted(distances)
            results[ef] = body["hits"]

        # 较大的 ef_search 在召回上应不劣于较小的：top-k 距离和单调非增（容差 1e-4）
        dist_low = sum(h["distance"] for h in results[8])
        dist_high = sum(h["distance"] for h in results[256])
        assert dist_high <= dist_low + 1e-4, (
            f"ef_search=256 总距离 {dist_high} 应 <= ef_search=8 总距离 {dist_low}"
        )


async def test_search_with_params_faiss_ivfpq_nprobe(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """faiss-ivfpq：``nprobe`` 越大召回越高，``Top-K`` 距离和应单调非增。"""
    async with _make_param_env(
        tmp_path,
        monkeypatch,
        backend_name="faiss-ivfpq",
        dim=32,
        n=600,
        metric="l2",
        build_params={"nlist": 32, "m": 8, "nbits": 8, "nprobe": 1},
        seed=11,
    ) as (ac, headers, dataset_id, index_id, _backend):
        rng = np.random.default_rng(321)
        query_vec = rng.normal(size=32).astype(np.float32).tolist()

        dist_sums: dict[int, float] = {}
        for nprobe in (1, 16):
            resp = await ac.post(
                "/api/v1/search/with_params",
                headers=headers,
                json={
                    "dataset_id": dataset_id,
                    "index_id": index_id,
                    "vector": query_vec,
                    "top_k": 10,
                    "runtime_params": {"nprobe": nprobe},
                },
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["index_backend"] == "faiss-ivfpq"
            assert body["effective_params"] == {"nprobe": nprobe}
            assert body["ignored_params"] == []
            assert len(body["hits"]) == 10
            dist_sums[nprobe] = sum(hit["distance"] for hit in body["hits"])

        assert dist_sums[16] <= dist_sums[1] + 1e-4, (
            f"nprobe=16 总距离 {dist_sums[16]} 应 <= nprobe=1 总距离 {dist_sums[1]}"
        )


async def test_search_with_params_brute_ignores_params(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """brute 后端不支持任何 runtime 参数，传入的 key 应全进入 ``ignored_params``。"""
    async with _make_param_env(
        tmp_path,
        monkeypatch,
        backend_name="brute",
        dim=8,
        n=100,
        metric="l2",
        seed=0,
    ) as (ac, headers, dataset_id, index_id, _backend):
        resp = await ac.post(
            "/api/v1/search/with_params",
            headers=headers,
            json={
                "dataset_id": dataset_id,
                "index_id": index_id,
                "cell_id": "c0001",
                "top_k": 5,
                "runtime_params": {"ef_search": 200, "nprobe": 32, "unknown_key": 1},
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["index_backend"] == "brute"
        assert body["effective_params"] == {}
        assert sorted(body["ignored_params"]) == sorted(["ef_search", "nprobe", "unknown_key"])
        assert len(body["hits"]) == 5
        assert all(hit["cell_id"] != "c0001" for hit in body["hits"])


async def test_search_with_params_restores_state(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """调用 ``/with_params`` 后再普通 ``/by-vector``，缓存 backend 的 ``ef_search`` 应恢复到原值。"""
    async with _make_param_env(
        tmp_path,
        monkeypatch,
        backend_name="hnswlib",
        dim=16,
        n=200,
        metric="l2",
        build_params={"M": 8, "ef_construction": 50, "ef_search": 32},
        seed=3,
    ) as (ac, headers, dataset_id, index_id, backend):
        original_ef = int(backend._ef_search)
        assert original_ef == 32

        rng = np.random.default_rng(5)
        query_vec = rng.normal(size=16).astype(np.float32).tolist()

        # 1) 调一次 with_params 把 ef_search 临时改成 256
        resp = await ac.post(
            "/api/v1/search/with_params",
            headers=headers,
            json={
                "dataset_id": dataset_id,
                "index_id": index_id,
                "vector": query_vec,
                "top_k": 5,
                "runtime_params": {"ef_search": 256},
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["effective_params"] == {"ef_search": 256}

        # 2) 缓存中的 backend 应已被恢复
        cached = IndexCache.instance().peek(index_id)
        assert cached is backend
        assert int(backend._ef_search) == original_ef, (
            f"with_params 结束后 ef_search 未恢复: 当前 {backend._ef_search}, 原值 {original_ef}"
        )

        # 3) 再发普通 by-vector，验证 backend 状态仍正常工作且仍是原 ef
        resp2 = await ac.post(
            "/api/v1/search/by-vector",
            headers=headers,
            json={
                "dataset_id": dataset_id,
                "index_id": index_id,
                "vector": query_vec,
                "top_k": 5,
            },
        )
        assert resp2.status_code == 200, resp2.text
        assert len(resp2.json()["hits"]) == 5
        assert int(backend._ef_search) == original_ef
