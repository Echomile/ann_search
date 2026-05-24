"""跨数据集语义对齐 (D7) 服务 + 端到端测试。

覆盖：
    - ``test_align_datasets_intersect_only_two_datasets``
        最小可运行 fixture: 构造 2 个共享基因子集的 .h5ad（pytest 不依赖
        真实 PBMC），调用 :func:`alignment_service.align_datasets`，
        检查 ``AlignedDataset`` 字段、磁盘产物、cell_map 行序与原始
        dataset 顺序一致。
    - ``test_align_datasets_three_datasets``
        3 个 dataset 也能正确合并，``cell_count`` 等于三者之和，
        ``common_genes_count`` 为基因交集大小。
    - ``test_align_requires_two_sources``
        长度 < 2 抛 ValueError；同时验证 REST 端 422。
    - ``test_align_endpoint_persists_aligned_record``
        端到端：调 ``POST /datasets/align`` 后能 ``GET /datasets/aligned``
        看到记录，且 ``DELETE /datasets/aligned/{id}`` 能清理磁盘。
    - ``test_aligned_path_search_returns_hits``
        端到端：完成对齐后调 ``POST /search/multi-dataset``
        with ``aligned_dataset_id``，验证响应回填 ``aligned_dataset_id``
        且 ``index_backend == "aligned-brute"``，命中的 cell 在原 dataset
        的子集里。
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from scipy import sparse as sp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  确保所有模型注册到 metadata
from app.api.deps import get_db
from app.db.base import Base
from app.main import app
from app.models.aligned_dataset import AlignedDataset
from app.models.dataset import Dataset
from app.models.user import User
from app.services import alignment as alignment_service


def _write_synthetic_h5ad(
    path: Path,
    *,
    n_cells: int,
    gene_names: list[str],
    seed: int,
    cell_prefix: str,
    cell_type_pool: list[str] | None = None,
) -> None:
    """构造一个最小可用的 .h5ad 用于对齐测试。

    Args:
        path: 目标 .h5ad 路径。
        n_cells: 细胞数量。
        gene_names: 基因列名（``var_names``）。
        seed: 随机数种子，便于复现。
        cell_prefix: cell_id 前缀，避免不同 dataset 之间 cell_id 冲突。
        cell_type_pool: 可选的离散标签池，用于 obs.cell_type 列。
    """
    rng = np.random.default_rng(seed)
    # 用泊松分布产稀疏计数矩阵，更接近真实单细胞数据
    counts = rng.poisson(lam=1.0, size=(n_cells, len(gene_names))).astype(np.float32)
    sparse_x = sp.csr_matrix(counts)
    cell_types = cell_type_pool or ["T", "B"]
    obs = pd.DataFrame(
        {
            "cell_type": [cell_types[i % len(cell_types)] for i in range(n_cells)],
            "n_genes": (counts > 0).sum(axis=1).astype(int),
        },
        index=[f"{cell_prefix}{i:03d}" for i in range(n_cells)],
    )
    var = pd.DataFrame(index=gene_names)
    adata = ad.AnnData(X=sparse_x, obs=obs, var=var)
    adata.write_h5ad(path)


# ---------- pytest fixtures ----------


@pytest_asyncio.fixture
async def align_env(
    tmp_path,
) -> AsyncGenerator[
    tuple[AsyncClient, dict[str, str], AsyncSession, list[int]],
    None,
]:
    """搭建 D7 端到端环境：1 个用户 + 2 个 dataset（共享 60 个基因）。"""
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

    # 共享 60 个基因；dataset A 额外 20 个独有，dataset B 额外 15 个独有
    common_genes = [f"GENE_{i:03d}" for i in range(60)]
    genes_a = common_genes + [f"GENE_A_{i:02d}" for i in range(20)]
    genes_b = common_genes + [f"GENE_B_{i:02d}" for i in range(15)]

    h5ad_a = tmp_path / "ds_a.h5ad"
    h5ad_b = tmp_path / "ds_b.h5ad"
    _write_synthetic_h5ad(h5ad_a, n_cells=40, gene_names=genes_a, seed=11, cell_prefix="A_")
    _write_synthetic_h5ad(h5ad_b, n_cells=35, gene_names=genes_b, seed=22, cell_prefix="B_")

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            reg = await ac.post(
                "/api/v1/auth/register",
                json={"username": "align_user", "password": "align_pw_123"},
            )
            assert reg.status_code == 201, reg.text
            login = await ac.post(
                "/api/v1/auth/login",
                data={"username": "align_user", "password": "align_pw_123"},
            )
            assert login.status_code == 200, login.text
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

            async with session_maker() as session:
                user = (
                    await session.execute(select(User).where(User.username == "align_user"))
                ).scalar_one()

                dataset_a = Dataset(
                    owner_id=user.id,
                    name="ds_a",
                    h5ad_path=str(h5ad_a),
                    status="ready",
                    cell_count=40,
                    vector_dim=10,
                )
                dataset_b = Dataset(
                    owner_id=user.id,
                    name="ds_b",
                    h5ad_path=str(h5ad_b),
                    status="ready",
                    cell_count=35,
                    vector_dim=10,
                )
                session.add_all([dataset_a, dataset_b])
                await session.commit()
                await session.refresh(dataset_a)
                await session.refresh(dataset_b)
                ids = [int(dataset_a.id), int(dataset_b.id)]

            # 给 caller 一个独立 session 用于服务层直接操作
            session = session_maker()
            try:
                yield ac, headers, session, ids
            finally:
                await session.close()
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


# ---------- 服务层单元测试 ----------


async def test_align_datasets_intersect_only_two_datasets(
    align_env: tuple[AsyncClient, dict[str, str], AsyncSession, list[int]],
) -> None:
    """intersect_only 对齐两个数据集：cell_count 相加、common_genes=60、向量落盘。"""
    _ac, _headers, session, ids = align_env
    aligned_id = await alignment_service.align_datasets(
        session=session,
        dataset_ids=ids,
        method="intersect_only",
        target_dim=8,
        user_id=None,
        name="ds_a_b_intersect",
    )
    aligned = await session.get(AlignedDataset, aligned_id)
    assert aligned is not None
    assert aligned.status == "done"
    assert aligned.cell_count == 40 + 35
    assert aligned.common_genes_count == 60
    # PCA 可能因数据集小而压缩 target_dim
    assert 1 <= aligned.target_dim <= 8
    assert aligned.method == "intersect_only"

    vp = Path(aligned.vectors_path or "")
    cm = Path(aligned.cell_map_path or "")
    assert vp.is_file()
    assert cm.is_file()

    vectors = np.load(vp)
    assert vectors.shape[0] == aligned.cell_count
    assert vectors.shape[1] == aligned.target_dim

    with cm.open(encoding="utf-8") as f:
        cell_map = json.load(f)
    assert len(cell_map) == aligned.cell_count
    # 前 40 条来自 dataset A，后 35 条来自 dataset B
    assert all(entry["source_dataset_id"] == ids[0] for entry in cell_map[:40])
    assert all(entry["source_dataset_id"] == ids[1] for entry in cell_map[40:])
    assert all(entry["cell_id"].startswith("A_") for entry in cell_map[:40])
    assert all(entry["cell_id"].startswith("B_") for entry in cell_map[40:])


async def test_align_datasets_three_datasets(
    tmp_path,
    align_env: tuple[AsyncClient, dict[str, str], AsyncSession, list[int]],
) -> None:
    """三个数据集也能正确合并，cell_count == 三者之和。"""
    ac, _headers, session, ids = align_env
    # 在 fixture 之上再追加一个 dataset C
    common_genes = [f"GENE_{i:03d}" for i in range(60)]
    genes_c = common_genes + [f"GENE_C_{i:02d}" for i in range(5)]
    h5ad_c = tmp_path / "ds_c.h5ad"
    _write_synthetic_h5ad(h5ad_c, n_cells=20, gene_names=genes_c, seed=33, cell_prefix="C_")

    # 通过 select 拿到 owner_id
    user = (await session.execute(select(User).where(User.username == "align_user"))).scalar_one()
    dataset_c = Dataset(
        owner_id=user.id,
        name="ds_c",
        h5ad_path=str(h5ad_c),
        status="ready",
        cell_count=20,
        vector_dim=10,
    )
    session.add(dataset_c)
    await session.commit()
    await session.refresh(dataset_c)
    all_ids = ids + [int(dataset_c.id)]

    aligned_id = await alignment_service.align_datasets(
        session=session,
        dataset_ids=all_ids,
        method="intersect_only",
        target_dim=6,
    )
    aligned = await session.get(AlignedDataset, aligned_id)
    assert aligned is not None
    assert aligned.cell_count == 40 + 35 + 20
    assert aligned.common_genes_count == 60


async def test_align_requires_two_sources(
    align_env: tuple[AsyncClient, dict[str, str], AsyncSession, list[int]],
) -> None:
    """source_dataset_ids 长度 < 2 时应该被服务层与 REST 端同时拒绝。"""
    ac, headers, session, ids = align_env

    # 服务层 ValueError
    with pytest.raises(ValueError, match="至少需要 2 个数据集"):
        await alignment_service.align_datasets(
            session=session,
            dataset_ids=[ids[0]],
            method="intersect_only",
            target_dim=8,
        )

    # REST 端 422 (Pydantic min_length=2)
    resp = await ac.post(
        "/api/v1/datasets/align",
        json={"source_dataset_ids": [ids[0]], "method": "intersect_only", "target_dim": 8},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text


async def test_align_endpoint_persists_aligned_record(
    align_env: tuple[AsyncClient, dict[str, str], AsyncSession, list[int]],
) -> None:
    """端到端: align -> list -> get -> delete 全链路。"""
    ac, headers, _session, ids = align_env

    # 1. align (POST)
    resp = await ac.post(
        "/api/v1/datasets/align",
        json={
            "source_dataset_ids": ids,
            "method": "intersect_only",
            "target_dim": 8,
            "name": "endpoint_test",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "endpoint_test"
    assert body["method"] == "intersect_only"
    assert body["status"] == "done"
    assert body["cell_count"] == 75
    assert body["common_genes_count"] == 60
    assert body["source_dataset_ids"] == ids
    aligned_id = int(body["id"])

    # 2. list
    resp = await ac.get("/api/v1/datasets/aligned", headers=headers)
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert any(it["id"] == aligned_id for it in items)

    # 3. get one
    resp = await ac.get(f"/api/v1/datasets/aligned/{aligned_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == aligned_id

    # 4. delete
    resp = await ac.delete(f"/api/v1/datasets/aligned/{aligned_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"deleted": True, "aligned_dataset_id": aligned_id}

    # 5. 再 get 应 404
    resp = await ac.get(f"/api/v1/datasets/aligned/{aligned_id}", headers=headers)
    assert resp.status_code == 404


async def test_aligned_path_search_returns_hits(
    align_env: tuple[AsyncClient, dict[str, str], AsyncSession, list[int]],
) -> None:
    """走对齐路径的 multi-dataset 检索应该回填 aligned_dataset_id。"""
    ac, headers, session, ids = align_env

    # 先对齐
    aligned_id = await alignment_service.align_datasets(
        session=session,
        dataset_ids=ids,
        method="intersect_only",
        target_dim=8,
    )
    aligned = await session.get(AlignedDataset, aligned_id)
    assert aligned is not None
    target_dim = aligned.target_dim

    # 用一个随机查询向量
    rng = np.random.default_rng(0)
    query_vec = rng.normal(size=(target_dim,)).astype(np.float32).tolist()

    resp = await ac.post(
        "/api/v1/search/multi-dataset",
        json={
            "dataset_ids": ids,
            "aligned_dataset_id": aligned_id,
            "vector": query_vec,
            "top_k": 5,
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["aligned_dataset_id"] == aligned_id
    assert body["index_backend"] == "aligned-brute"
    assert body["metric"] == "l2"
    assert len(body["hits"]) == 5
    # 每条 hit 都应当带 source_dataset_id 属于参与对齐的 dataset
    src_ids = {hit["source_dataset_id"] for hit in body["hits"]}
    assert src_ids.issubset(set(ids))
