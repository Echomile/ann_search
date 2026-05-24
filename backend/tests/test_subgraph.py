"""``GET /api/v1/indexes/{id}/subgraph`` 端到端测试（v1.2 D2 扩展功能）。

验证目标：
    - **test_subgraph_hnswlib**: hnswlib 后端可拉到 entry/邻居/边的合理子图，
      entry 在 ``depth=0`` 且 ``is_entry=True``，第 1/2 圈节点 ``depth`` 单调；
    - **test_subgraph_unsupported_backend**: brute 后端返回 400；
    - **test_subgraph_cell_not_found**: 不存在的 cell_id 返回 404。
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator

import numpy as np
import pandas as pd
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  确保模型注册到 metadata
from app.api.deps import get_db
from app.db.base import Base
from app.main import app
from app.models.dataset import Dataset
from app.models.index_record import IndexRecord
from app.models.user import User
from app.services import search as search_service
from app.services import search_cache
from app.services.ann.cache import IndexCache
from app.services.ann.factory import create_backend

_DIM = 8
_N = 60


@pytest_asyncio.fixture
async def subgraph_env(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[tuple[AsyncClient, dict[str, str], int, int, int, list[str]], None]:
    """搭建 D2 子图端到端环境。

    构造一个 ``N=60, dim=8`` 的合成数据集，落盘 ``vectors.npy`` + ``cell_ids.json``
    + ``metadata.csv``，**真实构建** hnswlib 索引并保存为索引文件，同时新建一条
    ``brute`` 索引用于覆盖 unsupported-backend 路径。

    Yields:
        ``(client, headers, dataset_id, hnswlib_index_id, brute_index_id, cell_ids)``
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

    dataset_dir = tmp_path / "ds_sub"
    dataset_dir.mkdir()
    rng = np.random.default_rng(0)
    vectors = rng.normal(size=(_N, _DIM)).astype(np.float32)
    np.save(dataset_dir / "vectors.npy", vectors)
    cell_ids = [f"c{i:03d}" for i in range(_N)]
    with open(dataset_dir / "cell_ids.json", "w", encoding="utf-8") as fp:
        json.dump(cell_ids, fp)
    pd.DataFrame({"cell_type": ["T" if i % 2 == 0 else "B" for i in range(_N)]}).to_csv(
        dataset_dir / "metadata.csv", index=False
    )

    # 构建真正的 hnswlib 索引并落盘
    hnsw_backend = create_backend("hnswlib", dim=_DIM, metric="l2")
    hnsw_backend.build(vectors, M=8, ef_construction=64, ef_search=64)
    hnsw_path = tmp_path / "hnswlib.bin"
    hnsw_backend.save(str(hnsw_path))

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            reg = await ac.post(
                "/api/v1/auth/register",
                json={"username": "subgraph_user", "password": "sub_pw_123"},
            )
            assert reg.status_code == 201, reg.text
            login = await ac.post(
                "/api/v1/auth/login",
                data={"username": "subgraph_user", "password": "sub_pw_123"},
            )
            assert login.status_code == 200, login.text
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

            async with session_maker() as session:
                user = (
                    await session.execute(select(User).where(User.username == "subgraph_user"))
                ).scalar_one()
                dataset = Dataset(
                    owner_id=user.id,
                    name="ds_sub",
                    h5ad_path=str(tmp_path / "fake.h5ad"),
                    vectors_path=str(dataset_dir),
                    status="ready",
                    vector_dim=_DIM,
                    cell_count=_N,
                )
                session.add(dataset)
                await session.commit()
                await session.refresh(dataset)

                hnsw_record = IndexRecord(
                    dataset_id=dataset.id,
                    backend="hnswlib",
                    metric="l2",
                    params={"M": 8, "ef_construction": 64, "ef_search": 64},
                    index_path=str(hnsw_path),
                    status="ready",
                )
                brute_record = IndexRecord(
                    dataset_id=dataset.id,
                    backend="brute",
                    metric="l2",
                    params={},
                    index_path=None,
                    status="ready",
                )
                session.add_all([hnsw_record, brute_record])
                await session.commit()
                await session.refresh(hnsw_record)
                await session.refresh(brute_record)
                dataset_id = int(dataset.id)
                hnsw_id = int(hnsw_record.id)
                brute_id = int(brute_record.id)

            yield ac, headers, dataset_id, hnsw_id, brute_id, cell_ids
    finally:
        app.dependency_overrides.pop(get_db, None)
        IndexCache.instance().clear()
        search_service.clear_dataset_cache()
        await engine.dispose()


