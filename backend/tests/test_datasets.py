"""数据集 CRUD 接口测试。

覆盖：
    - 未登录请求 ``GET /datasets`` 返回 ``401``；
    - 构造极小 .h5ad，覆盖 ``upload -> list -> status -> get -> delete`` 流程，
      ARQ 入队通过 monkeypatch 替换为不依赖 Redis 的 stub；
    - P3 防御性测试：通过 ``event.listen("after_cursor_execute")`` 抓取真实 SQL，
      验证 ``GET /datasets/{id}/indexes`` 与 ``GET /indexes/{id}/status``
      不再触发 N+1 查询。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  保证 alembic metadata 完整
from app.api.deps import get_db
from app.api.v1 import datasets as datasets_module
from app.core.config import settings
from app.db.base import Base
from app.main import app
from app.models.index_record import IndexRecord

TEST_DSN = "sqlite+aiosqlite:///:memory:"
_test_engine = create_async_engine(
    TEST_DSN,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=_test_engine,
    expire_on_commit=False,
    class_=AsyncSession,
    autoflush=False,
    autocommit=False,
)


async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
    """测试用 ``get_db``：使用本文件内的 SQLite 会话。"""
    async with _TestSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def setup_db() -> AsyncGenerator[None, None]:
    """每个测试一个干净的内存库，并把 ``get_db`` 依赖切换到测试库。"""
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_db, None)
        async with _test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def http_client(setup_db: None) -> AsyncGenerator[AsyncClient, None]:
    """绑定到测试库的 httpx 异步客户端。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


async def _login(client: AsyncClient, username: str, password: str) -> dict[str, str]:
    """注册并登录用户，返回 ``Authorization`` 请求头字典。"""
    resp = await client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 201, resp.text

    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": username, "password": password},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def test_list_empty(http_client: AsyncClient) -> None:
    """未登录访问 ``GET /datasets`` 应返回 401。"""
    resp = await http_client.get("/api/v1/datasets")
    assert resp.status_code == 401


