"""Retry caps, native closed history, and restart-safe cognitive gates.

Poison-pill pending ops stop at the retry cap. Authored closed-thread history
stays in encrypted CoreFS; deep-monologue gates survive a process restart.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from anima_server.db.base import Base
from anima_server.db.runtime_base import RuntimeBase
from anima_server.models import SoulOwner
from anima_server.models.pending_memory_op import PendingMemoryOp
from anima_server.models.runtime import RuntimeBackgroundTaskRun
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
        user = SoulOwner(core_owner_id=str(uuid4()))
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
            candidates = {c.content: c for c in runtime_db.scalars(select(MemoryCandidate))}

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
# Canonical history survives thread closure without sidecar archival
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_native_closed_history_stays_readable_without_jsonl_archival():
    from anima_server.config import settings
    from anima_server.services.agent import eager_consolidation
    from anima_server.services.corefs.conversation_authority import get_canonical_thread
    from anima_server.services.corefs.conversation_mutations import (
        append_canonical_message,
        close_canonical_thread,
        create_canonical_thread,
    )
    from anima_server.services.sessions import unlock_session_store
    from conftest import managed_test_client

    with managed_test_client("current-native-closed-history-") as client:
        registered = client.post(
            "/api/auth/register",
            json={
                "username": "closed-history",
                "password": "password-one",
                "name": "History owner",
            },
        )
        assert registered.status_code == 201, registered.text
        account = registered.json()
        session = unlock_session_store.resolve(account["unlockToken"])
        assert session is not None
        view = create_canonical_thread(session=session, force_new=True)
        append_canonical_message(
            session=session,
            thread_id=view.document.thread_id,
            role="user",
            content="Keep this authenticated closed-thread history",
        )
        close_canonical_thread(session=session, thread_id=view.document.thread_id)

        def no_runtime_history_authority():
            raise AssertionError("Native closed history cannot depend on Runtime transcript bodies")

        await eager_consolidation.on_thread_close(
            thread_id=view.document.legacy_thread_id,
            user_id=account["id"],
            runtime_db_factory=no_runtime_history_authority,
            soul_db_factory=no_runtime_history_authority,
        )
        assert (
            await eager_consolidation.inactivity_sweep(
                runtime_db_factory=no_runtime_history_authority,
                soul_db_factory=no_runtime_history_authority,
            )
            == 0
        )
        assert await eager_consolidation.prune_expired_transcripts() == 0
        restored = get_canonical_thread(session=session, thread_id=view.document.thread_id)
        assert restored is not None
        assert restored.document.status == "closed"
        assert [message.content for message in restored.messages] == [
            "Keep this authenticated closed-thread history",
        ]
        assert list(settings.data_dir.rglob("*.jsonl*")) == []


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
        _add_monologue_run(runtime_factory, user_id=7, completed_at=datetime.now(UTC))
        assert (
            sleep_tasks._should_run_deep_monologue(7, runtime_db_factory=runtime_factory) is False
        )

        # A run older than the interval re-arms the monologue.
        monkeypatch.setattr(sleep_tasks, "_last_deep_monologue", {})
        _add_monologue_run(
            runtime_factory,
            user_id=8,
            completed_at=datetime.now(UTC) - timedelta(hours=25),
        )
        assert sleep_tasks._should_run_deep_monologue(8, runtime_db_factory=runtime_factory) is True

        # A completed task run whose monologue reported errors does not gate.
        monkeypatch.setattr(sleep_tasks, "_last_deep_monologue", {})
        _add_monologue_run(
            runtime_factory,
            user_id=9,
            completed_at=datetime.now(UTC),
            errors=["LLM parse failure"],
        )
        assert sleep_tasks._should_run_deep_monologue(9, runtime_db_factory=runtime_factory) is True
    finally:
        runtime_engine.dispose()