async def test_subgraph_hnswlib(
    subgraph_env: tuple[AsyncClient, dict[str, str], int, int, int, list[str]],
) -> None:
    """hnswlib 后端可返回包含 entry + 邻居 + 边的合理子图。"""
    ac, headers, _ds, hnsw_id, _brute_id, cell_ids = subgraph_env
    entry_cell = cell_ids[7]
    resp = await ac.get(
        f"/api/v1/indexes/{hnsw_id}/subgraph",
        params={"cell_id": entry_cell, "depth": 2, "layer": 0, "max_nodes": 200},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["backend"] == "hnswlib"
    assert body["entry_cell_id"] == entry_cell
    assert body["depth"] == 2
    assert body["layer"] == 0

    nodes = body["nodes"]
    edges = body["edges"]
    assert len(nodes) >= 2, "至少应有 entry + 1 个邻居"
    assert len(edges) >= 1, "至少应有 1 条边"

    entry_nodes = [n for n in nodes if n["is_entry"]]
    assert len(entry_nodes) == 1, "entry 节点必须唯一"
    assert entry_nodes[0]["cell_id"] == entry_cell
    assert entry_nodes[0]["depth"] == 0
    assert entry_nodes[0]["label"] == body["entry_label"]

    depths = {n["depth"] for n in nodes}
    assert 0 in depths and depths.issubset({0, 1, 2})

    cell_types = {n["cell_type"] for n in nodes}
    assert cell_types <= {"T", "B"}, f"cell_type 仅应是 metadata 中的取值: {cell_types}"

    # 边端点必须全部在 nodes 集合内
    label_set = {n["label"] for n in nodes}
    for e in edges:
        assert e["src"] in label_set and e["dst"] in label_set


async def test_subgraph_unsupported_backend(
    subgraph_env: tuple[AsyncClient, dict[str, str], int, int, int, list[str]],
) -> None:
    """brute 后端不暴露图结构，应返回 400。"""
    ac, headers, _ds, _hnsw_id, brute_id, cell_ids = subgraph_env
    resp = await ac.get(
        f"/api/v1/indexes/{brute_id}/subgraph",
        params={"cell_id": cell_ids[0], "depth": 2},
        headers=headers,
    )
    assert resp.status_code == 400, resp.text
    assert "不暴露" in resp.json()["detail"]


async def test_subgraph_cell_not_found(
    subgraph_env: tuple[AsyncClient, dict[str, str], int, int, int, list[str]],
) -> None:
    """传入不存在的 cell_id 应返回 404。"""
    ac, headers, _ds, hnsw_id, _brute_id, _cells = subgraph_env
    resp = await ac.get(
        f"/api/v1/indexes/{hnsw_id}/subgraph",
        params={"cell_id": "no_such_cell", "depth": 2},
        headers=headers,
    )
    assert resp.status_code == 404, resp.text
    assert "cell_id" in resp.json()["detail"]


async def test_subgraph_invalid_depth(
    subgraph_env: tuple[AsyncClient, dict[str, str], int, int, int, list[str]],
) -> None:
    """depth 越界 (e.g. 5) 应返回 422 校验错误。"""
    ac, headers, _ds, hnsw_id, _brute_id, cell_ids = subgraph_env
    resp = await ac.get(
        f"/api/v1/indexes/{hnsw_id}/subgraph",
        params={"cell_id": cell_ids[0], "depth": 5},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text
