"""ARH-004: retry caps, archival backoff, and restart-safe gates.

Poison-pill pending ops must stop retrying at the cap instead of churning
on every soul-writer run; failed thread archival must back off instead of
re-firing every 60s sweep forever; the deep-monologue 24h gate must survive
a process restart.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta

import pytest
from anima_server.db.base import Base
from anima_server.db.runtime_base import RuntimeBase
from anima_server.models import User
from anima_server.models.pending_memory_op import PendingMemoryOp
from anima_server.models.runtime import RuntimeBackgroundTaskRun, RuntimeThread
from anima_server.models.runtime_memory import MemoryCandidate
from anima_server.services.agent.soul_writer import MAX_RETRY_COUNT, run_soul_writer
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


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


def _factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _content_hash(user_id: int, category: str, importance_source: str, content: str) -> str:
    normalized = content.strip().lower()
    return hashlib.sha256(
        f"{user_id}:{category}:{importance_source}:{normalized}".encode()
    ).hexdigest()


def _make_user(soul_factory: sessionmaker[Session], username: str) -> int:
    with soul_factory() as soul_db:
        user = User(username=username, password_hash="x", display_name=username)
        soul_db.add(user)
        soul_db.commit()
        return user.id


# --------------------------------------------------------------------------- #
# Poison-pill pending ops
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_poison_pill_pending_op_stops_at_retry_cap(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An op that fails deterministically (unknown op_type) is retried
    MAX_RETRY_COUNT times, then skipped on every later run with a WARNING
    on the degraded logger — never reset-and-retried forever."""
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _factory(soul_engine)
    runtime_factory = _factory(runtime_engine)

    try:
        user_id = _make_user(soul_factory, "poison-pill")

        with runtime_factory() as runtime_db:
            runtime_db.add(
                PendingMemoryOp(
                    user_id=user_id,
                    op_type="bogus_op",
                    target_block="human",
                    content="unprocessable",
                )
            )
            runtime_db.commit()

        for expected_retry in range(1, MAX_RETRY_COUNT + 1):
            result = await run_soul_writer(
                user_id,
                soul_db_factory=soul_factory,
                runtime_db_factory=runtime_factory,
                ops_only=True,
            )
            assert result.ops_failed == 1
            with runtime_factory() as runtime_db:
                op = runtime_db.scalar(select(PendingMemoryOp))
                assert op.failed is True
                assert op.retry_count == expected_retry

        # At the cap: the op is no longer picked up, and the skip is loud.
        with caplog.at_level(logging.WARNING, logger="anima.runtime.degraded"):
            result = await run_soul_writer(
                user_id,
                soul_db_factory=soul_factory,
                runtime_db_factory=runtime_factory,
                ops_only=True,
            )
        assert result.ops_failed == 0
        with runtime_factory() as runtime_db:
            op = runtime_db.scalar(select(PendingMemoryOp))
            assert op.retry_count == MAX_RETRY_COUNT
        assert any(
            r.name == "anima.runtime.degraded" and "retry cap" in r.getMessage()
            for r in caplog.records
        )
    finally:
        soul_engine.dispose()
        runtime_engine.dispose()


