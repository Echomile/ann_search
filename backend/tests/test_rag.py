"""RAG 自然语言查询测试。

覆盖：
    - :class:`MockLLMClient.parse_query` 能从中文/英文关键词中识别 ``cell_type`` 与 ``tissue``；
    - :class:`MockLLMClient.summarize` 在有/无命中下都返回非空字符串；
    - :func:`rag_answer` 端到端：sqlite in-memory + brute 索引，断言返回 hits 与 answer 非空。
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  保证 metadata 完整
from app.db.base import Base
from app.models.dataset import Dataset
from app.models.index_record import IndexRecord
from app.models.user import User
from app.schemas.rag import RagQueryRequest
from app.services import search as search_service
from app.services.ann.brute_backend import BruteBackend
from app.services.rag import MockLLMClient, rag_answer

TEST_DSN = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """提供独立的内存 SQLite 会话。"""
    engine = create_async_engine(
        TEST_DSN,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_maker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
        class_=AsyncSession,
        autoflush=False,
        autocommit=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with session_maker() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
def dataset_artifacts(tmp_path: Path) -> tuple[Path, list[str]]:
    """构造一个 60×8 的小数据集，含 ``cell_type``、``tissue``、``disease`` 三列。"""
    dim = 8
    n = 60
    rng = np.random.default_rng(0)
    vectors = rng.normal(size=(n, dim)).astype(np.float32)
    cell_ids = [f"cell_{i:03d}" for i in range(n)]
    cell_types = ["hepatocyte" if i % 3 == 0 else ("endothelial" if i % 3 == 1 else "macrophage") for i in range(n)]
    tissues = ["liver"] * n
    diseases = ["normal" if i % 4 != 0 else "fibrosis" for i in range(n)]
    metadata = pd.DataFrame(
        {"cell_type": cell_types, "tissue": tissues, "disease": diseases}
    )

    dataset_dir = tmp_path / "ds1"
    dataset_dir.mkdir()
    np.save(dataset_dir / "vectors.npy", vectors)
    with (dataset_dir / "cell_ids.json").open("w", encoding="utf-8") as fp:
        json.dump(cell_ids, fp)
    metadata.to_csv(dataset_dir / "metadata.csv", index=False)

    search_service.clear_dataset_cache()
    return dataset_dir, ["cell_type", "tissue", "disease"]


def test_mock_parse_query_recognizes_hepatocyte() -> None:
    """识别中英文混合的 “肝细胞 hepatocyte” 关键词。"""
    client = MockLLMClient()
    parsed = client.parse_query(
        "在儿童肝脏中找肝细胞 hepatocyte 的代表样本，top 5",
        available_filters=["cell_type", "tissue", "disease"],
    )
    assert parsed.filters.get("cell_type") == "hepatocyte"
    assert parsed.filters.get("tissue") == "liver"
    assert parsed.top_k == 5
    assert parsed.intent


def test_mock_parse_query_ignores_unavailable_columns() -> None:
    """``available_filters`` 不包含 ``tissue`` 时不应将其写入 filters。"""
    client = MockLLMClient()
    parsed = client.parse_query(
        "在肝脏中找 hepatocyte",
        available_filters=["cell_type"],
    )
    assert parsed.filters == {"cell_type": "hepatocyte"}


def test_mock_parse_query_extracts_cell_id() -> None:
    """显式 ``cell_id=xxx`` 应优先识别。"""
    client = MockLLMClient()
    parsed = client.parse_query(
        'cell_id="cell_007" 的相似细胞',
        available_filters=["cell_type"],
    )
    assert parsed.cell_id == "cell_007"


def test_mock_summarize_non_empty_for_empty_hits() -> None:
    """无命中时仍应返回非空字符串。"""
    answer = MockLLMClient().summarize("找肝细胞", hits=[])
    assert isinstance(answer, str)
    assert len(answer) > 0


def test_mock_summarize_aggregates_cell_types() -> None:
    """模板总结应包含命中数量与最高频细胞类型。"""
    hits = [
        {"rank": 1, "cell_id": "c1", "distance": 0.1, "meta": {"cell_type": "hepatocyte", "tissue": "liver"}},
        {"rank": 2, "cell_id": "c2", "distance": 0.2, "meta": {"cell_type": "hepatocyte", "tissue": "liver"}},
        {"rank": 3, "cell_id": "c3", "distance": 0.3, "meta": {"cell_type": "endothelial", "tissue": "liver"}},
    ]
    answer = MockLLMClient().summarize("找肝细胞", hits=hits)
    assert "3" in answer
    assert "hepatocyte" in answer
    assert "c1" in answer


async def _seed_dataset_with_index(
    db: AsyncSession,
    dataset_dir: Path,
    meta_columns: list[str],
) -> tuple[User, Dataset, IndexRecord]:
    """在测试库中插入 user/dataset/index_record，返回对应实体。"""
    user = User(username="rag_user", password_hash="x", role="user")
    db.add(user)
    await db.flush()

    dataset = Dataset(
        owner_id=user.id,
        name="liver-tiny",
        h5ad_path=str(dataset_dir / "raw.h5ad"),
        vectors_path=str(dataset_dir),
        status="ready",
        cell_count=60,
        vector_dim=8,
        vector_source="X_pca",
        meta_columns=meta_columns,
    )
    db.add(dataset)
    await db.flush()

    record = IndexRecord(
        dataset_id=dataset.id,
        backend="brute",
        metric="l2",
        params={},
        index_path=str(dataset_dir / "brute.npy"),
        build_time_seconds=0.0,
        memory_mb=0.0,
        status="ready",
    )
    db.add(record)
    await db.flush()
    await db.commit()
    return user, dataset, record


async def test_rag_answer_end_to_end(
    db_session: AsyncSession,
    dataset_artifacts: tuple[Path, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """端到端：解析 → 检索 → 总结，hits 与 answer 非空。"""
    dataset_dir, meta_columns = dataset_artifacts
    user, dataset, _record = await _seed_dataset_with_index(db_session, dataset_dir, meta_columns)

    backend = BruteBackend(dim=8, metric="l2")
    vectors = np.load(dataset_dir / "vectors.npy")
    backend.build(vectors)

    def _stub_get_backend(*_args: object, **_kwargs: object) -> BruteBackend:
        return backend

    monkeypatch.setattr(search_service, "get_index_backend", _stub_get_backend)

    response = await rag_answer(
        db=db_session,
        user_id=user.id,
        request=RagQueryRequest(
            dataset_id=dataset.id,
            query="在儿童肝脏中找肝细胞 hepatocyte 的代表样本",
            top_k=5,
        ),
        llm=MockLLMClient(),
    )
    assert response.parsed.filters.get("cell_type") == "hepatocyte"
    assert len(response.hits) > 0
    for hit in response.hits:
        assert hit["meta"]["cell_type"] == "hepatocyte"
    assert isinstance(response.answer, str)
    assert len(response.answer) > 0
    assert response.query_time_ms >= 0.0


async def test_rag_answer_respects_request_top_k(
    db_session: AsyncSession,
    dataset_artifacts: tuple[Path, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``request.top_k`` 应该收紧 LLM 解析的 top_k，避免越界。"""
    dataset_dir, meta_columns = dataset_artifacts
    user, dataset, _record = await _seed_dataset_with_index(db_session, dataset_dir, meta_columns)

    backend = BruteBackend(dim=8, metric="l2")
    vectors = np.load(dataset_dir / "vectors.npy")
    backend.build(vectors)
    monkeypatch.setattr(search_service, "get_index_backend", lambda *a, **k: backend)

    response = await rag_answer(
        db=db_session,
        user_id=user.id,
        request=RagQueryRequest(
            dataset_id=dataset.id,
            query="找 hepatocyte",
            top_k=3,
        ),
        llm=MockLLMClient(),
    )
    assert len(response.hits) <= 3
