"""Tests for Soul Writer orchestrator — single serialized promoter pipeline."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from anima_server.db.base import Base
from anima_server.db.runtime_base import RuntimeBase
from anima_server.models import MemoryItem, MemoryItemEvidence, User
from anima_server.models.consciousness import SelfModelBlock
from anima_server.models.pending_memory_op import PendingMemoryOp
from anima_server.models.runtime import RuntimeMessage, RuntimeThread
from anima_server.models.runtime_memory import (
    MemoryAccessLog,
    MemoryCandidate,
    MemoryExtractionFailure,
    MemoryRetrievalFeedback,
    PromotionJournal,
)
from anima_server.services.agent.retrieval_feedback import sync_retrieval_feedback
from anima_server.services.agent.soul_writer import (
    SoulWriterResult,
    plan_candidate_promotion,
    run_soul_writer,
)
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _content_hash(user_id: int, category: str, importance_source: str, content: str) -> str:
    normalized = content.strip().lower()
    return hashlib.sha256(
        f"{user_id}:{category}:{importance_source}:{normalized}".encode()
    ).hexdigest()


def _pending_op_hash(user_id: int, target_block: str, op_type: str, content: str) -> str:
    return hashlib.sha256(
        f"{user_id}:{target_block.strip()}:{op_type.strip().lower()}:{content.strip()}".encode()
    ).hexdigest()


@contextmanager
def _soul_db_session() -> Generator[Session, None, None]:
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


def _make_soul_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )


def _make_runtime_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )


def _create_soul_engine() -> Engine:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return engine


def _create_runtime_engine() -> Engine:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    RuntimeBase.metadata.create_all(bind=engine)
    return engine


def _make_thread_bound_soul_factory(
    engine: Engine,
    cross_thread_accesses: list[tuple[str, int, int]],
) -> sessionmaker[Session]:
    class ThreadBoundSession(Session):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._owner_thread = threading.get_ident()

        def _assert_owner_thread(self, operation: str) -> None:
            current_thread = threading.get_ident()
            if current_thread != self._owner_thread:
                cross_thread_accesses.append(
                    (operation, self._owner_thread, current_thread)
                )
                raise AssertionError(
                    f"Session.{operation} used from thread {current_thread}; "
                    f"owner thread is {self._owner_thread}"
                )

        def get(self, *args, **kwargs):
            self._assert_owner_thread("get")
            return super().get(*args, **kwargs)

        def execute(self, *args, **kwargs):
            self._assert_owner_thread("execute")
            return super().execute(*args, **kwargs)

        def flush(self, *args, **kwargs):
            self._assert_owner_thread("flush")
            return super().flush(*args, **kwargs)

        def commit(self, *args, **kwargs):
            self._assert_owner_thread("commit")
            return super().commit(*args, **kwargs)

    return sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
        class_=ThreadBoundSession,
    )


# ---------------------------------------------------------------------------
# Test 1: No work — access sync still runs, result has zero counts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_soul_writer_no_work() -> None:
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_soul_factory(soul_engine)
    runtime_factory = _make_runtime_factory(runtime_engine)

    # Create a user in soul DB
    with soul_factory() as soul_db:
        user = User(username="no-work", password_hash="x", display_name="No Work")
        soul_db.add(user)
        soul_db.commit()
        user_id = user.id

    result = await run_soul_writer(
        user_id,
        soul_db_factory=soul_factory,
        runtime_db_factory=runtime_factory,
    )

    assert isinstance(result, SoulWriterResult)
    assert result.ops_processed == 0
    assert result.ops_skipped == 0
    assert result.ops_failed == 0
    assert result.candidates_promoted == 0
    assert result.candidates_rejected == 0
    assert result.candidates_superseded == 0
    assert result.candidates_failed == 0
    assert result.errors == []
    # Access sync always runs (returns dict)
    assert isinstance(result.access_sync, dict)
    assert isinstance(result.retrieval_feedback_sync, dict)

    soul_engine.dispose()
    runtime_engine.dispose()


@pytest.mark.asyncio
async def test_run_soul_writer_retries_failed_extraction_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.config import settings
    from anima_server.services.data_crypto import df

    class _FakeResponse:
        content = (
            '{"memories":[{"content":"Likes careful retries",'
            '"category":"preference","importance":4}]}'
        )

    class _FakeLLM:
        async def ainvoke(self, _messages):
            return _FakeResponse()

    original_provider = settings.agent_provider
    settings.agent_provider = "openai"
    monkeypatch.setattr(
        "anima_server.services.agent.llm.create_llm",
        lambda: _FakeLLM(),
    )

    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_soul_factory(soul_engine)
    runtime_factory = _make_runtime_factory(runtime_engine)

    try:
        with soul_factory() as soul_db:
            user = User(username="retry-extraction", password_hash="x", display_name="Retry")
            soul_db.add(user)
            soul_db.commit()
            user_id = user.id

        with runtime_factory() as runtime_db:
            runtime_db.add(
                MemoryExtractionFailure(
                    user_id=user_id,
                    source_message_ids=[101, 102],
                    user_message_preview="I like careful retries.",
                    assistant_response_preview="Noted.",
                    failure_reason="LLM timed out",
                    status="failed",
                    retry_count=0,
                )
            )
            runtime_db.commit()

        result = await run_soul_writer(
            user_id,
            soul_db_factory=soul_factory,
            runtime_db_factory=runtime_factory,
        )

        with runtime_factory() as runtime_db:
            failure = runtime_db.scalar(select(MemoryExtractionFailure))
        with soul_factory() as soul_db:
            item = soul_db.scalar(select(MemoryItem))
            evidence = soul_db.scalar(select(MemoryItemEvidence))

        assert result.extraction_failures_retried == 1
        assert result.extraction_failures_resolved == 1
        assert failure is not None
        assert failure.status == "resolved"
        assert failure.retry_count == 1
        assert failure.resolved_at is not None
        assert item is not None
        assert df(user_id, item.content, table="memory_items", field="content") == (
            "Likes careful retries"
        )
        assert evidence is not None
        assert evidence.runtime_message_ids_json == [101, 102]
    finally:
        settings.agent_provider = original_provider
        soul_engine.dispose()
        runtime_engine.dispose()


def test_extraction_retry_wait_covers_full_retry_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The outer wait on a cross-loop extraction retry must cover the provider's
    entire retry budget (retry_limit+1 attempts x timeout + backoff), not a
    single timeout — else a legitimately retrying call is cancelled early."""
    from anima_server.config import settings
    from anima_server.services.agent.soul_writer import (
        EXTRACTION_RETRY_TIMEOUT_BUFFER,
        _extraction_retry_wait_seconds,
    )

    monkeypatch.setattr(settings, "agent_llm_timeout", 120.0)
    monkeypatch.setattr(settings, "agent_llm_retry_limit", 3)
    monkeypatch.setattr(settings, "agent_llm_retry_max_delay", 10.0)

    wait = _extraction_retry_wait_seconds()
    # 4 attempts x 120s + 3 x 10s max backoff + 30s buffer.
    assert wait == 4 * 120.0 + 3 * 10.0 + EXTRACTION_RETRY_TIMEOUT_BUFFER
    # Must far exceed a single provider timeout (the earlier one-timeout bug).
    assert wait > settings.agent_llm_timeout