# --------------------------------------------------------------------------- #
# Phase 2 IntegrityError fallback: savepoint per candidate
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_duplicate_hash_candidate_does_not_abort_batch_mates() -> None:
    """One duplicate-hash collision fails only that candidate (with its
    retry_count incremented) while batch-mates still get processed."""
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _factory(soul_engine)
    runtime_factory = _factory(runtime_engine)

    # The production partial unique index (created by migration 019) is what
    # makes re-queuing a duplicate hash fail; recreate it on the test DB.
    with runtime_engine.connect() as conn:
        conn.exec_driver_sql(
            "CREATE UNIQUE INDEX uq_memory_candidates_active_hash "
            "ON memory_candidates(content_hash) "
            "WHERE status NOT IN ('rejected', 'reinforced', 'superseded', 'failed')"
        )
        conn.commit()

    try:
        user_id = _make_user(soul_factory, "dupe-batch")
        dupe_hash = _content_hash(user_id, "preference", "llm", "Likes green tea")

        with runtime_factory() as runtime_db:
            # Active (promoted) row holding the hash — not selected by the
            # soul writer's query, so the collision only surfaces at flush.
            runtime_db.add(
                MemoryCandidate(
                    user_id=user_id,
                    content="Likes green tea",
                    category="preference",
                    importance=3,
                    importance_source="llm",
                    source="llm",
                    content_hash=dupe_hash,
                    status="promoted",
                )
            )
            # Failed candidate with the same hash, below the retry cap —
            # selected for retry, but re-queuing violates the index.
            runtime_db.add(
                MemoryCandidate(
                    user_id=user_id,
                    content="Likes green tea",
                    category="preference",
                    importance=3,
                    importance_source="llm",
                    source="llm",
                    content_hash=dupe_hash,
                    status="failed",
                    retry_count=0,
                )
            )
            # Innocent batch-mate.
            runtime_db.add(
                MemoryCandidate(
                    user_id=user_id,
                    content="Enjoys morning walks",
                    category="preference",
                    importance=3,
                    importance_source="llm",
                    source="llm",
                    content_hash=_content_hash(
                        user_id, "preference", "llm", "Enjoys morning walks"
                    ),
                    status="extracted",
                )
            )
            runtime_db.commit()

        result = await run_soul_writer(
            user_id,
            soul_db_factory=soul_factory,
            runtime_db_factory=runtime_factory,
        )

        with runtime_factory() as runtime_db:
            candidates = {
                c.content: c for c in runtime_db.scalars(select(MemoryCandidate))
            }

        dupe = [
            c
            for c in candidates.values()
            if c.content == "Likes green tea" and c.status == "failed"
        ]
        assert len(dupe) == 1
        assert dupe[0].retry_count == 1
        assert "duplicate" in (dupe[0].last_error or "")

        mate = candidates["Enjoys morning walks"]
        assert mate.status not in ("extracted", "queued", "failed")
        assert result.candidates_promoted >= 1
    finally:
        soul_engine.dispose()
        runtime_engine.dispose()


# --------------------------------------------------------------------------- #
# Archival backoff
# --------------------------------------------------------------------------- #


def _make_closed_thread(runtime_factory: sessionmaker[Session], user_id: int) -> int:
    with runtime_factory() as db:
        thread = RuntimeThread(
            user_id=user_id,
            status="closed",
            closed_at=datetime.now(UTC),
            is_archived=False,
        )
        db.add(thread)
        db.commit()
        return thread.id


@pytest.mark.asyncio
async def test_archival_failure_backs_off_instead_of_retrying_every_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import eager_consolidation

    runtime_engine = _create_runtime_engine()
    runtime_factory = _factory(runtime_engine)

    try:
        thread_id = _make_closed_thread(runtime_factory, user_id=1)

        attempts: list[int] = []

        async def failing_close(**kwargs) -> None:
            attempts.append(kwargs["thread_id"])
            raise RuntimeError("transcripts dir unwritable")

        monkeypatch.setattr(eager_consolidation, "on_thread_close", failing_close)

        await eager_consolidation.inactivity_sweep(
            runtime_db_factory=runtime_factory,
            soul_db_factory=lambda: None,
        )
        assert attempts == [thread_id]
        with runtime_factory() as db:
            thread = db.get(RuntimeThread, thread_id)
            assert thread.archive_retry_count == 1
            assert thread.archive_next_retry_at is not None
            assert thread.archive_failed is False

        # An immediate second sweep must NOT retry (backoff window).
        await eager_consolidation.inactivity_sweep(
            runtime_db_factory=runtime_factory,
            soul_db_factory=lambda: None,
        )
        assert attempts == [thread_id]
        with runtime_factory() as db:
            thread = db.get(RuntimeThread, thread_id)
            assert thread.archive_retry_count == 1
    finally:
        runtime_engine.dispose()


