"""RAG 自然语言查询测试。

覆盖：
    - :class:`MockLLMClient.parse_query` 能从中文/英文关键词中识别 ``cell_type`` 与 ``tissue``；
    - :class:`MockLLMClient.summarize` 在有/无命中下都返回非空字符串；
    - :func:`rag_answer` 端到端：sqlite in-memory + brute 索引，断言返回 hits 与 answer 非空。
"""

from __future__ import annotations

import json
import sys
import types
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

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
from app.services.rag import AnthropicClient, MockLLMClient, get_llm_client, rag_answer

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
    cell_types = [
        "hepatocyte" if i % 3 == 0 else ("endothelial" if i % 3 == 1 else "macrophage")
        for i in range(n)
    ]
    tissues = ["liver"] * n
    diseases = ["normal" if i % 4 != 0 else "fibrosis" for i in range(n)]
    metadata = pd.DataFrame({"cell_type": cell_types, "tissue": tissues, "disease": diseases})

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
        {
            "rank": 1,
            "cell_id": "c1",
            "distance": 0.1,
            "meta": {"cell_type": "hepatocyte", "tissue": "liver"},
        },
        {
            "rank": 2,
            "cell_id": "c2",
            "distance": 0.2,
            "meta": {"cell_type": "hepatocyte", "tissue": "liver"},
        },
        {
            "rank": 3,
            "cell_id": "c3",
            "distance": 0.3,
            "meta": {"cell_type": "endothelial", "tissue": "liver"},
        },
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


def _build_fake_anthropic_module(
    response_text: str | Exception,
    captured: dict[str, object] | None = None,
) -> types.ModuleType:
    """构造一个最小可用的 fake ``anthropic`` 模块，供 monkeypatch 注入 ``sys.modules``。

    Args:
        response_text: ``messages.create`` 应返回的文本，或要抛出的异常实例。
        captured: 可选 dict，将记录最近一次 ``create`` 的关键字参数，便于断言。
    """

    class _FakeMessages:
        """模拟 ``Anthropic().messages``。"""

        def create(self, **kwargs: object) -> object:
            if captured is not None:
                captured["kwargs"] = kwargs
            if isinstance(response_text, Exception):
                raise response_text
            block = types.SimpleNamespace(type="text", text=response_text)
            return types.SimpleNamespace(content=[block])

    class _FakeAnthropic:
        """模拟 ``anthropic.Anthropic`` 构造器。"""

        def __init__(self, api_key: str | None = None, **_: object) -> None:
            self.api_key = api_key
            self.messages = _FakeMessages()

    module = types.ModuleType("anthropic")
    module.Anthropic = _FakeAnthropic  # type: ignore[attr-defined]
    return module


def test_anthropic_client_parse_query_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    """``messages.create`` 返回严格 JSON 时，``parse_query`` 应正确转为 :class:`ParsedQuery`。"""
    captured: dict[str, object] = {}
    payload = json.dumps(
        {
            "cell_id": None,
            "filters": {"cell_type": "hepatocyte"},
            "top_k": 8,
            "intent": "查找 hepatocyte",
        },
        ensure_ascii=False,
    )
    fake_module = _build_fake_anthropic_module(payload, captured=captured)
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)

    client = AnthropicClient(model="claude-opus-4-7", api_key="sk-test")
    parsed = client.parse_query(
        "找 hepatocyte",
        available_filters=["cell_type", "tissue"],
    )

    assert parsed.cell_id is None
    assert parsed.filters == {"cell_type": "hepatocyte"}
    assert parsed.top_k == 8
    assert parsed.intent == "查找 hepatocyte"
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["model"] == "claude-opus-4-7"
    assert kwargs["messages"][0]["role"] == "user"  # type: ignore[index]


def test_anthropic_client_parse_query_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """``messages.create`` 抛异常时，``parse_query`` 应回退到 :class:`MockLLMClient`。"""
    fake_module = _build_fake_anthropic_module(RuntimeError("anthropic api boom"))
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)

    client = AnthropicClient(model="claude-opus-4-7", api_key="sk-test")
    parsed = client.parse_query(
        "找肝细胞 hepatocyte，top 5",
        available_filters=["cell_type", "tissue"],
    )

    assert parsed.filters.get("cell_type") == "hepatocyte"
    assert parsed.top_k == 5


def test_factory_returns_mock_when_anthropic_sdk_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``LLM_PROVIDER=anthropic`` 但 SDK 缺失时，工厂应回退 :class:`MockLLMClient` 不抛异常。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setitem(sys.modules, "anthropic", None)

    client = get_llm_client()
    assert isinstance(client, MockLLMClient)