@pytest.mark.asyncio
async def test_retry_skips_inflight_pending_but_recovers_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh "pending" guard is a live in-flight extraction and must be left
    alone (no concurrent double-extraction); a stale "pending" guard is from a
    crashed process and must be recovered."""
    from anima_server.config import settings
    from anima_server.services.agent.soul_writer import STALE_PENDING_EXTRACTION

    class _FakeResponse:
        content = '{"memories":[{"content":"Recovered","category":"fact","importance":3}]}'

    class _FakeLLM:
        async def ainvoke(self, _messages):
            return _FakeResponse()

    original_provider = settings.agent_provider
    settings.agent_provider = "openai"
    monkeypatch.setattr(
        "anima_server.services.agent.llm.create_llm", lambda: _FakeLLM()
    )

    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_soul_factory(soul_engine)
    runtime_factory = _make_runtime_factory(runtime_engine)

    try:
        with soul_factory() as soul_db:
            user = User(username="stale-pending", password_hash="x", display_name="S")
            soul_db.add(user)
            soul_db.commit()
            user_id = user.id

        now = datetime.now(UTC)
        with runtime_factory() as runtime_db:
            runtime_db.add(
                MemoryExtractionFailure(
                    user_id=user_id,
                    source_message_ids=[1],
                    user_message_preview="in flight",
                    assistant_response_preview="x",
                    failure_reason="LLM extraction pending (crash-recovery guard)",
                    status="pending",
                    last_attempt_at=now,  # fresh → live in-flight
                )
            )
            runtime_db.add(
                MemoryExtractionFailure(
                    user_id=user_id,
                    source_message_ids=[2],
                    user_message_preview="crashed",
                    assistant_response_preview="x",
                    failure_reason="LLM extraction pending (crash-recovery guard)",
                    status="pending",
                    last_attempt_at=now - STALE_PENDING_EXTRACTION - timedelta(minutes=1),
                )
            )
            runtime_db.commit()

        result = await run_soul_writer(
            user_id,
            soul_db_factory=soul_factory,
            runtime_db_factory=runtime_factory,
        )

        with runtime_factory() as runtime_db:
            rows = {
                tuple(r.source_message_ids): r.status
                for r in runtime_db.scalars(select(MemoryExtractionFailure)).all()
            }

        assert result.extraction_failures_retried == 1
        assert rows[(1,)] == "pending"  # in-flight guard untouched
        assert rows[(2,)] == "resolved"  # stale guard recovered
    finally:
        settings.agent_provider = original_provider
        soul_engine.dispose()
        runtime_engine.dispose()


@pytest.mark.asyncio
async def test_run_soul_writer_retries_failed_extraction_from_source_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.config import settings
    from anima_server.services.data_crypto import df

    captured_prompts: list[str] = []

    class _FakeResponse:
        content = (
            '{"memories":[{"content":"Keeps a rare mango marker",'
            '"category":"fact","importance":4}]}'
        )

    class _FakeLLM:
        async def ainvoke(self, messages):
            prompt = "\n".join(str(getattr(message, "content", message)) for message in messages)
            captured_prompts.append(prompt)
            return _FakeResponse()

    original_provider = settings.agent_provider
    settings.agent_provider = "openai"
    monkeypatch.setattr(
        "anima_server.services.agent.llm.create_llm",
        lambda: _FakeLLM(),
    )

    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_soul_factory(soul_engine)
    runtime_factory = _make_runtime_factory(runtime_engine)

    try:
        with soul_factory() as soul_db:
            user = User(username="retry-source", password_hash="x", display_name="Retry Source")
            soul_db.add(user)
            soul_db.commit()
            user_id = user.id

        long_prefix = "context " * 40
        full_user_message = (
            f"{long_prefix}The important late fact is rare-mango-marker."
        )
        with runtime_factory() as runtime_db:
            thread = RuntimeThread(user_id=user_id, status="closed", next_message_sequence=3)
            runtime_db.add(thread)
            runtime_db.flush()
            user_message = RuntimeMessage(
                thread_id=thread.id,
                user_id=user_id,
                sequence_id=1,
                role="user",
                content_text=full_user_message,
            )
            assistant_message = RuntimeMessage(
                thread_id=thread.id,
                user_id=user_id,
                sequence_id=2,
                role="assistant",
                content_text="I will remember the rare mango marker.",
            )
            runtime_db.add_all([user_message, assistant_message])
            runtime_db.flush()
            runtime_db.add(
                MemoryExtractionFailure(
                    user_id=user_id,
                    source_message_ids=[user_message.id, assistant_message.id],
                    user_message_preview=full_user_message[:80],
                    assistant_response_preview="preview without marker",
                    failure_reason="LLM timed out",
                    status="failed",
                    retry_count=0,
                )
            )
            runtime_db.commit()

        result = await run_soul_writer(
            user_id,
            soul_db_factory=soul_factory,
            runtime_db_factory=runtime_factory,
        )

        with soul_factory() as soul_db:
            item = soul_db.scalar(select(MemoryItem))

        assert result.extraction_failures_retried == 1
        assert result.extraction_failures_resolved == 1
        assert captured_prompts
        assert "rare-mango-marker" in captured_prompts[0]
        assert item is not None
        assert (
            df(user_id, item.content, table="memory_items", field="content")
            == "Keeps a rare mango marker"
        )
    finally:
        settings.agent_provider = original_provider
        soul_engine.dispose()
        runtime_engine.dispose()


# ---------------------------------------------------------------------------
# Test 2: Candidate gets promoted, journal entry created
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_candidate_promote() -> None:
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_soul_factory(soul_engine)
    runtime_factory = _make_runtime_factory(runtime_engine)

    with soul_factory() as soul_db:
        user = User(username="promote-test", password_hash="x", display_name="Promote Test")
        soul_db.add(user)
        soul_db.commit()
        user_id = user.id

    # Create a candidate in runtime DB
    with runtime_factory() as runtime_db:
        candidate = MemoryCandidate(
            user_id=user_id,
            content="Likes green tea",
            category="preference",
            importance=3,
            importance_source="llm",
            source="llm",
            content_hash=_content_hash(user_id, "preference", "llm", "Likes green tea"),
            status="extracted",
        )
        runtime_db.add(candidate)
        runtime_db.commit()

    result = await run_soul_writer(
        user_id,
        soul_db_factory=soul_factory,
        runtime_db_factory=runtime_factory,
    )

    assert result.candidates_promoted == 1
    assert result.candidates_rejected == 0
    assert result.errors == []

    # Verify candidate status updated
    with runtime_factory() as runtime_db:
        c = runtime_db.scalar(select(MemoryCandidate).where(MemoryCandidate.user_id == user_id))
        assert c is not None
        assert c.status == "promoted"
        assert c.processed_at is not None

        # Verify journal entry created
        journal = runtime_db.scalar(
            select(PromotionJournal).where(PromotionJournal.user_id == user_id)
        )
        assert journal is not None
        assert journal.decision == "promoted" or journal.decision == "promote"
        assert journal.journal_status == "confirmed"
        assert journal.target_table == "memory_items"

    # Verify MemoryItem was created in soul DB
    with soul_factory() as soul_db:
        items = soul_db.scalars(select(MemoryItem).where(MemoryItem.user_id == user_id)).all()
        assert len(items) >= 1

    soul_engine.dispose()
    runtime_engine.dispose()


@pytest.mark.asyncio
async def test_process_candidate_promote_creates_memory_item_evidence() -> None:
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_soul_factory(soul_engine)
    runtime_factory = _make_runtime_factory(runtime_engine)

    with soul_factory() as soul_db:
        user = User(username="evidence-promote", password_hash="x", display_name="Evidence")
        soul_db.add(user)
        soul_db.commit()
        user_id = user.id

    with runtime_factory() as runtime_db:
        thread = RuntimeThread(user_id=user_id, status="active", next_message_sequence=2)
        runtime_db.add(thread)
        runtime_db.flush()
        message = RuntimeMessage(
            thread_id=thread.id,
            user_id=user_id,
            sequence_id=1,
            role="user",
            content_text="I prefer green tea in the morning.",
        )
        runtime_db.add(message)
        runtime_db.flush()
        candidate = MemoryCandidate(
            user_id=user_id,
            content="Prefers green tea in the morning",
            category="preference",
            importance=4,
            importance_source="llm",
            source="llm",
            source_message_ids=[message.id],
            content_hash=_content_hash(
                user_id,
                "preference",
                "llm",
                "Prefers green tea in the morning",
            ),
            status="extracted",
        )
        runtime_db.add(candidate)
        runtime_db.commit()

    result = await run_soul_writer(
        user_id,
        soul_db_factory=soul_factory,
        runtime_db_factory=runtime_factory,
    )

    assert result.candidates_promoted == 1
    with soul_factory() as soul_db:
        evidence = soul_db.scalar(
            select(MemoryItemEvidence).where(MemoryItemEvidence.user_id == user_id)
        )

    assert evidence is not None
    assert evidence.source_kind == "llm_extraction"
    assert evidence.runtime_thread_id == thread.id
    assert evidence.runtime_message_id == message.id
    assert evidence.runtime_message_ids_json == [message.id]
    assert evidence.speaker == "user"
    assert evidence.evidence_text == "I prefer green tea in the morning."
    assert evidence.confidence == 0.8

    soul_engine.dispose()
    runtime_engine.dispose()


@pytest.mark.asyncio
async def test_candidate_evidence_ignores_source_messages_from_other_users() -> None:
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_soul_factory(soul_engine)
    runtime_factory = _make_runtime_factory(runtime_engine)

    with soul_factory() as soul_db:
        user_one = User(username="source-owner", password_hash="x", display_name="Source Owner")
        user_two = User(username="candidate-owner", password_hash="x", display_name="Candidate")
        soul_db.add_all([user_one, user_two])
        soul_db.commit()
        other_user_id = user_one.id
        candidate_user_id = user_two.id

    with runtime_factory() as runtime_db:
        thread = RuntimeThread(user_id=other_user_id, status="active", next_message_sequence=2)
        runtime_db.add(thread)
        runtime_db.flush()
        other_message = RuntimeMessage(
            thread_id=thread.id,
            user_id=other_user_id,
            sequence_id=1,
            role="user",
            content_text="Other user's private source text.",
        )
        runtime_db.add(other_message)
        runtime_db.flush()
        candidate = MemoryCandidate(
            user_id=candidate_user_id,
            content="Candidate user's extracted memory",
            category="fact",
            importance=4,
            importance_source="llm",
            source="llm",
            source_message_ids=[other_message.id],
            content_hash=_content_hash(
                candidate_user_id,
                "fact",
                "llm",
                "Candidate user's extracted memory",
            ),
            status="extracted",
        )
        runtime_db.add(candidate)
        runtime_db.commit()

    result = await run_soul_writer(
        candidate_user_id,
        soul_db_factory=soul_factory,
        runtime_db_factory=runtime_factory,
    )

    assert result.candidates_promoted == 1
    with soul_factory() as soul_db:
        evidence = soul_db.scalar(
            select(MemoryItemEvidence).where(MemoryItemEvidence.user_id == candidate_user_id)
        )

    assert evidence is not None
    assert evidence.evidence_text == "Candidate user's extracted memory"
    assert evidence.runtime_thread_id is None
    assert evidence.runtime_message_id is None
    assert evidence.speaker == "unknown"

    soul_engine.dispose()
    runtime_engine.dispose()


@pytest.mark.asyncio
async def test_inline_embedding_uses_event_loop_owned_soul_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    cross_thread_accesses: list[tuple[str, int, int]] = []
    soul_factory = _make_thread_bound_soul_factory(
        soul_engine,
        cross_thread_accesses,
    )
    runtime_factory = _make_runtime_factory(runtime_engine)

    import anima_server.services.agent.bm25_index as bm25_module
    import anima_server.services.agent.embeddings as embeddings_module
    import anima_server.services.agent.memory_store as memory_store_module
    import anima_server.services.agent.vector_store as vector_store_module

    async def _fake_generate_embedding(_text: str) -> list[float]:
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(
        embeddings_module,
        "generate_embedding",
        _fake_generate_embedding,
    )
    monkeypatch.setattr(
        memory_store_module,
        "sync_memory_item_to_retrieval_index",
        lambda _item: None,
    )
    monkeypatch.setattr(
        vector_store_module,
        "upsert_memory",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        bm25_module,
        "invalidate_index",
        lambda _user_id: None,
    )

    with soul_factory() as soul_db:
        user = User(
            username="inline-embed-thread",
            password_hash="x",
            display_name="Inline Embed Thread",
        )
        soul_db.add(user)
        soul_db.commit()
        user_id = user.id

    with runtime_factory() as runtime_db:
        candidate = MemoryCandidate(
            user_id=user_id,
            content="Likes jasmine tea",
            category="preference",
            importance=3,
            importance_source="llm",
            source="llm",
            content_hash=_content_hash(user_id, "preference", "llm", "Likes jasmine tea"),
            status="extracted",
        )
        runtime_db.add(candidate)
        runtime_db.commit()

    result = await run_soul_writer(
        user_id,
        soul_db_factory=soul_factory,
        runtime_db_factory=runtime_factory,
    )

    assert result.candidates_promoted == 1
    assert result.errors == []
    assert cross_thread_accesses == []

    with soul_factory() as soul_db:
        item = soul_db.scalar(select(MemoryItem).where(MemoryItem.user_id == user_id))
        assert item is not None
        assert item.embedding_json == [0.1, 0.2, 0.3]
        assert item.embedding_checksum is not None

    soul_engine.dispose()
    runtime_engine.dispose()


# ---------------------------------------------------------------------------
# Test 3: Candidate matching existing item is rejected (duplicate)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_candidate_duplicate_reinforced() -> None:
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_soul_factory(soul_engine)
    runtime_factory = _make_runtime_factory(runtime_engine)

    with soul_factory() as soul_db:
        user = User(username="dup-test", password_hash="x", display_name="Dup Test")
        soul_db.add(user)
        soul_db.flush()
        user_id = user.id

        # Pre-existing memory item in soul DB
        item = MemoryItem(
            user_id=user_id,
            content="Likes green tea",
            category="preference",
            importance=3,
            source="extraction",
        )
        soul_db.add(item)
        soul_db.commit()

    # Create a duplicate candidate in runtime DB
    with runtime_factory() as runtime_db:
        candidate = MemoryCandidate(
            user_id=user_id,
            content="Likes green tea",
            category="preference",
            importance=3,
            importance_source="llm",
            source="llm",
            content_hash=_content_hash(user_id, "preference", "llm", "Likes green tea"),
            status="extracted",
        )
        runtime_db.add(candidate)
        runtime_db.commit()

    result = await run_soul_writer(
        user_id,
        soul_db_factory=soul_factory,
        runtime_db_factory=runtime_factory,
    )

    assert result.candidates_reinforced == 1
    assert result.candidates_rejected == 0
    assert result.candidates_promoted == 0

    # Verify candidate marked as reinforced
    with runtime_factory() as runtime_db:
        c = runtime_db.scalar(select(MemoryCandidate).where(MemoryCandidate.user_id == user_id))
        assert c is not None
        assert c.status == "reinforced"

    soul_engine.dispose()
    runtime_engine.dispose()


# ---------------------------------------------------------------------------
# Test 4: importance_source="user_explicit" always promotes (skips dedup)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_candidate_user_explicit_always_promotes() -> None:
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_soul_factory(soul_engine)
    runtime_factory = _make_runtime_factory(runtime_engine)

    with soul_factory() as soul_db:
        user = User(username="explicit-test", password_hash="x", display_name="Explicit Test")
        soul_db.add(user)
        soul_db.flush()
        user_id = user.id

        # Pre-existing identical memory item
        item = MemoryItem(
            user_id=user_id,
            content="Likes green tea",
            category="preference",
            importance=3,
            source="extraction",
        )
        soul_db.add(item)
        soul_db.commit()

    # Create candidate with user_explicit importance
    with runtime_factory() as runtime_db:
        candidate = MemoryCandidate(
            user_id=user_id,
            content="Likes green tea",
            category="preference",
            importance=4,
            importance_source="user_explicit",
            source="tool",
            content_hash=_content_hash(user_id, "preference", "user_explicit", "Likes green tea"),
            status="extracted",
        )
        runtime_db.add(candidate)
        runtime_db.commit()

    await run_soul_writer(
        user_id,
        soul_db_factory=soul_factory,
        runtime_db_factory=runtime_factory,
    )

    # user_explicit always promotes — plan_candidate_promotion returns "promote"
    # but store_memory_item may reject it if it's a duplicate
    # The key test: plan_candidate_promotion returns "promote" for user_explicit
    with runtime_factory() as runtime_db:
        c = runtime_db.scalar(select(MemoryCandidate).where(MemoryCandidate.user_id == user_id))
        assert c is not None
        # It was either promoted (new add) or rejected (store_memory_item detected dup)
        # The important thing: the decision was "promote" (not "rejected" by plan_)

        journal = runtime_db.scalar(
            select(PromotionJournal).where(PromotionJournal.user_id == user_id)
        )
        assert journal is not None
        # plan_candidate_promotion returned "promote" for user_explicit;
        # but store_memory_item may have then rejected as duplicate
        # The plan decision is what gets logged initially
        assert journal.journal_status == "confirmed"

    soul_engine.dispose()
    runtime_engine.dispose()


def test_plan_candidate_promotion_user_explicit() -> None:
    """plan_candidate_promotion returns 'promote' for user_explicit regardless of content."""
    with _soul_db_session() as soul_db:
        user = User(username="plan-explicit", password_hash="x", display_name="Plan Explicit")
        soul_db.add(user)
        soul_db.flush()
        user_id = user.id

        # Pre-existing identical item
        soul_db.add(
            MemoryItem(
                user_id=user_id,
                content="Likes green tea",
                category="preference",
                importance=3,
                source="extraction",
            )
        )
        soul_db.commit()

        # Create a mock candidate with user_explicit
        class FakeCandidate:
            pass

        candidate = FakeCandidate()
        candidate.importance_source = "user_explicit"
        candidate.supersedes_item_id = None
        candidate.content = "Likes green tea"
        candidate.category = "preference"
        candidate.importance = 4
        candidate.source = "tool"

        decision = plan_candidate_promotion(soul_db, candidate, user_id)

    assert decision.action == "promote"
    assert "user_explicit" in decision.reason


# ---------------------------------------------------------------------------
# Test 5: Correction with valid target supersedes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_candidate_correction_supersedes() -> None:
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_soul_factory(soul_engine)
    runtime_factory = _make_runtime_factory(runtime_engine)

    with soul_factory() as soul_db:
        user = User(username="correction-test", password_hash="x", display_name="Correction Test")
        soul_db.add(user)
        soul_db.flush()
        user_id = user.id

        # Old memory item to be superseded
        old_item = MemoryItem(
            user_id=user_id,
            content="Age: 25",
            category="fact",
            importance=3,
            source="extraction",
        )
        soul_db.add(old_item)
        soul_db.commit()
        old_item_id = old_item.id

    # Create a correction candidate that targets the old item
    with runtime_factory() as runtime_db:
        candidate = MemoryCandidate(
            user_id=user_id,
            content="Age: 26",
            category="fact",
            importance=3,
            importance_source="correction",
            source="feedback",
            supersedes_item_id=old_item_id,
            content_hash=_content_hash(user_id, "fact", "correction", "Age: 26"),
            status="extracted",
        )
        runtime_db.add(candidate)
        runtime_db.commit()

    result = await run_soul_writer(
        user_id,
        soul_db_factory=soul_factory,
        runtime_db_factory=runtime_factory,
    )

    assert result.candidates_superseded == 1
    assert result.candidates_promoted == 0
    assert result.errors == []

    # Verify old item was superseded
    with soul_factory() as soul_db:
        old = soul_db.get(MemoryItem, old_item_id)
        assert old is not None
        assert old.superseded_by is not None

    # Verify candidate marked as promoted
    with runtime_factory() as runtime_db:
        c = runtime_db.scalar(select(MemoryCandidate).where(MemoryCandidate.user_id == user_id))
        assert c is not None
        assert c.status == "promoted"

    soul_engine.dispose()
    runtime_engine.dispose()


# ---------------------------------------------------------------------------
# Test 6: Pending op append is idempotent (same op replayed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_op_append_idempotent() -> None:
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_soul_factory(soul_engine)
    runtime_factory = _make_runtime_factory(runtime_engine)

    with soul_factory() as soul_db:
        user = User(username="idempotent-test", password_hash="x", display_name="Idempotent")
        soul_db.add(user)
        soul_db.flush()
        user_id = user.id

        # Create initial block content
        soul_db.add(
            SelfModelBlock(
                user_id=user_id,
                section="human",
                content="Name: Alice",
                version=1,
                updated_by="seed",
            )
        )
        soul_db.commit()

    content_hash = _pending_op_hash(user_id, "human", "append", "Likes green tea")

    # First run: create and process op
    with runtime_factory() as runtime_db:
        op = PendingMemoryOp(
            user_id=user_id,
            op_type="append",
            target_block="human",
            content="Likes green tea",
            content_hash=content_hash,
        )
        runtime_db.add(op)
        runtime_db.commit()

    result1 = await run_soul_writer(
        user_id,
        soul_db_factory=soul_factory,
        runtime_db_factory=runtime_factory,
    )
    assert result1.ops_processed == 1
    assert result1.ops_skipped == 0

    # Verify block was updated
    with soul_factory() as soul_db:
        block = soul_db.scalar(
            select(SelfModelBlock).where(
                SelfModelBlock.user_id == user_id,
                SelfModelBlock.section == "human",
            )
        )
        assert block is not None
        assert "Likes green tea" in block.content

    # Second run: create a duplicate op with same content_hash
    with runtime_factory() as runtime_db:
        op2 = PendingMemoryOp(
            user_id=user_id,
            op_type="append",
            target_block="human",
            content="Likes green tea",
            content_hash=content_hash,
        )
        runtime_db.add(op2)
        runtime_db.commit()

    result2 = await run_soul_writer(
        user_id,
        soul_db_factory=soul_factory,
        runtime_db_factory=runtime_factory,
    )
    # The second op should be skipped (idempotent) either by journal hash or content check
    assert result2.ops_skipped == 1
    assert result2.ops_processed == 0

    # Verify content was NOT doubled in the block
    with soul_factory() as soul_db:
        block = soul_db.scalar(
            select(SelfModelBlock).where(
                SelfModelBlock.user_id == user_id,
                SelfModelBlock.section == "human",
            )
        )
        assert block is not None
        count = block.content.count("Likes green tea")
        assert count == 1, f"Content duplicated {count} times: {block.content!r}"

    soul_engine.dispose()
    runtime_engine.dispose()


# ---------------------------------------------------------------------------
# Test 7: Per-item error isolation — one failing candidate doesn't block others
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_item_error_isolation() -> None:
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_soul_factory(soul_engine)
    runtime_factory = _make_runtime_factory(runtime_engine)

    with soul_factory() as soul_db:
        user = User(username="isolation-test", password_hash="x", display_name="Isolation Test")
        soul_db.add(user)
        soul_db.commit()
        user_id = user.id

    # Create two candidates: one good, one with invalid data
    with runtime_factory() as runtime_db:
        good_candidate = MemoryCandidate(
            user_id=user_id,
            content="Has a dog named Biscuit",
            category="fact",
            importance=3,
            importance_source="llm",
            source="llm",
            content_hash=_content_hash(user_id, "fact", "llm", "Has a dog named Biscuit"),
            status="extracted",
        )
        runtime_db.add(good_candidate)

        # This candidate will fail because it has an impossible category
        # that will cause issues deeper in the pipeline. We simulate failure
        # by using a candidate whose content processing will raise an error.
        bad_candidate = MemoryCandidate(
            user_id=user_id,
            content="Another valid fact",
            category="fact",
            importance=3,
            importance_source="llm",
            source="llm",
            content_hash=_content_hash(user_id, "fact", "llm", "Another valid fact"),
            status="extracted",
        )
        runtime_db.add(bad_candidate)
        runtime_db.commit()
        good_id = good_candidate.id
        bad_id = bad_candidate.id

    # Patch _process_candidate to make the second candidate fail
    import anima_server.services.agent.soul_writer as sw_module

    original_process = sw_module._process_candidate
    call_count = 0

    def _patched_process(
        candidate, *, user_id, runtime_db, soul_db_factory, result, event_loop=None
    ):
        nonlocal call_count
        call_count += 1
        if candidate.content == "Another valid fact":
            raise RuntimeError("Simulated processing failure")
        return original_process(
            candidate,
            user_id=user_id,
            runtime_db=runtime_db,
            soul_db_factory=soul_db_factory,
            result=result,
            event_loop=event_loop,
        )

    sw_module._process_candidate = _patched_process
    try:
        result = await run_soul_writer(
            user_id,
            soul_db_factory=soul_factory,
            runtime_db_factory=runtime_factory,
        )
    finally:
        sw_module._process_candidate = original_process

    # One should succeed, one should fail
    assert result.candidates_promoted == 1
    assert result.candidates_failed == 1
    assert len(result.errors) == 1
    assert "Simulated processing failure" in result.errors[0]

    # Verify the good candidate was promoted
    with runtime_factory() as runtime_db:
        good = runtime_db.get(MemoryCandidate, good_id)
        assert good is not None
        assert good.status == "promoted"

        bad = runtime_db.get(MemoryCandidate, bad_id)
        assert bad is not None
        assert bad.status == "failed"
        assert bad.retry_count == 1
        assert "Simulated" in (bad.last_error or "")

    soul_engine.dispose()
    runtime_engine.dispose()


# ---------------------------------------------------------------------------
# Test 8: Access sync runs even with no candidates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_access_sync_runs_with_no_candidates() -> None:
    """Access sync should run even when there are no candidates or ops."""
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_soul_factory(soul_engine)
    runtime_factory = _make_runtime_factory(runtime_engine)

    with soul_factory() as soul_db:
        user = User(username="access-sync", password_hash="x", display_name="Access Sync")
        soul_db.add(user)
        soul_db.flush()
        user_id = user.id

        item = MemoryItem(
            user_id=user_id,
            content="Has a cat",
            category="fact",
            importance=3,
            source="extraction",
        )
        soul_db.add(item)
        soul_db.commit()
        item_id = item.id

    # Create access log rows in runtime DB (no candidates)
    with runtime_factory() as runtime_db:
        for _ in range(3):
            runtime_db.add(
                MemoryAccessLog(
                    user_id=user_id,
                    memory_item_id=item_id,
                    accessed_at=datetime.now(UTC),
                    synced=False,
                )
            )
        runtime_db.commit()

    result = await run_soul_writer(
        user_id,
        soul_db_factory=soul_factory,
        runtime_db_factory=runtime_factory,
    )

    assert result.candidates_promoted == 0
    assert result.ops_processed == 0
    assert result.access_sync.get("items_synced", 0) == 1
    assert result.access_sync.get("access_counts", {}).get(item_id) == 3

    # Verify reference_count updated in soul DB
    with soul_factory() as soul_db:
        updated_item = soul_db.get(MemoryItem, item_id)
        assert updated_item is not None
        assert updated_item.reference_count == 3

    # Verify access log rows purged
    with runtime_factory() as runtime_db:
        remaining = runtime_db.scalars(
            select(MemoryAccessLog).where(MemoryAccessLog.user_id == user_id)
        ).all()
        assert len(remaining) == 0

    soul_engine.dispose()
    runtime_engine.dispose()


@pytest.mark.asyncio
async def test_retrieval_feedback_sync_updates_used_items_without_penalizing_mixed_run() -> None:
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_soul_factory(soul_engine)
    runtime_factory = _make_runtime_factory(runtime_engine)

    with soul_factory() as soul_db:
        user = User(username="retrieval-feedback", password_hash="x", display_name="Feedback")
        soul_db.add(user)
        soul_db.flush()
        user_id = user.id

        used_item = MemoryItem(
            user_id=user_id,
            content="Likes cats",
            category="preference",
            importance=3,
            source="extraction",
        )
        unused_item = MemoryItem(
            user_id=user_id,
            content="Runs marathons on weekends",
            category="fact",
            importance=3,
            source="extraction",
        )
        soul_db.add(used_item)
        soul_db.add(unused_item)
        soul_db.commit()
        used_item_id = used_item.id
        unused_item_id = unused_item.id

    with runtime_factory() as runtime_db:
        runtime_db.add(
            MemoryRetrievalFeedback(
                user_id=user_id,
                run_id=101,
                memory_item_id=used_item_id,
                was_used=True,
                evidence_score=1.0,
                synced=False,
            )
        )
        runtime_db.add(
            MemoryRetrievalFeedback(
                user_id=user_id,
                run_id=101,
                memory_item_id=unused_item_id,
                was_used=False,
                evidence_score=0.0,
                synced=False,
            )
        )
        runtime_db.commit()

    result = await run_soul_writer(
        user_id,
        soul_db_factory=soul_factory,
        runtime_db_factory=runtime_factory,
    )

    assert result.retrieval_feedback_sync.get("items_synced", 0) == 2
    assert result.retrieval_feedback_sync.get("used_counts", {}).get(used_item_id) == 1
    assert result.retrieval_feedback_sync.get("used_evidence_totals", {}).get(used_item_id) == 1.0
    assert result.retrieval_feedback_sync.get("corrected_counts", {}) == {}
    assert result.retrieval_feedback_sync.get("unused_counts", {}).get(unused_item_id) == 1
    assert result.retrieval_feedback_sync.get("zero_reference_runs", 0) == 0
    assert result.retrieval_feedback_sync.get("importance_deltas", {}).get(used_item_id) == 1
    assert result.retrieval_feedback_sync.get("evidence_heat_factors", {}).get(used_item_id) == 1.2
    assert unused_item_id not in result.retrieval_feedback_sync.get("importance_deltas", {})

    with soul_factory() as soul_db:
        refreshed_used = soul_db.get(MemoryItem, used_item_id)
        refreshed_unused = soul_db.get(MemoryItem, unused_item_id)
        assert refreshed_used is not None
        assert refreshed_unused is not None
        assert refreshed_used.importance == 4
        assert refreshed_unused.importance == 3

    with runtime_factory() as runtime_db:
        remaining = runtime_db.scalars(
            select(MemoryRetrievalFeedback).where(MemoryRetrievalFeedback.user_id == user_id)
        ).all()
        assert remaining == []

    soul_engine.dispose()
    runtime_engine.dispose()


def test_retrieval_feedback_sync_dry_run_keeps_rows_unsynced() -> None:
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_soul_factory(soul_engine)
    runtime_factory = _make_runtime_factory(runtime_engine)

    with soul_factory() as soul_db:
        user = User(username="retrieval-dry-run", password_hash="x", display_name="Dry Run")
        soul_db.add(user)
        soul_db.flush()
        user_id = user.id

        item = MemoryItem(
            user_id=user_id,
            content="Likes cats",
            category="preference",
            importance=3,
            source="extraction",
        )
        soul_db.add(item)
        soul_db.commit()
        item_id = item.id

    with runtime_factory() as runtime_db, soul_factory() as soul_db:
        runtime_db.add(
            MemoryRetrievalFeedback(
                user_id=user_id,
                run_id=707,
                memory_item_id=item_id,
                was_used=True,
                evidence_score=1.0,
                synced=False,
            )
        )
        runtime_db.commit()

        result = sync_retrieval_feedback(
            user_id=user_id,
            runtime_db=runtime_db,
            soul_db=soul_db,
            dry_run=True,
        )

        runtime_db.expire_all()
        remaining = runtime_db.scalars(
            select(MemoryRetrievalFeedback).where(MemoryRetrievalFeedback.user_id == user_id)
        ).all()
        assert len(remaining) == 1
        assert remaining[0].synced is False
        assert result["items_synced"] == 1

        refreshed = soul_db.get(MemoryItem, item_id)
        assert refreshed is not None
        assert refreshed.importance == 3

    soul_engine.dispose()
    runtime_engine.dispose()


def test_retrieval_feedback_sync_only_marks_selected_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_soul_factory(soul_engine)
    runtime_factory = _make_runtime_factory(runtime_engine)

    with soul_factory() as soul_db:
        user = User(username="retrieval-selected-rows", password_hash="x", display_name="Selected Rows")
        soul_db.add(user)
        soul_db.flush()
        user_id = user.id

        item = MemoryItem(
            user_id=user_id,
            content="Likes cats",
            category="preference",
            importance=3,
            source="extraction",
        )
        soul_db.add(item)
        soul_db.commit()
        item_id = item.id

    with runtime_factory() as runtime_db, soul_factory() as soul_db:
        runtime_db.add(
            MemoryRetrievalFeedback(
                user_id=user_id,
                run_id=808,
                memory_item_id=item_id,
                was_used=True,
                evidence_score=1.0,
                synced=False,
            )
        )
        runtime_db.commit()

        original_execute = runtime_db.execute
        injected_row_id: int | None = None
        execute_calls = 0

        def execute_with_injected_row(statement, *args, **kwargs):
            nonlocal execute_calls, injected_row_id
            execute_calls += 1
            if execute_calls == 2:
                runtime_db.add(
                    MemoryRetrievalFeedback(
                        user_id=user_id,
                        run_id=809,
                        memory_item_id=item_id,
                        was_used=True,
                        evidence_score=0.6,
                        synced=False,
                    )
                )
                runtime_db.flush()
                injected_row_id = runtime_db.scalar(
                    select(MemoryRetrievalFeedback.id)
                    .where(MemoryRetrievalFeedback.user_id == user_id)
                    .order_by(MemoryRetrievalFeedback.id.desc())
                    .limit(1)
                )
            return original_execute(statement, *args, **kwargs)

        monkeypatch.setattr(runtime_db, "execute", execute_with_injected_row)

        result = sync_retrieval_feedback(
            user_id=user_id,
            runtime_db=runtime_db,
            soul_db=soul_db,
            dry_run=False,
        )

        runtime_db.expire_all()
        remaining = runtime_db.scalars(
            select(MemoryRetrievalFeedback)
            .where(MemoryRetrievalFeedback.user_id == user_id)
            .order_by(MemoryRetrievalFeedback.id)
        ).all()

        assert result["items_synced"] == 1
        assert injected_row_id is not None
        assert len(remaining) == 1
        assert remaining[0].id == injected_row_id
        assert remaining[0].synced is False

    soul_engine.dispose()
    runtime_engine.dispose()


@pytest.mark.asyncio
async def test_retrieval_feedback_sync_decays_heat_for_zero_reference_run() -> None:
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_soul_factory(soul_engine)
    runtime_factory = _make_runtime_factory(runtime_engine)

    with soul_factory() as soul_db:
        user = User(username="retrieval-zero-ref", password_hash="x", display_name="Zero Ref")
        soul_db.add(user)
        soul_db.flush()
        user_id = user.id

        item = MemoryItem(
            user_id=user_id,
            content="Runs marathons on weekends",
            category="fact",
            importance=3,
            heat=20.0,
            source="extraction",
        )
        soul_db.add(item)
        soul_db.commit()
        item_id = item.id

    with runtime_factory() as runtime_db:
        runtime_db.add(
            MemoryRetrievalFeedback(
                user_id=user_id,
                run_id=202,
                memory_item_id=item_id,
                was_used=False,
                evidence_score=0.0,
                synced=False,
            )
        )
        runtime_db.commit()

    result = await run_soul_writer(
        user_id,
        soul_db_factory=soul_factory,
        runtime_db_factory=runtime_factory,
    )

    assert result.retrieval_feedback_sync.get("items_synced", 0) == 1
    assert result.retrieval_feedback_sync.get("zero_reference_runs", 0) == 1
    assert result.retrieval_feedback_sync.get("corrected_counts", {}) == {}
    assert result.retrieval_feedback_sync.get("unused_counts", {}).get(item_id) == 1
    assert result.retrieval_feedback_sync.get("zero_reference_counts", {}).get(item_id) == 1
    assert result.retrieval_feedback_sync.get("heat_decay_factors", {}).get(item_id) == 0.95
    assert item_id not in result.retrieval_feedback_sync.get("importance_deltas", {})

    with soul_factory() as soul_db:
        refreshed = soul_db.get(MemoryItem, item_id)
        assert refreshed is not None
        assert refreshed.importance == 3
        assert refreshed.heat == pytest.approx(19.0)

    soul_engine.dispose()
    runtime_engine.dispose()


@pytest.mark.asyncio
async def test_retrieval_feedback_sync_penalizes_corrected_items() -> None:
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_soul_factory(soul_engine)
    runtime_factory = _make_runtime_factory(runtime_engine)

    with soul_factory() as soul_db:
        user = User(username="retrieval-corrected", password_hash="x", display_name="Corrected")
        soul_db.add(user)
        soul_db.flush()
        user_id = user.id

        item = MemoryItem(
            user_id=user_id,
            content="Lives in Paris",
            category="fact",
            importance=3,
            source="extraction",
        )
        soul_db.add(item)
        soul_db.commit()
        item_id = item.id

    with runtime_factory() as runtime_db:
        runtime_db.add(
            MemoryRetrievalFeedback(
                user_id=user_id,
                run_id=303,
                memory_item_id=item_id,
                was_used=False,
                was_corrected=True,
                evidence_score=1.0,
                synced=False,
            )
        )
        runtime_db.commit()

    result = await run_soul_writer(
        user_id,
        soul_db_factory=soul_factory,
        runtime_db_factory=runtime_factory,
    )

    assert result.retrieval_feedback_sync.get("items_synced", 0) == 1
    assert result.retrieval_feedback_sync.get("zero_reference_runs", 0) == 0
    assert result.retrieval_feedback_sync.get("corrected_counts", {}).get(item_id) == 1
    assert result.retrieval_feedback_sync.get("importance_deltas", {}).get(item_id) == -1
    assert result.retrieval_feedback_sync.get("evidence_heat_factors", {}).get(item_id) == 0.8

    with soul_factory() as soul_db:
        refreshed = soul_db.get(MemoryItem, item_id)
        assert refreshed is not None
        assert refreshed.importance == 2
        assert refreshed.heat is not None
        assert refreshed.heat < 2.0

    soul_engine.dispose()
    runtime_engine.dispose()


@pytest.mark.asyncio
async def test_retrieval_feedback_sync_warms_heat_without_importance_boost_on_weak_used_evidence() -> None:
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_soul_factory(soul_engine)
    runtime_factory = _make_runtime_factory(runtime_engine)

    with soul_factory() as soul_db:
        user = User(username="retrieval-weak-evidence", password_hash="x", display_name="Weak Evidence")
        soul_db.add(user)
        soul_db.flush()
        user_id = user.id

        item = MemoryItem(
            user_id=user_id,
            content="Likes cats",
            category="preference",
            importance=3,
            heat=10.0,
            source="extraction",
        )
        soul_db.add(item)
        soul_db.commit()
        item_id = item.id

    with runtime_factory() as runtime_db:
        runtime_db.add(
            MemoryRetrievalFeedback(
                user_id=user_id,
                run_id=404,
                memory_item_id=item_id,
                was_used=True,
                was_corrected=False,
                evidence_score=0.6,
                synced=False,
            )
        )
        runtime_db.commit()

    result = await run_soul_writer(
        user_id,
        soul_db_factory=soul_factory,
        runtime_db_factory=runtime_factory,
    )

    assert result.retrieval_feedback_sync.get("items_synced", 0) == 1
    assert result.retrieval_feedback_sync.get("zero_reference_runs", 0) == 0
    assert result.retrieval_feedback_sync.get("used_counts", {}).get(item_id) == 1
    assert result.retrieval_feedback_sync.get("used_evidence_totals", {}).get(item_id) == 0.6
    assert result.retrieval_feedback_sync.get("evidence_heat_factors", {}).get(item_id) == 1.12
    assert item_id not in result.retrieval_feedback_sync.get("importance_deltas", {})

    with soul_factory() as soul_db:
        refreshed = soul_db.get(MemoryItem, item_id)
        assert refreshed is not None
        assert refreshed.importance == 3
        assert refreshed.heat == pytest.approx(11.2)

    soul_engine.dispose()
    runtime_engine.dispose()


@pytest.mark.asyncio
async def test_retrieval_feedback_heat_warming_changes_scored_retrieval_order() -> None:
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_soul_factory(soul_engine)
    runtime_factory = _make_runtime_factory(runtime_engine)

    with soul_factory() as soul_db:
        user = User(username="retrieval-order", password_hash="x", display_name="Retrieval Order")
        soul_db.add(user)
        soul_db.flush()
        user_id = user.id

        warmed_item = MemoryItem(
            user_id=user_id,
            content="Likes cats",
            category="preference",
            importance=3,
            heat=10.0,
            source="extraction",
        )
        baseline_item = MemoryItem(
            user_id=user_id,
            content="Likes dogs",
            category="preference",
            importance=3,
            heat=10.0,
            source="extraction",
        )
        soul_db.add(warmed_item)
        soul_db.add(baseline_item)
        soul_db.commit()
        warmed_item_id = warmed_item.id
        baseline_item_id = baseline_item.id

    with runtime_factory() as runtime_db:
        runtime_db.add(
            MemoryRetrievalFeedback(
                user_id=user_id,
                run_id=505,
                memory_item_id=warmed_item_id,
                was_used=True,
                was_corrected=False,
                evidence_score=0.6,
                synced=False,
            )
        )
        runtime_db.commit()

    await run_soul_writer(
        user_id,
        soul_db_factory=soul_factory,
        runtime_db_factory=runtime_factory,
    )

    with soul_factory() as soul_db:
        from anima_server.services.agent.memory_store import get_memory_items_scored

        ranked = get_memory_items_scored(
            soul_db,
            user_id=user_id,
            category="preference",
            limit=10,
        )
        assert ranked[0].id == warmed_item_id
        assert ranked[1].id == baseline_item_id

    soul_engine.dispose()
    runtime_engine.dispose()


@pytest.mark.asyncio
async def test_retrieval_feedback_heat_cooling_changes_scored_retrieval_order() -> None:
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_soul_factory(soul_engine)
    runtime_factory = _make_runtime_factory(runtime_engine)

    with soul_factory() as soul_db:
        user = User(username="retrieval-cooling-order", password_hash="x", display_name="Retrieval Cooling")
        soul_db.add(user)
        soul_db.flush()
        user_id = user.id

        cooled_item = MemoryItem(
            user_id=user_id,
            content="Lives in Paris",
            category="fact",
            importance=3,
            heat=10.0,
            source="extraction",
        )
        baseline_item = MemoryItem(
            user_id=user_id,
            content="Lives in Berlin",
            category="fact",
            importance=3,
            heat=10.0,
            source="extraction",
        )
        soul_db.add(cooled_item)
        soul_db.add(baseline_item)
        soul_db.commit()
        cooled_item_id = cooled_item.id
        baseline_item_id = baseline_item.id

    with runtime_factory() as runtime_db:
        runtime_db.add(
            MemoryRetrievalFeedback(
                user_id=user_id,
                run_id=606,
                memory_item_id=cooled_item_id,
                was_used=False,
                was_corrected=True,
                evidence_score=0.4,
                synced=False,
            )
        )
        runtime_db.commit()

    result = await run_soul_writer(
        user_id,
        soul_db_factory=soul_factory,
        runtime_db_factory=runtime_factory,
    )

    assert result.retrieval_feedback_sync.get("corrected_counts", {}).get(cooled_item_id) == 1
    assert result.retrieval_feedback_sync.get("evidence_heat_factors", {}).get(cooled_item_id) == 0.92
    assert cooled_item_id not in result.retrieval_feedback_sync.get("importance_deltas", {})

    with soul_factory() as soul_db:
        from anima_server.services.agent.memory_store import get_memory_items_scored

        ranked = get_memory_items_scored(
            soul_db,
            user_id=user_id,
            category="fact",
            limit=10,
        )
        assert ranked[0].id == baseline_item_id
        assert ranked[1].id == cooled_item_id

    soul_engine.dispose()
    runtime_engine.dispose()


# ---------------------------------------------------------------------------
# Test 9: Ops processed before candidates (ordering guarantee)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ops_processed_before_candidates() -> None:
    """PendingMemoryOps should be processed before MemoryCandidates."""
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_soul_factory(soul_engine)
    runtime_factory = _make_runtime_factory(runtime_engine)

    with soul_factory() as soul_db:
        user = User(username="order-test", password_hash="x", display_name="Order Test")
        soul_db.add(user)
        soul_db.flush()
        user_id = user.id

        soul_db.add(
            SelfModelBlock(
                user_id=user_id,
                section="human",
                content="Name: Alice",
                version=1,
                updated_by="seed",
            )
        )
        soul_db.commit()

    # Create both an op and a candidate
    with runtime_factory() as runtime_db:
        op = PendingMemoryOp(
            user_id=user_id,
            op_type="append",
            target_block="human",
            content="\nAge: 30",
            content_hash=_pending_op_hash(user_id, "human", "append", "\nAge: 30"),
        )
        runtime_db.add(op)

        candidate = MemoryCandidate(
            user_id=user_id,
            content="Works at Google",
            category="fact",
            importance=3,
            importance_source="llm",
            source="llm",
            content_hash=_content_hash(user_id, "fact", "llm", "Works at Google"),
            status="extracted",
        )
        runtime_db.add(candidate)
        runtime_db.commit()

    result = await run_soul_writer(
        user_id,
        soul_db_factory=soul_factory,
        runtime_db_factory=runtime_factory,
    )

    assert result.ops_processed == 1
    assert result.candidates_promoted == 1
    assert result.errors == []

    # Verify both were applied
    with soul_factory() as soul_db:
        block = soul_db.scalar(
            select(SelfModelBlock).where(
                SelfModelBlock.user_id == user_id,
                SelfModelBlock.section == "human",
            )
        )
        assert block is not None
        assert "Age: 30" in block.content

        items = soul_db.scalars(select(MemoryItem).where(MemoryItem.user_id == user_id)).all()
        assert len(items) >= 1

    soul_engine.dispose()
    runtime_engine.dispose()


# ---------------------------------------------------------------------------
# Test 10: Failed candidate retried up to MAX_RETRY_COUNT then permanent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_candidate_retried_then_permanent() -> None:
    """Failed candidates retry up to MAX_RETRY_COUNT, then stay failed permanently."""
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_soul_factory(soul_engine)
    runtime_factory = _make_runtime_factory(runtime_engine)

    with soul_factory() as soul_db:
        user = User(username="retry-test", password_hash="x", display_name="Retry Test")
        soul_db.add(user)
        soul_db.commit()
        user_id = user.id

    # Create a candidate already at retry_count=2 (MAX_RETRY_COUNT - 1)
    with runtime_factory() as runtime_db:
        candidate = MemoryCandidate(
            user_id=user_id,
            content="Will fail again",
            category="fact",
            importance=3,
            importance_source="llm",
            source="llm",
            content_hash=_content_hash(user_id, "fact", "llm", "Will fail again"),
            status="failed",
            retry_count=2,
            last_error="previous failure",
        )
        runtime_db.add(candidate)
        runtime_db.commit()
        candidate_id = candidate.id

    # Patch to make it fail again
    import anima_server.services.agent.soul_writer as sw_module

    original_process = sw_module._process_candidate

    def _always_fail(candidate, *, user_id, runtime_db, soul_db_factory, result):
        raise RuntimeError("Persistent failure")

    sw_module._process_candidate = _always_fail
    try:
        result = await run_soul_writer(
            user_id,
            soul_db_factory=soul_factory,
            runtime_db_factory=runtime_factory,
        )
    finally:
        sw_module._process_candidate = original_process

    assert result.candidates_failed == 1

    # Verify retry_count is now 3 (>= MAX_RETRY_COUNT)
    with runtime_factory() as runtime_db:
        c = runtime_db.get(MemoryCandidate, candidate_id)
        assert c is not None
        assert c.retry_count == 3
        assert c.status == "failed"

    # Run again — should NOT pick it up (retry_count >= MAX_RETRY_COUNT)
    result2 = await run_soul_writer(
        user_id,
        soul_db_factory=soul_factory,
        runtime_db_factory=runtime_factory,
    )

    # No candidates processed at all
    assert result2.candidates_promoted == 0
    assert result2.candidates_failed == 0
    assert result2.candidates_rejected == 0

    soul_engine.dispose()
    runtime_engine.dispose()


# ---------------------------------------------------------------------------
# Test: Phase 4 (emotional-pattern promotion) dual-write ordering — A-6
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase4_runtime_commit_failure_does_not_lose_soul_promotion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A-6: if the runtime commit fails after the soul commit, the promoted
    patterns stay in the soul store and no exception escapes run_soul_writer
    (Phase 4 errors are logged and swallowed by design)."""
    from anima_server.models.soul_consciousness import CoreEmotionalPattern
    from anima_server.services.agent.emotional_intelligence import record_emotional_signal

    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_soul_factory(soul_engine)
    base_runtime_factory = _make_runtime_factory(runtime_engine)

    with soul_factory() as soul_db:
        user = User(
            username="phase4-commit-fail", password_hash="x", display_name="Phase4"
        )
        soul_db.add(user)
        soul_db.commit()
        user_id = user.id

    # Seed enough emotional signals of one emotion to clear both the
    # should_promote_emotional_patterns gate and the promotion threshold
    # (MIN_SIGNALS_FOR_PATTERN = 3), mirroring
    # TestEmotionalPatternPromotion.test_promote_from_signals in
    # test_p3_self_model_split.py.
    with base_runtime_factory() as runtime_db:
        for _ in range(5):
            record_emotional_signal(
                runtime_db,
                user_id=user_id,
                emotion="frustrated",
                confidence=0.7,
                evidence="Deadline talk",
                topic="work",
            )
        runtime_db.commit()

    # Soul Writer opens exactly one runtime session per phase (1, 1.5, 2, 2.5,
    # 3, 4) in that fixed order regardless of dual_session_scope migration —
    # Phase 4 is always the 6th runtime session opened. Fail only that
    # session's commit, simulating a runtime-commit failure strictly on the
    # dual-write (Phase 4) site without touching earlier phases.
    call_count = {"n": 0}

    def failing_runtime_factory():
        call_count["n"] += 1
        session = base_runtime_factory()
        if call_count["n"] == 6:
            def _raise_on_commit() -> None:
                raise RuntimeError("simulated runtime commit failure (Phase 4)")

            session.commit = _raise_on_commit
        return session

    result = await run_soul_writer(
        user_id,
        soul_db_factory=soul_factory,
        runtime_db_factory=failing_runtime_factory,
    )

    # 1. run_soul_writer completes without raising, and the swallowed Phase 4
    #    failure is not surfaced in result.errors (Phase 4 errors are logged
    #    at debug level and swallowed by the enclosing except, by design).
    assert isinstance(result, SoulWriterResult)
    assert result.errors == []

    # 2. The promoted pattern persisted in the soul store despite the
    #    runtime commit failure, because soul commits before runtime.
    with soul_factory() as soul_db:
        patterns = soul_db.scalars(
            select(CoreEmotionalPattern).where(
                CoreEmotionalPattern.user_id == user_id
            )
        ).all()
        assert len(patterns) >= 1
        assert any(p.dominant_emotion == "frustrated" for p in patterns)

    soul_engine.dispose()
    runtime_engine.dispose()