@pytest.mark.asyncio
async def test_archival_gives_up_terminally_at_the_cap(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from anima_server.services.agent import eager_consolidation

    runtime_engine = _create_runtime_engine()
    runtime_factory = _factory(runtime_engine)

    try:
        thread_id = _make_closed_thread(runtime_factory, user_id=1)
        with runtime_factory() as db:
            thread = db.get(RuntimeThread, thread_id)
            thread.archive_retry_count = eager_consolidation._ARCHIVE_MAX_RETRIES - 1
            thread.archive_next_retry_at = datetime.now(UTC) - timedelta(minutes=1)
            db.commit()

        async def failing_close(**kwargs) -> None:
            raise RuntimeError("still broken")

        monkeypatch.setattr(eager_consolidation, "on_thread_close", failing_close)

        with caplog.at_level(logging.WARNING, logger="anima.runtime.degraded"):
            await eager_consolidation.inactivity_sweep(
                runtime_db_factory=runtime_factory,
                soul_db_factory=lambda: None,
            )

        with runtime_factory() as db:
            thread = db.get(RuntimeThread, thread_id)
            assert thread.archive_failed is True
        assert any(
            r.name == "anima.runtime.degraded" and "permanently failed" in r.getMessage()
            for r in caplog.records
        )

        # Terminal threads are never picked up again.
        attempts: list[int] = []

        async def counting_close(**kwargs) -> None:
            attempts.append(kwargs["thread_id"])

        monkeypatch.setattr(eager_consolidation, "on_thread_close", counting_close)
        await eager_consolidation.inactivity_sweep(
            runtime_db_factory=runtime_factory,
            soul_db_factory=lambda: None,
        )
        assert attempts == []
    finally:
        runtime_engine.dispose()


@pytest.mark.asyncio
async def test_successful_archival_clears_retry_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import eager_consolidation

    runtime_engine = _create_runtime_engine()
    runtime_factory = _factory(runtime_engine)

    try:
        thread_id = _make_closed_thread(runtime_factory, user_id=1)
        with runtime_factory() as db:
            thread = db.get(RuntimeThread, thread_id)
            thread.archive_retry_count = 3
            thread.archive_next_retry_at = datetime.now(UTC) - timedelta(minutes=1)
            db.commit()

        async def succeeding_close(**kwargs) -> None:
            with runtime_factory() as db:
                thread = db.get(RuntimeThread, kwargs["thread_id"])
                thread.is_archived = True
                db.commit()

        monkeypatch.setattr(eager_consolidation, "on_thread_close", succeeding_close)
        await eager_consolidation.inactivity_sweep(
            runtime_db_factory=runtime_factory,
            soul_db_factory=lambda: None,
        )

        with runtime_factory() as db:
            thread = db.get(RuntimeThread, thread_id)
            assert thread.is_archived is True
            assert thread.archive_retry_count == 0
            assert thread.archive_next_retry_at is None
            assert thread.archive_failed is False
    finally:
        runtime_engine.dispose()


# --------------------------------------------------------------------------- #
# Restart-safe deep-monologue gate
# --------------------------------------------------------------------------- #


def _add_monologue_run(
    runtime_factory: sessionmaker[Session],
    *,
    user_id: int,
    completed_at: datetime,
    errors: list[str] | None = None,
) -> None:
    with runtime_factory() as db:
        db.add(
            RuntimeBackgroundTaskRun(
                user_id=user_id,
                task_type="deep_monologue",
                status="completed",
                completed_at=completed_at,
                result_json={"errors": errors or []},
            )
        )
        db.commit()


def test_deep_monologue_gate_survives_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import sleep_tasks

    runtime_engine = _create_runtime_engine()
    runtime_factory = _factory(runtime_engine)

    try:
        # Fresh process: the in-memory dict is empty.
        monkeypatch.setattr(sleep_tasks, "_last_deep_monologue", {})
        _add_monologue_run(
            runtime_factory, user_id=7, completed_at=datetime.now(UTC)
        )
        assert (
            sleep_tasks._should_run_deep_monologue(
                7, runtime_db_factory=runtime_factory
            )
            is False
        )

        # A run older than the interval re-arms the monologue.
        monkeypatch.setattr(sleep_tasks, "_last_deep_monologue", {})
        _add_monologue_run(
            runtime_factory,
            user_id=8,
            completed_at=datetime.now(UTC) - timedelta(hours=25),
        )
        assert (
            sleep_tasks._should_run_deep_monologue(
                8, runtime_db_factory=runtime_factory
            )
            is True
        )

        # A completed task run whose monologue reported errors does not gate.
        monkeypatch.setattr(sleep_tasks, "_last_deep_monologue", {})
        _add_monologue_run(
            runtime_factory,
            user_id=9,
            completed_at=datetime.now(UTC),
            errors=["LLM parse failure"],
        )
        assert (
            sleep_tasks._should_run_deep_monologue(
                9, runtime_db_factory=runtime_factory
            )
            is True
        )
    finally:
        runtime_engine.dispose()