async def test_dataset_crud_minimal(
    http_client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """覆盖最小化的 CRUD 流程，不真正经过 ARQ / Scanpy。

    步骤：
        1. monkeypatch 把 DATA/PROCESSED/INDEX_DIR 指向 ``tmp_path``；
        2. 替换 ``enqueue_preprocess`` 为本地 stub；
        3. 用 ``anndata`` 临时构造一个 5 cells × 4 genes 的 .h5ad；
        4. 走完 upload -> list -> status -> detail -> delete -> 404。
    """
    anndata = pytest.importorskip("anndata")
    import numpy as np

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(settings, "PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setattr(settings, "INDEX_DIR", str(tmp_path / "indexes"))

    async def fake_enqueue(dataset_id: int) -> str:
        return f"fake-job-{dataset_id}"

    monkeypatch.setattr(datasets_module, "enqueue_preprocess", fake_enqueue)

    adata = anndata.AnnData(X=np.random.rand(5, 4).astype(np.float32))
    h5ad_path = tmp_path / "tiny.h5ad"
    adata.write_h5ad(str(h5ad_path))

    headers = await _login(http_client, "tester", "pa55word")

    with h5ad_path.open("rb") as f:
        resp = await http_client.post(
            "/api/v1/datasets/upload",
            headers=headers,
            files={"file": ("tiny.h5ad", f, "application/octet-stream")},
            data={"name": "tiny"},
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    dataset_id = body["dataset"]["id"]
    assert body["dataset"]["name"] == "tiny"
    assert body["dataset"]["status"] == "uploading"
    assert body["task_id"] == f"fake-job-{dataset_id}"

    raw_dir = Path(settings.DATA_DIR) / "raw" / str(body["dataset"]["owner_id"])
    assert raw_dir.is_dir()
    assert any(p.suffix == ".h5ad" for p in raw_dir.iterdir())

    resp = await http_client.get("/api/v1/datasets", headers=headers)
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["id"] == dataset_id

    resp = await http_client.get(f"/api/v1/datasets/{dataset_id}/status", headers=headers)
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["dataset_id"] == dataset_id
    assert payload["status"] == "uploading"
    assert payload["cell_count"] is None
    assert payload["vector_dim"] is None

    resp = await http_client.get(f"/api/v1/datasets/{dataset_id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == dataset_id

    resp = await http_client.delete(f"/api/v1/datasets/{dataset_id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True, "dataset_id": dataset_id}

    assert not list(raw_dir.iterdir())

    resp = await http_client.get(f"/api/v1/datasets/{dataset_id}", headers=headers)
    assert resp.status_code == 404


async def test_get_dataset_forbidden_for_other_user(
    http_client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 用户上传的数据集，B 用户访问应返回 403。"""
    anndata = pytest.importorskip("anndata")
    import numpy as np

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(settings, "PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setattr(settings, "INDEX_DIR", str(tmp_path / "indexes"))

    async def fake_enqueue(dataset_id: int) -> str:
        return ""

    monkeypatch.setattr(datasets_module, "enqueue_preprocess", fake_enqueue)

    adata = anndata.AnnData(X=np.random.rand(3, 3).astype(np.float32))
    h5ad_path = tmp_path / "tiny.h5ad"
    adata.write_h5ad(str(h5ad_path))

    headers_a = await _login(http_client, "alice", "alice_pw")
    with h5ad_path.open("rb") as f:
        resp = await http_client.post(
            "/api/v1/datasets/upload",
            headers=headers_a,
            files={"file": ("tiny.h5ad", f, "application/octet-stream")},
            data={"name": "alice-ds"},
        )
    assert resp.status_code == 201
    dataset_id = resp.json()["dataset"]["id"]

    headers_b = await _login(http_client, "bob", "bob_pw00")
    resp = await http_client.get(f"/api/v1/datasets/{dataset_id}", headers=headers_b)
    assert resp.status_code == 403

    resp = await http_client.delete(f"/api/v1/datasets/{dataset_id}", headers=headers_b)
    assert resp.status_code == 403

    resp = await http_client.get("/api/v1/datasets", headers=headers_b)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_upload_duplicate_name_409(
    http_client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同名再次上传应返回 409，且 detail 含明确提示文案。"""
    anndata = pytest.importorskip("anndata")
    import numpy as np

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(settings, "PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setattr(settings, "INDEX_DIR", str(tmp_path / "indexes"))

    async def fake_enqueue(dataset_id: int) -> str:
        return ""

    monkeypatch.setattr(datasets_module, "enqueue_preprocess", fake_enqueue)

    adata = anndata.AnnData(X=np.random.rand(3, 3).astype(np.float32))
    h5ad_path = tmp_path / "dup.h5ad"
    adata.write_h5ad(str(h5ad_path))

    headers = await _login(http_client, "dupuser", "duppass00")

    with h5ad_path.open("rb") as f:
        resp = await http_client.post(
            "/api/v1/datasets/upload",
            headers=headers,
            files={"file": ("dup.h5ad", f, "application/octet-stream")},
            data={"name": "dup_ds"},
        )
    assert resp.status_code == 201, resp.text

    with h5ad_path.open("rb") as f:
        resp = await http_client.post(
            "/api/v1/datasets/upload",
            headers=headers,
            files={"file": ("dup.h5ad", f, "application/octet-stream")},
            data={"name": "dup_ds"},
        )
    assert resp.status_code == 409, resp.text
    assert "已存在" in resp.json()["detail"]


async def test_rename_dataset(
    http_client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PATCH /datasets/{id} 支持重命名 + 同名 409 + 跨用户 403。"""
    anndata = pytest.importorskip("anndata")
    import numpy as np

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(settings, "PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setattr(settings, "INDEX_DIR", str(tmp_path / "indexes"))

    async def fake_enqueue(dataset_id: int) -> str:
        return ""

    monkeypatch.setattr(datasets_module, "enqueue_preprocess", fake_enqueue)

    adata = anndata.AnnData(X=np.random.rand(3, 3).astype(np.float32))
    h5ad_path = tmp_path / "rn.h5ad"
    adata.write_h5ad(str(h5ad_path))

    headers = await _login(http_client, "rnuser", "rnpass00")

    async def _upload(name: str) -> int:
        with h5ad_path.open("rb") as f:
            resp = await http_client.post(
                "/api/v1/datasets/upload",
                headers=headers,
                files={"file": ("rn.h5ad", f, "application/octet-stream")},
                data={"name": name},
            )
        assert resp.status_code == 201, resp.text
        return int(resp.json()["dataset"]["id"])

    ds_a = await _upload("alpha")
    ds_b = await _upload("beta")

    # 正常重命名
    resp = await http_client.patch(
        f"/api/v1/datasets/{ds_a}", headers=headers, json={"name": "alpha2"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "alpha2"

    # 同名 409
    resp = await http_client.patch(
        f"/api/v1/datasets/{ds_a}", headers=headers, json={"name": "beta"}
    )
    assert resp.status_code == 409
    assert "已存在" in resp.json()["detail"]

    # 同名（自身现名）应直接 200 + 不变
    resp = await http_client.patch(
        f"/api/v1/datasets/{ds_a}", headers=headers, json={"name": "alpha2"}
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "alpha2"

    # 跨用户 403
    headers_other = await _login(http_client, "rnother", "rnpass99")
    resp = await http_client.patch(
        f"/api/v1/datasets/{ds_b}", headers=headers_other, json={"name": "stolen"}
    )
    assert resp.status_code == 403


async def test_cleanup_orphan(
    http_client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """构造一个 status=failed 的孤儿数据集，孤儿清理接口应将其删除。

    步骤：
        1. 上传一个数据集（status=uploading），后端 stub 不入队；
        2. 直接通过 service 把状态改为 ``failed``；
        3. 调用 ``DELETE /datasets/orphan``，断言 ``count=1`` 且包含其 ID；
        4. ``GET /datasets`` 不再列出它。
    """
    anndata = pytest.importorskip("anndata")
    import numpy as np

    from app.services import dataset_service

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(settings, "PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setattr(settings, "INDEX_DIR", str(tmp_path / "indexes"))

    async def fake_enqueue(dataset_id: int) -> str:
        return ""

    monkeypatch.setattr(datasets_module, "enqueue_preprocess", fake_enqueue)

    adata = anndata.AnnData(X=np.random.rand(3, 3).astype(np.float32))
    h5ad_path = tmp_path / "orphan.h5ad"
    adata.write_h5ad(str(h5ad_path))

    headers = await _login(http_client, "orphanuser", "orphanpw0")

    with h5ad_path.open("rb") as f:
        resp = await http_client.post(
            "/api/v1/datasets/upload",
            headers=headers,
            files={"file": ("orphan.h5ad", f, "application/octet-stream")},
            data={"name": "orphan_ds"},
        )
    assert resp.status_code == 201, resp.text
    dataset_id = resp.json()["dataset"]["id"]
    owner_id = resp.json()["dataset"]["owner_id"]

    async with _TestSessionLocal() as session:
        ds = await dataset_service.get_dataset(session, dataset_id)
        assert ds is not None
        ds.status = "failed"
        await session.commit()

    resp = await http_client.delete("/api/v1/datasets/orphan", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 1
    assert dataset_id in body["deleted_ids"]

    resp = await http_client.get("/api/v1/datasets", headers=headers)
    assert resp.status_code == 200
    ids = [item["id"] for item in resp.json()]
    assert dataset_id not in ids

    resp = await http_client.delete("/api/v1/datasets/orphan", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"deleted_ids": [], "count": 0}

    _ = owner_id


async def test_upload_progress_not_found(http_client: AsyncClient) -> None:
    """访问不存在数据集的 ``/upload-progress`` 应返回 ``404``。"""
    headers = await _login(http_client, "puser", "ppass1234")
    resp = await http_client.get(
        "/api/v1/datasets/999/upload-progress",
        headers=headers,
    )
    assert resp.status_code == 404


@contextmanager
def _capture_select_sql() -> Any:
    """注册 ``after_cursor_execute`` 监听器，统计 SELECT 语句明细。

    Yields:
        list[str]: 期间执行过的所有 SELECT 语句快照，调用方可据此断言
        N+1 是否消除（例如同时命中 ``datasets`` / ``index_records`` 的语句条数）。
    """
    statements: list[str] = []

    def _on_after_cursor_execute(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: Any,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    sync_engine = _test_engine.sync_engine
    event.listen(sync_engine, "after_cursor_execute", _on_after_cursor_execute)
    try:
        yield statements
    finally:
        event.remove(sync_engine, "after_cursor_execute", _on_after_cursor_execute)


async def _seed_dataset_with_indexes(
    http_client: AsyncClient,
    headers: dict[str, str],
    h5ad_path: Path,
    *,
    n_indexes: int = 3,
) -> int:
    """上传一个数据集并直接通过 ORM 写入 ``n_indexes`` 条索引记录。

    Args:
        http_client: 测试客户端。
        headers: 已登录的 Authorization 头。
        h5ad_path: 预先生成的 .h5ad 文件路径。
        n_indexes: 要插入的索引记录数量。

    Returns:
        int: 数据集 ID。
    """
    with h5ad_path.open("rb") as f:
        resp = await http_client.post(
            "/api/v1/datasets/upload",
            headers=headers,
            files={"file": ("seed.h5ad", f, "application/octet-stream")},
            data={"name": "seed"},
        )
    assert resp.status_code == 201, resp.text
    dataset_id = int(resp.json()["dataset"]["id"])

    async with _TestSessionLocal() as session:
        for i in range(n_indexes):
            record = IndexRecord(
                dataset_id=dataset_id,
                backend="brute",
                metric="l2",
                params={"i": i},
                status="ready",
            )
            session.add(record)
        await session.commit()
    return dataset_id


async def test_list_dataset_indexes_no_n_plus_one(
    http_client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P3 防御：``GET /datasets/{id}/indexes`` 应只触发 1 条针对 datasets/index_records 的 SELECT。

    旧实现：先 ``SELECT datasets`` 再 ``SELECT index_records``（两条 SQL）。
    新实现：``joinedload(Dataset.indexes)`` 合并为单条 ``LEFT OUTER JOIN``，
    避免在前端轮询索引列表时把 round-trip 放大。
    """
    anndata = pytest.importorskip("anndata")
    import numpy as np

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(settings, "PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setattr(settings, "INDEX_DIR", str(tmp_path / "indexes"))

    async def fake_enqueue(dataset_id: int) -> str:
        return ""

    monkeypatch.setattr(datasets_module, "enqueue_preprocess", fake_enqueue)

    adata = anndata.AnnData(X=np.random.rand(3, 3).astype(np.float32))
    h5ad_path = tmp_path / "n1.h5ad"
    adata.write_h5ad(str(h5ad_path))

    headers = await _login(http_client, "n1user", "n1pass00")
    dataset_id = await _seed_dataset_with_indexes(
        http_client, headers, h5ad_path, n_indexes=5
    )

    with _capture_select_sql() as statements:
        resp = await http_client.get(
            f"/api/v1/datasets/{dataset_id}/indexes",
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 5

    target = [
        s
        for s in statements
        if "datasets" in s.lower() or "index_records" in s.lower()
    ]
    assert len(target) == 1, (
        f"期望 1 条联合查询，实际 {len(target)} 条 SQL：\n" + "\n---\n".join(target)
    )
    joined = target[0].lower()
    assert "index_records" in joined and "datasets" in joined, (
        f"期望 SQL 同时引用 datasets 与 index_records：{joined}"
    )


async def test_get_index_status_no_n_plus_one(
    http_client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P3 防御：``GET /indexes/{id}/status`` 应只触发 1 条针对 datasets/index_records 的 SELECT。

    旧实现：``db.get(IndexRecord)`` + ``db.get(Dataset)``（两条 SQL）。
    新实现：``joinedload(IndexRecord.dataset)`` 把双查询合并为单条 ``LEFT OUTER JOIN``，
    在评测页轮询场景下显著减少 round-trip。
    """
    anndata = pytest.importorskip("anndata")
    import numpy as np

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(settings, "PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setattr(settings, "INDEX_DIR", str(tmp_path / "indexes"))

    async def fake_enqueue(dataset_id: int) -> str:
        return ""

    monkeypatch.setattr(datasets_module, "enqueue_preprocess", fake_enqueue)

    adata = anndata.AnnData(X=np.random.rand(3, 3).astype(np.float32))
    h5ad_path = tmp_path / "n2.h5ad"
    adata.write_h5ad(str(h5ad_path))

    headers = await _login(http_client, "n2user", "n2pass00")
    dataset_id = await _seed_dataset_with_indexes(
        http_client, headers, h5ad_path, n_indexes=1
    )
    async with _TestSessionLocal() as session:
        row = await session.execute(
            select(IndexRecord.id).where(IndexRecord.dataset_id == dataset_id)
        )
        index_id = int(row.scalar_one())

    with _capture_select_sql() as statements:
        resp = await http_client.get(
            f"/api/v1/indexes/{index_id}/status",
            headers=headers,
        )
    assert resp.status_code == 200, resp.text

    target = [
        s
        for s in statements
        if "datasets" in s.lower() or "index_records" in s.lower()
    ]
    assert len(target) == 1, (
        f"期望 1 条联合查询，实际 {len(target)} 条 SQL：\n" + "\n---\n".join(target)
    )
    joined = target[0].lower()
    assert "datasets" in joined and "index_records" in joined, (
        f"期望 SQL 同时引用 datasets 与 index_records：{joined}"
    )