# =============================================================================
# v1.2 D4 LLM Function Calling Agent loop 测试
# =============================================================================

from app.schemas.rag import RagChatRequest  # noqa: E402
from app.services.rag import (  # noqa: E402
    ChatResponse,
    ToolCall,
    chat_with_tools,
    get_session_detail,
    list_user_sessions,
)


@pytest_asyncio.fixture
async def seeded_dataset(
    db_session: AsyncSession,
    dataset_artifacts: tuple[Path, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[User, Dataset, BruteBackend, list[str]]:
    """复用 dataset_artifacts，把 user/dataset/index 写库并注入 brute backend。"""
    dataset_dir, meta_columns = dataset_artifacts
    user, dataset, _record = await _seed_dataset_with_index(db_session, dataset_dir, meta_columns)
    backend = BruteBackend(dim=8, metric="l2")
    vectors = np.load(dataset_dir / "vectors.npy")
    backend.build(vectors)
    monkeypatch.setattr(search_service, "get_index_backend", lambda *a, **k: backend)
    return user, dataset, backend, meta_columns


def test_mock_chat_with_tools_list_datasets() -> None:
    """query 含 “列出所有数据集” 时 MockLLMClient 返回 list_datasets tool_call。"""
    client = MockLLMClient()
    messages = [
        {"role": "system", "content": "agent"},
        {"role": "user", "content": "请帮我列出所有数据集"},
    ]
    resp = client.chat_with_tools(messages, tools=[])
    assert isinstance(resp, ChatResponse)
    assert resp.finish_reason == "tool_calls"
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "list_datasets"


def test_mock_chat_with_tools_search_by_cell_id() -> None:
    """query 含 cell_id 时 MockLLMClient 返回 search_by_cell_id tool_call。"""
    client = MockLLMClient()
    messages = [
        {"role": "system", "content": "agent\ncurrent_dataset_id=5"},
        {"role": "user", "content": "找和 cell_id=cell_007 相似的细胞 top 3"},
    ]
    resp = client.chat_with_tools(messages, tools=[])
    assert resp.finish_reason == "tool_calls"
    tc = resp.tool_calls[0]
    assert tc.name == "search_by_cell_id"
    assert tc.arguments["cell_id"] == "cell_007"
    assert tc.arguments["dataset_id"] == 5
    assert tc.arguments["top_k"] == 3


def test_mock_chat_with_tools_stop_after_tool_result() -> None:
    """已经有 tool result 时 MockLLMClient 应进入收尾，返回 finish_reason=stop。"""
    client = MockLLMClient()
    messages = [
        {"role": "user", "content": "列出数据集"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "list_datasets", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "c1",
            "name": "list_datasets",
            "content": json.dumps({"datasets": [{"id": 1, "name": "liver-tiny"}]}),
        },
    ]
    resp = client.chat_with_tools(messages, tools=[])
    assert resp.finish_reason == "stop"
    assert "liver-tiny" in resp.content


async def test_chat_with_mock_list_datasets(
    db_session: AsyncSession,
    dataset_artifacts: tuple[Path, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """端到端：query=列出所有数据集 → tool_call=list_datasets → 最终回答含数据集名。"""
    dataset_dir, meta_columns = dataset_artifacts
    user, dataset, _record = await _seed_dataset_with_index(db_session, dataset_dir, meta_columns)
    monkeypatch.setattr(search_service, "get_index_backend", lambda *a, **k: None)

    resp = await chat_with_tools(
        db=db_session,
        user=user,
        request=RagChatRequest(query="请列出所有可用数据集"),
        llm=MockLLMClient(),
    )
    assert resp.session_id > 0
    assert resp.iterations == 2
    assert resp.finish_reason == "stop"
    assert any(t.name == "list_datasets" for t in resp.tool_trace)
    assert dataset.name in resp.answer


async def test_chat_with_mock_search_by_cell_id(
    seeded_dataset: tuple[User, Dataset, BruteBackend, list[str]],
    db_session: AsyncSession,
) -> None:
    """端到端：query=找和 cell ABC 相似 → tool_call=search_by_cell_id → answer 含相似细胞信息。"""
    user, dataset, _backend, _meta = seeded_dataset
    resp = await chat_with_tools(
        db=db_session,
        user=user,
        request=RagChatRequest(
            query="找和 cell_id=cell_001 相似的细胞 top 5",
            dataset_id=dataset.id,
        ),
        llm=MockLLMClient(),
    )
    assert resp.finish_reason == "stop"
    assert any(t.name == "search_by_cell_id" and t.ok for t in resp.tool_trace)
    assert len(resp.citations) > 0
    assert all(c.dataset_id == dataset.id for c in resp.citations)
    assert "find top matches" in resp.answer or "cell_id" in resp.answer


async def test_chat_multi_turn_session(
    seeded_dataset: tuple[User, Dataset, BruteBackend, list[str]],
    db_session: AsyncSession,
) -> None:
    """两轮对话：第一轮 list_datasets，第二轮基于结果继续 search_by_cell_id。"""
    user, dataset, _backend, _meta = seeded_dataset

    first = await chat_with_tools(
        db=db_session,
        user=user,
        request=RagChatRequest(query="请列出所有可用数据集"),
        llm=MockLLMClient(),
    )
    assert first.iterations == 2
    sid = first.session_id

    second = await chat_with_tools(
        db=db_session,
        user=user,
        request=RagChatRequest(
            query="基于结果，找和 cell_id=cell_001 相似的细胞 top 3",
            dataset_id=dataset.id,
            session_id=sid,
        ),
        llm=MockLLMClient(),
    )
    assert second.session_id == sid
    assert any(t.name == "search_by_cell_id" for t in second.tool_trace)

    detail = await get_session_detail(db_session, user.id, sid)
    roles = [m.role for m in detail.messages]
    assert roles.count("user") == 2
    assert "assistant" in roles
    assert "tool" in roles
    assert detail.messages[0].role == "user"


async def test_chat_session_persistence_and_listing(
    db_session: AsyncSession,
    dataset_artifacts: tuple[Path, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """创建 session → 写 messages → 拉 session 验证 history + list_user_sessions。"""
    dataset_dir, meta_columns = dataset_artifacts
    user, _dataset, _record = await _seed_dataset_with_index(db_session, dataset_dir, meta_columns)
    monkeypatch.setattr(search_service, "get_index_backend", lambda *a, **k: None)

    r1 = await chat_with_tools(
        db=db_session,
        user=user,
        request=RagChatRequest(query="列出数据集"),
        llm=MockLLMClient(),
    )
    r2 = await chat_with_tools(
        db=db_session,
        user=user,
        request=RagChatRequest(query="再次列出"),
        llm=MockLLMClient(),
    )
    assert r1.session_id != r2.session_id

    sessions = await list_user_sessions(db_session, user.id)
    assert len(sessions) >= 2
    ids = {s.id for s in sessions}
    assert {r1.session_id, r2.session_id}.issubset(ids)
    target = next(s for s in sessions if s.id == r1.session_id)
    assert target.message_count >= 3  # user + assistant(tool_calls) + tool + assistant
    assert target.title.startswith("列出数据集")


class _LoopingLLMClient:
    """专用于测试的桩 LLM：始终返回 tool_call 触发 max_iterations 安全退出。"""

    def parse_query(self, query: str, available_filters: list[str]):  # type: ignore[override]
        return MockLLMClient().parse_query(query, available_filters)

    def summarize(self, query: str, hits: list[dict[str, Any]]) -> str:  # type: ignore[override]
        return "stub"

    def chat_with_tools(  # type: ignore[override]
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ChatResponse:
        return ChatResponse(
            finish_reason="tool_calls",
            tool_calls=[ToolCall(id="loop", name="list_datasets", arguments={})],
        )


async def test_chat_max_iterations_safety(
    db_session: AsyncSession,
    dataset_artifacts: tuple[Path, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM 始终返回 tool_call 时应在 max_iterations 后强制停止。"""
    dataset_dir, meta_columns = dataset_artifacts
    user, _dataset, _record = await _seed_dataset_with_index(db_session, dataset_dir, meta_columns)
    monkeypatch.setattr(search_service, "get_index_backend", lambda *a, **k: None)

    resp = await chat_with_tools(
        db=db_session,
        user=user,
        request=RagChatRequest(query="不会终止", max_iterations=3),
        llm=_LoopingLLMClient(),
    )
    assert resp.iterations == 3
    assert resp.finish_reason == "max_iterations"
    assert "最大工具调用轮数" in resp.answer


async def test_chat_with_invalid_session_id_returns_404(
    db_session: AsyncSession,
    dataset_artifacts: tuple[Path, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """传入不存在的 session_id 应 404。"""
    from fastapi import HTTPException

    dataset_dir, meta_columns = dataset_artifacts
    user, _dataset, _record = await _seed_dataset_with_index(db_session, dataset_dir, meta_columns)
    monkeypatch.setattr(search_service, "get_index_backend", lambda *a, **k: None)

    with pytest.raises(HTTPException) as ei:
        await chat_with_tools(
            db=db_session,
            user=user,
            request=RagChatRequest(query="hello", session_id=9999),
            llm=MockLLMClient(),
        )
    assert ei.value.status_code == 404
