from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import ClassVar

import pytest
from anima_server.db.base import Base
from anima_server.models import MemoryItem, MemoryItemEvidence, User
from anima_server.services.agent import evidence_retrieval
from anima_server.services.agent.evidence_retrieval import (
    RetrievalIntent,
    RetrievalMode,
    compact_evidence_text,
    extract_session_date,
    rerank_evidence_texts,
)
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@contextmanager
def _db_session() -> Generator[Session, None, None]:
    engine: Engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=Session,
    )
    Base.metadata.create_all(bind=engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_extracts_session_date_from_raw_chunk() -> None:
    text = "Session date: 2023/05/29 (Mon) 20:29\nUser: I bought a 1/72 scale B-29."

    assert extract_session_date(text) == "2023/05/29 (Mon) 20:29"


def test_compacts_to_relevant_user_lines_and_keeps_date() -> None:
    text = (
        "Session date: 2023/05/29 (Mon) 20:29\n"
        "User: I bought a 1/72 scale B-29 bomber model kit and a 1/24 scale Camaro.\n"
        "Assistant: Here are many long photo-etching tips that are not the answer."
    )

    compacted = compact_evidence_text(text, query_terms={"model", "kit", "bomber", "camaro"})

    assert "Session date: 2023/05/29" in compacted
    assert "B-29 bomber" in compacted
    assert "Camaro" in compacted
    assert "photo-etching tips" not in compacted


def test_aggregate_rerank_preserves_distinct_model_kit_evidence() -> None:
    texts = [
        "Session date: 2023/05/21\nUser: I finished a Revell F-15 Eagle kit.",
        "Session date: 2023/05/21\nAssistant: Weathering tips for models.",
        "Session date: 2023/05/27\nUser: I started a 1/16 scale German Tiger I tank diorama.",
        "Session date: 2023/05/29\nUser: I got a 1/72 scale B-29 bomber and a 1/24 scale Camaro.",
    ]
    intent = RetrievalIntent(mode=RetrievalMode.AGGREGATE, candidate_limit=50, max_evidence=3)

    ranked = rerank_evidence_texts(
        texts,
        query="How many model kits have I worked on or bought?",
        intent=intent,
    )

    joined = "\n".join(ranked)
    assert "Revell F-15" in joined
    assert "German Tiger" in joined
    assert "B-29 bomber" in joined
    assert "Camaro" in joined
    assert "Weathering tips" not in joined


def test_latest_update_rerank_prefers_newer_entity_evidence() -> None:
    texts = [
        "Session date: 2023/05/21\nUser: Rachel moved to a new apartment in the city.",
        "Session date: 2023/05/26\nUser: Rachel relocated to the suburbs.",
    ]
    intent = RetrievalIntent(mode=RetrievalMode.LATEST_UPDATE, candidate_limit=40, max_evidence=2)

    ranked = rerank_evidence_texts(
        texts,
        query="Where did Rachel move to after her recent relocation?",
        intent=intent,
    )

    assert "suburbs" in ranked[0]


@pytest.mark.asyncio
async def test_retrieve_wide_evidence_uses_intent_candidate_limit(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeItem:
        id = 10
        category = "fact"
        content = "Session date: 2023/05/29\nUser: I got a 1/72 scale B-29 bomber."

    class FakeResult:
        query_embedding: ClassVar[list[float]] = [0.1, 0.2]
        items: ClassVar[list] = [(FakeItem(), 0.9)]

    async def fake_hybrid_search(db, **kwargs):
        calls.append(kwargs)
        return FakeResult()

    monkeypatch.setattr(evidence_retrieval, "hybrid_search", fake_hybrid_search)
    monkeypatch.setattr(evidence_retrieval, "df", lambda user_id, value, **kwargs: value)

    result = await evidence_retrieval.retrieve_wide_evidence(
        db=object(),
        user_id=1,
        query="How many model kits have I worked on or bought?",
        mode=RetrievalMode.AGGREGATE,
        runtime_db=None,
    )

    assert result.mode == RetrievalMode.AGGREGATE
    assert calls[0]["limit"] >= 40
    assert result.query_embedding == [0.1, 0.2]
    assert result.semantic_results[0][0] == 10
    assert "B-29 bomber" in result.semantic_results[0][1]


@pytest.mark.asyncio
async def test_retrieve_wide_evidence_skips_for_direct_intent(monkeypatch) -> None:
    called = False

    async def fake_hybrid_search(db, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("hybrid_search should not be called for DIRECT intent")

    monkeypatch.setattr(evidence_retrieval, "hybrid_search", fake_hybrid_search)
    monkeypatch.setattr(evidence_retrieval, "df", lambda user_id, value, **kwargs: value)

    result = await evidence_retrieval.retrieve_wide_evidence(
        db=object(),
        user_id=1,
        query="What is my dog's name?",
        mode=RetrievalMode.DIRECT,
        runtime_db=None,
    )

    assert result.mode == RetrievalMode.DIRECT
    assert result.semantic_results == []
    assert result.query_embedding is None
    assert called is False


@pytest.mark.asyncio
async def test_retrieve_wide_evidence_prefers_observed_at_for_latest(
    monkeypatch,
) -> None:
    monkeypatch.setattr(evidence_retrieval, "df", lambda user_id, value, **kwargs: value)

    with _db_session() as db:
        user = User(username="latest-evidence", password_hash="x", display_name="Latest")
        db.add(user)
        db.flush()
        old_item = MemoryItem(
            user_id=user.id,
            content="Rachel moved to the city.",
            category="fact",
            importance=3,
        )
        new_item = MemoryItem(
            user_id=user.id,
            content="Rachel moved to the suburbs.",
            category="fact",
            importance=3,
        )
        db.add_all([old_item, new_item])
        db.flush()
        db.add_all(
            [
                MemoryItemEvidence(
                    user_id=user.id,
                    memory_item_id=old_item.id,
                    source_kind="llm_extraction",
                    speaker="user",
                    observed_at=datetime(2023, 5, 21, 9, 0, tzinfo=UTC),
                    evidence_text="Rachel moved to the city.",
                ),
                MemoryItemEvidence(
                    user_id=user.id,
                    memory_item_id=new_item.id,
                    source_kind="llm_extraction",
                    speaker="user",
                    observed_at=datetime(2023, 5, 26, 9, 0, tzinfo=UTC),
                    evidence_text="Rachel relocated to the suburbs.",
                ),
            ]
        )
        db.flush()

        class FakeResult:
            query_embedding: ClassVar[list[float]] = [0.1, 0.2]
            items: ClassVar[list] = [(old_item, 0.99), (new_item, 0.2)]

        async def fake_hybrid_search(db_arg, **kwargs):
            del db_arg, kwargs
            return FakeResult()

        monkeypatch.setattr(evidence_retrieval, "hybrid_search", fake_hybrid_search)

        result = await evidence_retrieval.retrieve_wide_evidence(
            db=db,
            user_id=user.id,
            query="Where did Rachel move most recently?",
            mode=RetrievalMode.LATEST_UPDATE,
        )

    assert result.semantic_results[0][0] == new_item.id
    assert "2023-05-26" in result.semantic_results[0][1]
    assert "suburbs" in result.semantic_results[0][1]


@pytest.mark.asyncio
async def test_retrieve_wide_evidence_expands_distinct_evidence_rows_for_counts(
    monkeypatch,
) -> None:
    monkeypatch.setattr(evidence_retrieval, "df", lambda user_id, value, **kwargs: value)

    with _db_session() as db:
        user = User(username="count-evidence", password_hash="x", display_name="Count")
        db.add(user)
        db.flush()
        item = MemoryItem(
            user_id=user.id,
            content="Model kit purchases.",
            category="fact",
            importance=4,
        )
        db.add(item)
        db.flush()
        db.add_all(
            [
                MemoryItemEvidence(
                    user_id=user.id,
                    memory_item_id=item.id,
                    source_kind="eval_import",
                    speaker="user",
                    observed_at=datetime(2023, 5, 21, 10, 0, tzinfo=UTC),
                    evidence_text="I finished a Revell F-15 Eagle kit.",
                ),
                MemoryItemEvidence(
                    user_id=user.id,
                    memory_item_id=item.id,
                    source_kind="eval_import",
                    speaker="user",
                    observed_at=datetime(2023, 5, 29, 10, 0, tzinfo=UTC),
                    evidence_text="I got a 1/72 scale B-29 bomber kit.",
                ),
            ]
        )
        db.flush()

        class FakeResult:
            query_embedding: ClassVar[list[float]] = [0.1, 0.2]
            items: ClassVar[list] = [(item, 0.8)]

        async def fake_hybrid_search(db_arg, **kwargs):
            del db_arg, kwargs
            return FakeResult()

        monkeypatch.setattr(evidence_retrieval, "hybrid_search", fake_hybrid_search)

        result = await evidence_retrieval.retrieve_wide_evidence(
            db=db,
            user_id=user.id,
            query="How many model kits have I worked on or bought?",
            mode=RetrievalMode.AGGREGATE,
        )

    joined = "\n".join(text for _item_id, text, _score in result.semantic_results)
    assert len(result.semantic_results) == 2
    assert "Revell F-15" in joined
    assert "B-29 bomber" in joined


@pytest.mark.asyncio
async def test_retrieve_wide_evidence_preserves_distinct_sessions_for_aggregate(
    monkeypatch,
) -> None:
    monkeypatch.setattr(evidence_retrieval, "df", lambda user_id, value, **kwargs: value)
    monkeypatch.setattr(
        evidence_retrieval,
        "intent_for_mode",
        lambda mode: RetrievalIntent(
            mode=RetrievalMode.AGGREGATE,
            candidate_limit=20,
            max_evidence=3,
            min_distinct_sessions=3,
        ),
    )

    with _db_session() as db:
        user = User(username="aggregate-distinct", password_hash="x", display_name="Aggregate")
        db.add(user)
        db.flush()
        item = MemoryItem(
            user_id=user.id,
            content="Model kit sessions.",
            category="fact",
            importance=4,
        )
        db.add(item)
        db.flush()
        db.add_all(
            [
                MemoryItemEvidence(
                    user_id=user.id,
                    memory_item_id=item.id,
                    source_kind="eval_import",
                    speaker="user",
                    observed_at=datetime(2023, 5, 21, 10, 0, tzinfo=UTC),
                    evidence_text="I worked on model kit 1/72 1/48 1/35.",
                ),
                MemoryItemEvidence(
                    user_id=user.id,
                    memory_item_id=item.id,
                    source_kind="eval_import",
                    speaker="user",
                    observed_at=datetime(2023, 5, 21, 11, 0, tzinfo=UTC),
                    evidence_text="I worked on another model kit 1/144 1/700.",
                ),
                MemoryItemEvidence(
                    user_id=user.id,
                    memory_item_id=item.id,
                    source_kind="eval_import",
                    speaker="user",
                    observed_at=datetime(2023, 5, 21, 12, 0, tzinfo=UTC),
                    evidence_text="I bought model kit decals 1/32 1/24.",
                ),
                MemoryItemEvidence(
                    user_id=user.id,
                    memory_item_id=item.id,
                    source_kind="eval_import",
                    speaker="user",
                    observed_at=datetime(2023, 5, 22, 10, 0, tzinfo=UTC),
                    evidence_text="I bought a tank kit.",
                ),
                MemoryItemEvidence(
                    user_id=user.id,
                    memory_item_id=item.id,
                    source_kind="eval_import",
                    speaker="user",
                    observed_at=datetime(2023, 5, 23, 10, 0, tzinfo=UTC),
                    evidence_text="I started a bomber kit.",
                ),
            ]
        )
        db.flush()

        class FakeResult:
            query_embedding: ClassVar[list[float]] = [0.1, 0.2]
            items: ClassVar[list] = [(item, 0.8)]

        async def fake_hybrid_search(db_arg, **kwargs):
            del db_arg, kwargs
            return FakeResult()

        monkeypatch.setattr(evidence_retrieval, "hybrid_search", fake_hybrid_search)

        result = await evidence_retrieval.retrieve_wide_evidence(
            db=db,
            user_id=user.id,
            query="How many model kits have I worked on or bought?",
            mode=RetrievalMode.AGGREGATE,
        )

    joined = "\n".join(text for _item_id, text, _score in result.semantic_results)
    assert "2023-05-21" in joined
    assert "2023-05-22" in joined
    assert "2023-05-23" in joined


@pytest.mark.asyncio
async def test_retrieve_wide_evidence_orders_temporal_evidence_by_observed_at(
    monkeypatch,
) -> None:
    monkeypatch.setattr(evidence_retrieval, "df", lambda user_id, value, **kwargs: value)

    with _db_session() as db:
        user = User(username="temporal-evidence", password_hash="x", display_name="Temporal")
        db.add(user)
        db.flush()
        first = MemoryItem(
            user_id=user.id,
            content="First workshop event.",
            category="fact",
            importance=3,
        )
        second = MemoryItem(
            user_id=user.id,
            content="Second workshop event.",
            category="fact",
            importance=3,
        )
        db.add_all([first, second])
        db.flush()
        db.add_all(
            [
                MemoryItemEvidence(
                    user_id=user.id,
                    memory_item_id=second.id,
                    source_kind="llm_extraction",
                    speaker="user",
                    observed_at=datetime(2023, 6, 5, 15, 0, tzinfo=UTC),
                    evidence_text="I attended the second workshop.",
                ),
                MemoryItemEvidence(
                    user_id=user.id,
                    memory_item_id=first.id,
                    source_kind="llm_extraction",
                    speaker="user",
                    observed_at=datetime(2023, 6, 1, 9, 0, tzinfo=UTC),
                    evidence_text="I attended the first workshop.",
                ),
            ]
        )
        db.flush()

        class FakeResult:
            query_embedding: ClassVar[list[float]] = [0.1, 0.2]
            items: ClassVar[list] = [(second, 0.95), (first, 0.2)]

        async def fake_hybrid_search(db_arg, **kwargs):
            del db_arg, kwargs
            return FakeResult()

        monkeypatch.setattr(evidence_retrieval, "hybrid_search", fake_hybrid_search)

        result = await evidence_retrieval.retrieve_wide_evidence(
            db=db,
            user_id=user.id,
            query="Which workshop did I attend first?",
            mode=RetrievalMode.TEMPORAL,
        )

    assert result.semantic_results[0][0] == first.id
    assert "2023-06-01" in result.semantic_results[0][1]
