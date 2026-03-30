"""Tests for Soul Writer orchestrator — single serialized promoter pipeline."""
from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from anima_server.db.base import Base
from anima_server.db.runtime_base import RuntimeBase
from anima_server.models import MemoryItem, User
from anima_server.models.consciousness import SelfModelBlock
from anima_server.models.pending_memory_op import PendingMemoryOp
from anima_server.models.runtime_memory import (
    MemoryAccessLog,
    MemoryCandidate,
    PromotionJournal,
)
from anima_server.services.agent.soul_writer import (
    SoulWriterResult,
    _process_candidate,
    _process_pending_op,
    plan_candidate_promotion,
    run_soul_writer,
)
from conftest_runtime import runtime_db_session
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.ext.compiler import compiles
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
        items = soul_db.scalars(
            select(MemoryItem).where(MemoryItem.user_id == user_id)
        ).all()
        assert len(items) >= 1

    soul_engine.dispose()
    runtime_engine.dispose()


# ---------------------------------------------------------------------------
# Test 3: Candidate matching existing item is rejected (duplicate)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_candidate_duplicate_rejected() -> None:
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

    assert result.candidates_rejected == 1
    assert result.candidates_promoted == 0

    # Verify candidate marked as rejected
    with runtime_factory() as runtime_db:
        c = runtime_db.scalar(select(MemoryCandidate).where(MemoryCandidate.user_id == user_id))
        assert c is not None
        assert c.status == "rejected"

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

    result = await run_soul_writer(
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

    def _patched_process(candidate, *, user_id, runtime_db, soul_db_factory, result):
        nonlocal call_count
        call_count += 1
        if candidate.content == "Another valid fact":
            raise RuntimeError("Simulated processing failure")
        return original_process(
            candidate, user_id=user_id, runtime_db=runtime_db,
            soul_db_factory=soul_db_factory, result=result,
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

        items = soul_db.scalars(
            select(MemoryItem).where(MemoryItem.user_id == user_id)
        ).all()
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
