"""Tests for F5 — Async sleep-time agent orchestrator."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from anima_server.db.base import Base
from anima_server.db.runtime_base import RuntimeBase
from anima_server.models import KGRelation, User
from anima_server.models.runtime import RuntimeBackgroundTaskRun, RuntimeMessage, RuntimeThread
from anima_server.models.runtime_memory import MemoryCandidate, MemoryExtractionFailure
from anima_server.services.agent import knowledge_graph as knowledge_graph_module
from anima_server.services.agent.sleep_agent import (
    _issue_background_task,
    _should_run_expensive,
    _task_consolidation,
    _task_episode_gen,
    _task_graph_ingestion,
    _task_reparse_pending_documents,
    get_last_processed_message_id,
    run_sleeptime_agents,
    should_run_sleeptime,
    update_last_processed_message_id,
)
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture()
def db_engine():
    """Soul DB engine (for heat scoring, soul-side operations)."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _set_wal(conn, _rec):
        conn.execute("PRAGMA journal_mode=WAL")

    Base.metadata.create_all(engine)
    return engine


@pytest.fixture()
def db_factory(db_engine):
    factory = sessionmaker(
        bind=db_engine,
        autoflush=False,
        expire_on_commit=False,
    )
    return factory


@pytest.fixture()
def runtime_db_engine():
    """Runtime DB engine for RuntimeBackgroundTaskRun and task tracking."""

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _set_wal(conn, _rec):
        conn.execute("PRAGMA journal_mode=WAL")

    RuntimeBase.metadata.create_all(engine)
    return engine


@pytest.fixture()
def rt_factory(runtime_db_engine):
    """Runtime DB session factory."""
    factory = sessionmaker(
        bind=runtime_db_engine,
        autoflush=False,
        expire_on_commit=False,
    )
    return factory


# ── _issue_background_task ───────────────────────────────────────────


class TestIssueBackgroundTask:
    @pytest.mark.asyncio()
    async def test_successful_task(self, db_factory, rt_factory):
        async def _dummy_task(*, user_id, db_factory=None):
            return {"ok": True}

        run_id = await _issue_background_task(
            user_id=1,
            task_type="test_task",
            task_fn=_dummy_task,
            db_factory=db_factory,
            runtime_db_factory=rt_factory,
        )

        assert run_id.startswith("test_task:")
        task_id = int(run_id.split(":")[1])

        with rt_factory() as db:
            run = db.get(RuntimeBackgroundTaskRun, task_id)
            assert run is not None
            assert run.status == "completed"
            assert run.result_json == {"ok": True}
            assert run.error_message is None
            assert run.started_at is not None
            assert run.completed_at is not None

    @pytest.mark.asyncio()
    async def test_failed_task(self, db_factory, rt_factory):
        async def _failing_task(*, user_id, db_factory=None):
            raise ValueError("test error")

        run_id = await _issue_background_task(
            user_id=1,
            task_type="fail_task",
            task_fn=_failing_task,
            db_factory=db_factory,
            runtime_db_factory=rt_factory,
        )

        task_id = int(run_id.split(":")[1])
        with rt_factory() as db:
            run = db.get(RuntimeBackgroundTaskRun, task_id)
            assert run is not None
            assert run.status == "failed"
            assert "test error" in run.error_message
            assert run.completed_at is not None

    @pytest.mark.asyncio()
    async def test_non_dict_result(self, db_factory, rt_factory):
        """When task_fn returns a non-dict, result_json should be None."""

        async def _string_task(*, user_id, db_factory=None):
            return "just a string"

        run_id = await _issue_background_task(
            user_id=1,
            task_type="string_task",
            task_fn=_string_task,
            db_factory=db_factory,
            runtime_db_factory=rt_factory,
        )

        task_id = int(run_id.split(":")[1])
        with rt_factory() as db:
            run = db.get(RuntimeBackgroundTaskRun, task_id)
            assert run.status == "completed"
            assert run.result_json is None

    @pytest.mark.asyncio()
    async def test_uses_default_runtime_factory_for_task_cursor(self, db_factory, rt_factory):
        with rt_factory() as db:
            thread = RuntimeThread(user_id=1, status="active")
            db.add(thread)
            db.flush()
            message = RuntimeMessage(
                user_id=1,
                thread_id=thread.id,
                sequence_id=1,
                role="user",
                content_text="hello",
            )
            db.add(message)
            db.commit()
            thread_id = thread.id
            expected_message_id = message.id

        with (
            patch(
                "anima_server.db.runtime.get_runtime_session_factory",
                return_value=rt_factory,
            ),
            patch(
                "anima_server.services.agent.soul_writer.run_soul_writer",
                new_callable=AsyncMock,
            ),
        ):
            run_id = await _issue_background_task(
                user_id=1,
                task_type="consolidation",
                task_fn=_task_consolidation,
                db_factory=db_factory,
                user_message="hello",
                thread_id=thread_id,
                _task_runtime_db_factory=None,
            )

        task_id = int(run_id.split(":")[1])
        with rt_factory() as db:
            run = db.get(RuntimeBackgroundTaskRun, task_id)
            assert run is not None
            assert run.status == "completed"
            assert run.result_json == {
                "thread_id": thread_id,
                "last_processed_message_id": expected_message_id,
                "messages_processed": 1,
            }


# ── Task failure isolation ───────────────────────────────────────────


class TestTaskFailureIsolation:
    @pytest.mark.asyncio()
    async def test_one_failure_does_not_cancel_others(self, db_factory, rt_factory):
        """One task raising does not prevent others from completing."""
        call_log = []

        async def _good_task(*, user_id, db_factory=None, **kwargs):
            call_log.append("good")
            return {"status": "ok"}

        async def _bad_task(*, user_id, db_factory=None, **kwargs):
            call_log.append("bad")
            raise RuntimeError("boom")

        results = await asyncio.gather(
            _issue_background_task(
                user_id=1,
                task_type="good1",
                task_fn=_good_task,
                db_factory=db_factory,
                runtime_db_factory=rt_factory,
            ),
            _issue_background_task(
                user_id=1,
                task_type="bad1",
                task_fn=_bad_task,
                db_factory=db_factory,
                runtime_db_factory=rt_factory,
            ),
            _issue_background_task(
                user_id=1,
                task_type="good2",
                task_fn=_good_task,
                db_factory=db_factory,
                runtime_db_factory=rt_factory,
            ),
            return_exceptions=True,
        )

        # All three tasks should have been called
        assert len(call_log) == 3

        # Good tasks completed, bad task failed
        good_ids = [r for r in results if isinstance(
            r, str) and r.startswith("good")]
        assert len(good_ids) == 2

        with rt_factory() as db:
            runs = list(db.scalars(select(RuntimeBackgroundTaskRun)).all())
            statuses = {r.task_type: r.status for r in runs}
            assert statuses["good1"] == "completed"
            assert statuses["good2"] == "completed"
            assert statuses["bad1"] == "failed"


class TestEpisodeGenerationRetry:
    @pytest.mark.asyncio()
    async def test_retries_locked_database_then_succeeds(self, db_factory):
        with (
            patch(
                "anima_server.services.agent.episodes.maybe_generate_episode",
                new=AsyncMock(
                    side_effect=[
                        OperationalError(
                            "insert", {}, Exception("database is locked")),
                        object(),
                    ]
                ),
            ) as maybe_generate_episode,
            patch("anima_server.services.agent.sleep_agent.asyncio.sleep", new=AsyncMock()) as sleep_mock,
        ):
            result = await _task_episode_gen(user_id=1, db_factory=db_factory)

        assert result == {"generated": True}
        assert maybe_generate_episode.await_count == 2
        sleep_mock.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_skips_when_database_remains_locked(self, db_factory):
        locked_error = OperationalError(
            "insert", {}, Exception("database is locked"))

        with (
            patch(
                "anima_server.services.agent.episodes.maybe_generate_episode",
                new=AsyncMock(
                    side_effect=[locked_error, locked_error, locked_error]),
            ) as maybe_generate_episode,
            patch("anima_server.services.agent.sleep_agent.asyncio.sleep", new=AsyncMock()) as sleep_mock,
        ):
            result = await _task_episode_gen(user_id=1, db_factory=db_factory)

        assert result == {"generated": False, "skipped": "database_locked"}
        assert maybe_generate_episode.await_count == 3
        assert sleep_mock.await_count == 2


# ── force=True ───────────────────────────────────────────────────────


class TestForceMode:
    @pytest.mark.asyncio()
    async def test_force_bypasses_heat_gate(self, db_factory, rt_factory):
        """With force=True, synthesis tasks run even with no heat — but the
        contradiction scan (the dominant recurring LLM cost) still honors
        the heat gate, and tasks with no fresh inputs are skipped."""
        # Fresh inputs for the synthesis tasks: a memory item (profile)
        # and an episode (patterns), with no completed runs recorded.
        from anima_server.models import MemoryEpisode, MemoryItem

        with db_factory() as db:
            user = User(username="force-mode", password_hash="x", display_name="F")
            db.add(user)
            db.flush()
            db.add(
                MemoryItem(
                    user_id=user.id,
                    content="Likes green tea",
                    category="preference",
                    importance=3,
                    source="extraction",
                )
            )
            db.add(
                MemoryEpisode(
                    user_id=user.id,
                    date="2026-07-07",
                    summary="Talked about tea preferences.",
                )
            )
            db.commit()
            user_id = user.id

        with (
            patch(
                "anima_server.services.agent.sleep_agent._task_consolidation",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "anima_server.services.agent.sleep_agent._task_embedding_backfill",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "anima_server.services.agent.sleep_agent._task_graph_ingestion",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "anima_server.services.agent.sleep_agent._task_heat_decay",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "anima_server.services.agent.sleep_agent._task_episode_gen",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "anima_server.services.agent.sleep_agent._task_contradiction_scan",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "anima_server.services.agent.sleep_agent._task_profile_synthesis",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "anima_server.services.agent.sleep_agent._task_pattern_synthesis",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "anima_server.services.agent.sleep_agent._task_deep_monologue",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "anima_server.services.agent.sleep_tasks._should_run_deep_monologue",
                return_value=True,
            ),
            patch(
                "anima_server.services.agent.companion.get_companion",
                return_value=None,
            ),
        ):
            run_ids = await run_sleeptime_agents(
                user_id=user_id,
                user_message="test",
                assistant_response="resp",
                db_factory=db_factory,
                runtime_db_factory=rt_factory,
                force=True,
            )

        # With force=True and fresh inputs, the synthesis tasks run.
        # The contradiction scan honors the heat gate even under force
        # (no heat here → skipped).  Deep monologue respects the 24h
        # throttle (mocked True here).
        assert not any("contradiction_scan" in r for r in run_ids)
        assert any("profile_synthesis" in r for r in run_ids)
        assert any("pattern_synthesis" in r for r in run_ids)
        assert any("deep_monologue" in r for r in run_ids)


# ── Heat gating ──────────────────────────────────────────────────────


@pytest.mark.asyncio()
async def test_scheduled_sleeptime_runs_foresight_lifecycle(db_factory, rt_factory):
    with (
        patch(
            "anima_server.services.agent.sleep_agent._task_consolidation",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch(
            "anima_server.services.agent.sleep_agent._task_embedding_backfill",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch(
            "anima_server.services.agent.sleep_agent._task_graph_ingestion",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch(
            "anima_server.services.agent.sleep_agent._task_heat_decay",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch(
            "anima_server.services.agent.sleep_agent._task_episode_gen",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch(
            "anima_server.services.agent.sleep_agent._should_run_expensive",
            return_value=False,
        ),
        patch(
            "anima_server.services.agent.sleep_tasks._should_run_deep_monologue",
            return_value=False,
        ),
        patch(
            "anima_server.services.agent.companion.get_companion",
            return_value=None,
        ),
    ):
        run_ids = await run_sleeptime_agents(
            user_id=1,
            user_message="hello",
            assistant_response="hi",
            db_factory=db_factory,
            runtime_db_factory=rt_factory,
        )

    task_types = {run_id.split(":")[0] for run_id in run_ids}
    assert "foresight_lifecycle" in task_types


class TestHeatGating:
    def test_no_items_means_no_expensive(self, db_factory):
        with db_factory() as db:
            assert _should_run_expensive(db, user_id=999) is False


class TestTurnFrequency:
    def test_should_run_sleeptime_every_third_turn(self):
        assert should_run_sleeptime(None) is False
        assert should_run_sleeptime(1) is False
        assert should_run_sleeptime(2) is False
        assert should_run_sleeptime(3) is True
        assert should_run_sleeptime(6) is True


@pytest.mark.asyncio()
async def test_run_sleeptime_agents_invalidates_companion_cache(
    db_factory, rt_factory, monkeypatch
):
    """The orchestrator invalidates the companion memory cache once at the end
    of a run so the next turn sees fresh soul data (this used to be coupled to
    profile reconciliation inside the removed run_sleep_tasks; it is now an
    unconditional post-run step)."""
    from anima_server.services.agent import sleep_agent

    with db_factory() as db:
        user = User(
            username="sleep-invalidate", password_hash="x", display_name="S"
        )
        db.add(user)
        db.commit()
        user_id = user.id

    invalidations = {"count": 0}

    class _FakeCompanion:
        def invalidate_memory(self) -> None:
            invalidations["count"] += 1

    async def _recording_issue(*, user_id, task_type, task_fn, **kwargs) -> str:
        return f"{task_type}:0"

    monkeypatch.setattr(sleep_agent, "_issue_background_task", _recording_issue)
    monkeypatch.setattr(sleep_agent, "_should_run_expensive", lambda db, uid: False)
    monkeypatch.setattr(
        "anima_server.services.agent.companion.get_companion",
        lambda uid: _FakeCompanion(),
    )

    await sleep_agent.run_sleeptime_agents(
        user_id=user_id,
        user_message="hi",
        assistant_response="hello",
        force=True,
        db_factory=db_factory,
        runtime_db_factory=rt_factory,
    )

    assert invalidations["count"] == 1


@pytest.mark.asyncio()
async def test_reembed_reset_runs_once_per_cycle(db_factory, monkeypatch):
    """The re-embed reset is destructive and must run exactly once per user per
    cycle.  Backfill does ~10 items/pass; re-running the reset on every
    sleeptime pass (while reembed_required stays true) would re-null the
    previous pass's work and semantic search would never recover.  The reset is
    gated by an explicit per-user marker, not by null-embedding counts — a
    null-count guard mis-fires the moment the first batch embeds (count drops to
    0, triggering a second destructive reset mid-cycle)."""
    from anima_server.models import MemoryItem
    from anima_server.services.agent import (
        consolidation,
        embedding_contract,
        sleep_agent,
        vector_store,
    )

    reset_calls = {"n": 0}
    reset_marker: set[int] = set()

    def _reset_spy(soul_db, *, user_id, runtime_db_factory=None):
        reset_calls["n"] += 1
        return 0

    async def _noop_backfill(user_id, db_factory=None):
        return None

    monkeypatch.setattr(embedding_contract, "is_reembed_required", lambda *a, **k: True)
    monkeypatch.setattr(embedding_contract, "reset_derived_embedding_stores", _reset_spy)
    monkeypatch.setattr(
        embedding_contract, "ensure_pgvector_dimension", lambda *a, **k: True
    )
    monkeypatch.setattr(
        embedding_contract,
        "has_reset_done",
        lambda uid, *a, **k: uid in reset_marker,
    )
    monkeypatch.setattr(
        embedding_contract,
        "mark_reset_done",
        lambda uid, *a, **k: reset_marker.add(uid),
    )
    monkeypatch.setattr(
        embedding_contract, "mark_user_reembed_complete", lambda *a, **k: None
    )
    monkeypatch.setattr(
        embedding_contract, "sweep_orphaned_runtime_embeddings", lambda *a, **k: 0
    )
    monkeypatch.setattr(consolidation, "_backfill_user_embeddings", _noop_backfill)
    monkeypatch.setattr(vector_store, "consume_vector_store_dirty", lambda uid: False)

    with db_factory() as db:
        user_a = User(username="reembed-a", password_hash="x", display_name="A")
        db.add(user_a)
        db.commit()
        uid_a = user_a.id
        db.add(
            MemoryItem(
                user_id=uid_a, content="x", category="fact", importance=3,
                source="e", embedding_json=[0.1] * 8,
            )
        )
        db.commit()

    # First pass resets once and records the marker; a second pass in the same
    # cycle sees the marker and must NOT reset again.
    await sleep_agent._task_embedding_backfill(user_id=uid_a, db_factory=db_factory)
    await sleep_agent._task_embedding_backfill(user_id=uid_a, db_factory=db_factory)
    assert reset_calls["n"] == 1


@pytest.mark.asyncio()
async def test_reembed_reset_not_marked_when_pgvector_alignment_fails(
    db_factory, monkeypatch
):
    """If the pgvector ALTER fails (PG unavailable/locked), the reset must NOT
    be marked done — otherwise the column stays at the old vector(N) type with
    every upsert failing and no later pass retrying the ALTER."""
    from anima_server.services.agent import (
        consolidation,
        embedding_contract,
        sleep_agent,
        vector_store,
    )

    reset_marker: set[int] = set()
    complete_calls: list[int] = []

    async def _noop_backfill(user_id, db_factory=None):
        return 0

    monkeypatch.setattr(embedding_contract, "is_reembed_required", lambda *a, **k: True)
    monkeypatch.setattr(
        embedding_contract, "reset_derived_embedding_stores", lambda *a, **k: 0
    )
    # Alignment keeps failing.
    monkeypatch.setattr(
        embedding_contract, "ensure_pgvector_dimension", lambda *a, **k: False
    )
    monkeypatch.setattr(
        embedding_contract,
        "has_reset_done",
        lambda uid, *a, **k: uid in reset_marker,
    )
    monkeypatch.setattr(
        embedding_contract,
        "mark_reset_done",
        lambda uid, *a, **k: reset_marker.add(uid),
    )
    monkeypatch.setattr(
        embedding_contract,
        "mark_user_reembed_complete",
        lambda uid, *a, **k: complete_calls.append(uid),
    )
    monkeypatch.setattr(
        embedding_contract, "sweep_orphaned_runtime_embeddings", lambda *a, **k: 0
    )
    monkeypatch.setattr(consolidation, "_backfill_user_embeddings", _noop_backfill)
    monkeypatch.setattr(vector_store, "consume_vector_store_dirty", lambda uid: False)

    with db_factory() as db:
        user = User(username="reembed-fail", password_hash="x", display_name="F")
        db.add(user)
        db.commit()
        uid = user.id

    await sleep_agent._task_embedding_backfill(user_id=uid, db_factory=db_factory)
    # Alignment failed → the reset marker must remain unset so a later pass
    # re-runs the reset + ALTER.
    assert uid not in reset_marker
    # And the user must NOT be marked complete (which would re-enable semantic
    # search against a still-misaligned column) even though 0 items remain.
    assert complete_calls == []


@pytest.mark.asyncio()
async def test_embedding_backfill_task_reports_counts(db_factory, monkeypatch):
    """The task must return the real backfill/resync counts so the manual
    /sleep summary isn't hard-coded to 0."""
    from anima_server.services.agent import (
        consolidation,
        embedding_contract,
        sleep_agent,
        vector_store,
    )

    async def _backfill_five(user_id, db_factory=None):
        return 5

    monkeypatch.setattr(embedding_contract, "is_reembed_required", lambda *a, **k: False)
    monkeypatch.setattr(
        embedding_contract, "sweep_orphaned_runtime_embeddings", lambda *a, **k: 0
    )
    monkeypatch.setattr(consolidation, "_backfill_user_embeddings", _backfill_five)
    monkeypatch.setattr(vector_store, "consume_vector_store_dirty", lambda uid: False)

    result = await sleep_agent._task_embedding_backfill(user_id=1, db_factory=db_factory)
    assert result == {"backfilled": 5, "resynced": 0}


def test_reset_derived_embedding_stores_nulls_via_sql_null() -> None:
    """reset must persist SQL NULL, not JSON 'null' — otherwise the backfill
    selector (embedding_json IS NULL) never finds reset items and re-embed
    silently 'completes' without regenerating anything."""
    from anima_server.models import MemoryItem
    from anima_server.services.agent.embedding_contract import (
        reset_derived_embedding_stores,
    )

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with factory() as db:
            db.add(User(username="reset-null", password_hash="x", display_name="R"))
            db.commit()
            db.add(
                MemoryItem(
                    user_id=1, content="z", category="fact", importance=3,
                    source="e", embedding_json=[0.1] * 8,
                )
            )
            db.commit()
            reset_derived_embedding_stores(db, user_id=1)
            db.commit()
            pending = db.scalar(
                select(func.count())
                .select_from(MemoryItem)
                .where(MemoryItem.embedding_json.is_(None))
            )
        assert pending == 1  # reset item is matched by IS NULL
    finally:
        engine.dispose()


# ── Restart cursor ───────────────────────────────────────────────────


class TestRestartCursor:
    def test_no_runs_returns_none(self, rt_factory):
        assert get_last_processed_message_id(
            1, runtime_db_factory=rt_factory) is None

    def test_round_trip(self, rt_factory):
        update_last_processed_message_id(
            1,
            thread_id=10,
            message_id=42,
            messages_processed=5,
            runtime_db_factory=rt_factory,
        )
        msg_id = get_last_processed_message_id(
            1, thread_id=10, runtime_db_factory=rt_factory)
        assert msg_id == 42

    def test_thread_scope_isolation(self, rt_factory):
        """Cursor for thread 10 should not match thread 20 or the global scope."""
        update_last_processed_message_id(
            1,
            thread_id=10,
            message_id=42,
            messages_processed=5,
            runtime_db_factory=rt_factory,
        )
        # Thread 20 and the None (global) scope have no cursor.
        assert get_last_processed_message_id(
            1, thread_id=20, runtime_db_factory=rt_factory) is None
        assert get_last_processed_message_id(
            1, thread_id=None, runtime_db_factory=rt_factory) is None
        # Thread 10 has the cursor.
        assert get_last_processed_message_id(
            1, thread_id=10, runtime_db_factory=rt_factory) == 42

    def test_update_cursor_upserts_single_row(self, rt_factory):
        update_last_processed_message_id(
            1,
            thread_id=None,
            message_id=10,
            messages_processed=3,
            runtime_db_factory=rt_factory,
        )
        # A second update for the same scope overwrites in place — no
        # duplicate row (SQL treats NULL as distinct, so the select-then-
        # upsert must handle the global scope explicitly).
        update_last_processed_message_id(
            1,
            thread_id=None,
            message_id=50,
            messages_processed=7,
            runtime_db_factory=rt_factory,
        )
        assert get_last_processed_message_id(
            1, thread_id=None, runtime_db_factory=rt_factory) == 50

        from anima_server.models.runtime import RuntimeConsolidationCursor

        with rt_factory() as db:
            rows = db.scalars(
                select(RuntimeConsolidationCursor).where(
                    RuntimeConsolidationCursor.user_id == 1,
                    RuntimeConsolidationCursor.thread_id.is_(None),
                )
            ).all()
        assert len(rows) == 1

    def test_cursor_only_advances_forward(self, rt_factory):
        """An older overlapping task committing a smaller message_id must not
        rewind the cursor (which would re-process already-handled messages)."""
        update_last_processed_message_id(
            1,
            thread_id=7,
            message_id=100,
            messages_processed=10,
            runtime_db_factory=rt_factory,
        )
        # A stale/older task reports a smaller id — must be ignored.
        update_last_processed_message_id(
            1,
            thread_id=7,
            message_id=40,
            messages_processed=2,
            runtime_db_factory=rt_factory,
        )
        assert get_last_processed_message_id(
            1, thread_id=7, runtime_db_factory=rt_factory) == 100

    @pytest.mark.asyncio()
    async def test_cursor_survives_task_run_pruning(self, rt_factory):
        """The cursor now lives in its own table, so pruning old completed
        task-run rows must not lose it (the old result_json cursor did)."""
        from anima_server.services.agent.eager_consolidation import (
            prune_old_background_task_runs,
        )

        update_last_processed_message_id(
            1,
            thread_id=10,
            message_id=99,
            messages_processed=4,
            runtime_db_factory=rt_factory,
        )
        # An old completed consolidation task-run that retention will drop.
        with rt_factory() as db:
            db.add(
                RuntimeBackgroundTaskRun(
                    user_id=1,
                    task_type="consolidation",
                    status="completed",
                    completed_at=datetime.now(UTC) - timedelta(days=90),
                    created_at=datetime.now(UTC) - timedelta(days=90),
                    result_json={"thread_id": 10, "last_processed_message_id": 99},
                )
            )
            db.commit()

        deleted = await prune_old_background_task_runs(runtime_db_factory=rt_factory)
        assert deleted == 1

        with rt_factory() as db:
            remaining = db.scalars(select(RuntimeBackgroundTaskRun)).all()
        assert remaining == []
        # Cursor is intact despite the task-run rows being gone.
        assert get_last_processed_message_id(
            1, thread_id=10, runtime_db_factory=rt_factory) == 99

    @pytest.mark.asyncio()
    async def test_consolidation_task_records_latest_runtime_message_cursor(self, rt_factory):
        with rt_factory() as db:
            thread = RuntimeThread(user_id=1, status="active")
            db.add(thread)
            db.flush()
            first = RuntimeMessage(
                user_id=1,
                thread_id=thread.id,
                sequence_id=1,
                role="user",
                content_text="first",
            )
            second = RuntimeMessage(
                user_id=1,
                thread_id=thread.id,
                sequence_id=2,
                role="assistant",
                content_text="second",
            )
            db.add_all([first, second])
            db.commit()
            thread_id = thread.id
            expected_message_id = second.id

        with patch(
            "anima_server.services.agent.soul_writer.run_soul_writer",
            new_callable=AsyncMock,
        ):
            result = await _task_consolidation(
                user_id=1,
                user_message="first",
                assistant_response="second",
                thread_id=thread_id,
                runtime_db_factory=rt_factory,
            )

        assert result == {
            "thread_id": thread_id,
            "last_processed_message_id": expected_message_id,
            "messages_processed": 2,
        }

    @pytest.mark.asyncio()
    async def test_consolidation_task_does_not_advance_cursor_with_candidate_backlog(
        self, rt_factory
    ):
        with rt_factory() as db:
            thread = RuntimeThread(user_id=1, status="active")
            db.add(thread)
            db.flush()
            first = RuntimeMessage(
                user_id=1,
                thread_id=thread.id,
                sequence_id=1,
                role="user",
                content_text="first",
            )
            second = RuntimeMessage(
                user_id=1,
                thread_id=thread.id,
                sequence_id=2,
                role="assistant",
                content_text="second",
            )
            db.add_all([first, second])
            db.flush()
            db.add_all(
                [
                    MemoryCandidate(
                        user_id=1,
                        content="processed fact",
                        category="fact",
                        importance=3,
                        source="extractor",
                        source_message_ids=[first.id],
                        content_hash="processed",
                        status="promoted",
                        processed_at=datetime.now(UTC),
                    ),
                    MemoryCandidate(
                        user_id=1,
                        content="queued fact",
                        category="fact",
                        importance=3,
                        source="extractor",
                        source_message_ids=[second.id],
                        content_hash="queued",
                        status="extracted",
                    ),
                ]
            )
            db.commit()
            thread_id = thread.id

        with patch(
            "anima_server.services.agent.soul_writer.run_soul_writer",
            new_callable=AsyncMock,
        ):
            result = await _task_consolidation(
                user_id=1,
                user_message="first",
                assistant_response="second",
                thread_id=thread_id,
                runtime_db_factory=rt_factory,
            )

        assert result == {
            "thread_id": thread_id,
            "last_processed_message_id": None,
            "messages_processed": 0,
        }

    @pytest.mark.asyncio()
    async def test_consolidation_task_does_not_advance_cursor_with_extraction_failure_backlog(
        self, rt_factory
    ):
        with rt_factory() as db:
            thread = RuntimeThread(user_id=1, status="active")
            db.add(thread)
            db.flush()
            message = RuntimeMessage(
                user_id=1,
                thread_id=thread.id,
                sequence_id=1,
                role="user",
                content_text="remember this",
            )
            db.add(message)
            db.flush()
            db.add(
                MemoryExtractionFailure(
                    user_id=1,
                    source_message_ids=[message.id],
                    user_message_preview="remember this",
                    assistant_response_preview=None,
                    failure_reason="temporary extraction failure",
                    status="failed",
                    retry_count=0,
                )
            )
            db.commit()
            thread_id = thread.id

        with patch(
            "anima_server.services.agent.soul_writer.run_soul_writer",
            new_callable=AsyncMock,
        ):
            result = await _task_consolidation(
                user_id=1,
                user_message="remember this",
                assistant_response="",
                thread_id=thread_id,
                runtime_db_factory=rt_factory,
            )

        assert result == {
            "thread_id": thread_id,
            "last_processed_message_id": None,
            "messages_processed": 0,
        }


# ── Orchestrator integration ─────────────────────────────────────────


class TestRunSleeptimeAgents:
    @pytest.mark.asyncio()
    async def test_parallel_tasks_all_run(self, db_factory, rt_factory):
        """All five parallel tasks should create RuntimeBackgroundTaskRun records."""
        with (
            patch(
                "anima_server.services.agent.sleep_agent._task_consolidation",
                new_callable=AsyncMock,
                return_value={"ok": True},
            ),
            patch(
                "anima_server.services.agent.sleep_agent._task_embedding_backfill",
                new_callable=AsyncMock,
                return_value={"ok": True},
            ),
            patch(
                "anima_server.services.agent.sleep_agent._task_graph_ingestion",
                new_callable=AsyncMock,
                return_value={"ok": True},
            ),
            patch(
                "anima_server.services.agent.sleep_agent._task_heat_decay",
                new_callable=AsyncMock,
                return_value={"ok": True},
            ),
            patch(
                "anima_server.services.agent.sleep_agent._task_episode_gen",
                new_callable=AsyncMock,
                return_value={"ok": True},
            ),
            patch(
                "anima_server.services.agent.sleep_agent._task_reparse_pending_documents",
                new_callable=AsyncMock,
                return_value="no documents pending reparse",
            ),
            patch(
                "anima_server.services.agent.sleep_agent._should_run_expensive",
                return_value=False,
            ),
            patch(
                "anima_server.services.agent.sleep_tasks._should_run_deep_monologue",
                return_value=False,
            ),
            patch(
                "anima_server.services.agent.sleep_tasks._should_run_latent_decay",
                return_value=False,
            ),
            patch(
                "anima_server.services.agent.companion.get_companion",
                return_value=None,
            ),
        ):
            run_ids = await run_sleeptime_agents(
                user_id=1,
                user_message="hello",
                assistant_response="hi",
                db_factory=db_factory,
                runtime_db_factory=rt_factory,
            )

        assert len(run_ids) == 8
        task_types = {r.split(":")[0] for r in run_ids}
        assert task_types == {
            "consolidation",
            "embedding_backfill",
            "graph_ingestion",
            "heat_decay",
            "foresight_lifecycle",
            "episode_gen",
            "knowledge_autocompile",
            "document_reparse",
        }

        with rt_factory() as db:
            runs = list(db.scalars(select(RuntimeBackgroundTaskRun)).all())
            assert len(runs) == 8
            assert all(r.status == "completed" for r in runs)


class TestGraphIngestionTask:
    @pytest.mark.asyncio()
    async def test_scaffold_mode_uses_rule_ingestion(self, db_factory, monkeypatch):
        monkeypatch.setattr(
            knowledge_graph_module.settings,
            "agent_provider",
            "scaffold",
            raising=False,
        )

        with db_factory() as db:
            user = User(
                username="sleep-kg",
                password_hash="not-used",
                display_name="Sleep KG",
            )
            db.add(user)
            db.commit()
            user_id = user.id

        result = await _task_graph_ingestion(
            user_id=user_id,
            user_message="I work at Anthropic.",
            assistant_response="",
            db_factory=db_factory,
        )

        assert result["entities"] >= 2
        assert result["relations"] >= 1
        assert result["pruned"] == 0

        with db_factory() as db:
            relations = list(db.scalars(select(KGRelation)).all())
            assert len(relations) == 1
            assert relations[0].relation_type == "works_at"

    @pytest.mark.asyncio()
    async def test_blank_turn_skips_ingestion(self, db_factory, monkeypatch):
        """A manual /sleep passes empty turn text — graph ingestion must skip
        the entity-extraction LLM rather than run it on an empty prompt (a
        useless billable call that could persist hallucinated entities)."""

        async def _boom(*args, **kwargs):
            raise AssertionError(
                "ingest_conversation_graph should not run on blank turn text"
            )

        monkeypatch.setattr(
            "anima_server.services.agent.knowledge_graph.ingest_conversation_graph",
            _boom,
        )

        result = await _task_graph_ingestion(
            user_id=1,
            user_message="",
            assistant_response="   ",
            db_factory=db_factory,
        )
        assert result == {"entities": 0, "relations": 0, "pruned": 0}


# ── Auto-reparse task ─────────────────────────────────────────────────


class TestReparsePendingDocumentsTask:
    """_task_reparse_pending_documents — closes the loop on preview/legacy
    documents once the parsing pack is ready. Patches ``parsing_pack_ready``,
    ``list_reparse_candidates`` and ``reparse_document`` at their
    sleep_agent module import site (imported at module scope there, not
    lazily inside the task, precisely so these tests can patch them)."""

    @pytest.mark.asyncio()
    async def test_skips_when_policy_off(self, rt_factory, monkeypatch):
        from anima_server.config import settings

        monkeypatch.setattr(settings, "document_auto_reparse", "off")
        with patch(
            "anima_server.services.agent.sleep_agent.parsing_pack_ready"
        ) as pack_ready, patch(
            "anima_server.services.agent.sleep_agent.list_reparse_candidates"
        ) as candidates:
            result = await _task_reparse_pending_documents(
                user_id=1,
                runtime_db_factory=rt_factory,
            )

        pack_ready.assert_not_called()
        candidates.assert_not_called()
        assert isinstance(result, str)
        assert "disabled" in result

    @pytest.mark.asyncio()
    async def test_skips_when_pack_not_ready(self, rt_factory, monkeypatch):
        from anima_server.config import settings

        monkeypatch.setattr(settings, "document_auto_reparse", "on")
        with patch(
            "anima_server.services.agent.sleep_agent.parsing_pack_ready",
            return_value=False,
        ), patch(
            "anima_server.services.agent.sleep_agent.list_reparse_candidates"
        ) as candidates, patch(
            "anima_server.services.agent.sleep_agent.reparse_document"
        ) as reparse_fn:
            result = await _task_reparse_pending_documents(
                user_id=1,
                runtime_db_factory=rt_factory,
            )

        candidates.assert_not_called()
        reparse_fn.assert_not_called()
        assert isinstance(result, str)
        assert "not ready" in result

    @pytest.mark.asyncio()
    async def test_processes_at_most_budget_candidates(self, rt_factory, monkeypatch):
        from anima_server.config import settings
        from anima_server.services.documents.reparse import ReparseResult

        monkeypatch.setattr(settings, "document_auto_reparse", "on")
        monkeypatch.setattr(settings, "document_auto_reparse_budget", 2)

        calls: list[int] = []

        def fake_reparse_document(runtime_db, *, user_id, document_id):
            calls.append(document_id)
            return ReparseResult(status="upgraded", chunk_count=3)

        with patch(
            "anima_server.services.agent.sleep_agent.parsing_pack_ready",
            return_value=True,
        ), patch(
            "anima_server.services.agent.sleep_agent.list_reparse_candidates",
            return_value=[1, 2, 3],
        ), patch(
            "anima_server.services.agent.sleep_agent.reparse_document",
            side_effect=fake_reparse_document,
        ):
            result = await _task_reparse_pending_documents(
                user_id=1,
                runtime_db_factory=rt_factory,
            )

        assert calls == [1, 2]
        assert "reparsed 2" in result

    @pytest.mark.asyncio()
    async def test_aborts_loop_on_parser_unavailable(self, rt_factory, monkeypatch):
        from anima_server.config import settings
        from anima_server.services.documents.reparse import ReparseResult

        monkeypatch.setattr(settings, "document_auto_reparse", "on")
        monkeypatch.setattr(settings, "document_auto_reparse_budget", 5)

        calls: list[int] = []

        def fake_reparse_document(runtime_db, *, user_id, document_id):
            calls.append(document_id)
            return ReparseResult(status="parser_unavailable", detail="not installed")

        with patch(
            "anima_server.services.agent.sleep_agent.parsing_pack_ready",
            return_value=True,
        ), patch(
            "anima_server.services.agent.sleep_agent.list_reparse_candidates",
            return_value=[1, 2, 3],
        ), patch(
            "anima_server.services.agent.sleep_agent.reparse_document",
            side_effect=fake_reparse_document,
        ):
            result = await _task_reparse_pending_documents(
                user_id=1,
                runtime_db_factory=rt_factory,
            )

        # The loop stops at the first unhealthy status instead of burning
        # the rest of the budget on a parser that's clearly sick.
        assert calls == [1]
        assert "reparsed 0" in result
        assert "pending" in result

    @pytest.mark.asyncio()
    async def test_upgraded_unembedded_rolls_back_and_aborts(
        self, rt_factory, monkeypatch
    ):
        """FINDING A: reparse_document() calls replace_document_chunks()
        (resets the document to non-indexed and deletes old chunk vectors)
        *before* embedding. If the embedding backend is down, the document
        comes back "upgraded_unembedded" — docling-quality chunks written,
        but the document left non-indexed because nothing embedded. If the
        cycle committed that, a previously-searchable indexed preview
        document becomes invisible to search AND drops out of
        list_reparse_candidates (which requires status=="indexed") —
        orphaned forever. The cycle must instead roll back (discarding the
        flushed-but-uncommitted reset so the original indexed preview
        survives), not count the document as reparsed, and abort the rest
        of the budget since an unavailable embedding backend affects every
        candidate identically."""
        from anima_server.config import settings
        from anima_server.services.documents.reparse import ReparseResult

        monkeypatch.setattr(settings, "document_auto_reparse", "on")
        monkeypatch.setattr(settings, "document_auto_reparse_budget", 5)

        calls: list[int] = []
        rollback_calls: list[int] = []
        commit_calls: list[int] = []

        def fake_reparse_document(runtime_db, *, user_id, document_id):
            calls.append(document_id)
            return ReparseResult(status="upgraded_unembedded", chunk_count=3)

        def spying_factory(*args, **kwargs):
            session = rt_factory(*args, **kwargs)
            original_rollback = session.rollback
            original_commit = session.commit

            def _spy_rollback():
                rollback_calls.append(1)
                return original_rollback()

            def _spy_commit():
                commit_calls.append(1)
                return original_commit()

            session.rollback = _spy_rollback
            session.commit = _spy_commit
            return session

        with patch(
            "anima_server.services.agent.sleep_agent.parsing_pack_ready",
            return_value=True,
        ), patch(
            "anima_server.services.agent.sleep_agent.list_reparse_candidates",
            return_value=[1, 2, 3],
        ), patch(
            "anima_server.services.agent.sleep_agent.reparse_document",
            side_effect=fake_reparse_document,
        ):
            result = await _task_reparse_pending_documents(
                user_id=1,
                runtime_db_factory=spying_factory,
            )

        # Only the first candidate is attempted — the cycle aborts instead
        # of repeating the same unembedded dance for candidates 2 and 3.
        assert calls == [1]
        # The flushed replace_document_chunks reset must be rolled back, not
        # committed — committing it is exactly the orphaning bug.
        assert rollback_calls == [1]
        assert commit_calls == []
        assert "reparsed 0" in result
        assert "embeddings unavailable" in result
        assert "pending" in result

    @pytest.mark.asyncio()
    async def test_parse_degraded_skips_and_continues(self, rt_factory, monkeypatch):
        """A per-document Docling crash (parse_degraded) must NOT abort the
        cycle: the offending file sorts first by id and stays an indexed
        preview candidate, so aborting on it would head-of-line-block every
        document behind it forever. The loop skips it and keeps going."""
        from anima_server.config import settings
        from anima_server.services.documents.reparse import ReparseResult

        monkeypatch.setattr(settings, "document_auto_reparse", "on")
        monkeypatch.setattr(settings, "document_auto_reparse_budget", 5)

        calls: list[int] = []

        def fake_reparse_document(runtime_db, *, user_id, document_id):
            calls.append(document_id)
            if document_id == 1:
                return ReparseResult(status="parse_degraded", detail="docling crashed")
            return ReparseResult(status="upgraded", chunk_count=3)

        with patch(
            "anima_server.services.agent.sleep_agent.parsing_pack_ready",
            return_value=True,
        ), patch(
            "anima_server.services.agent.sleep_agent.list_reparse_candidates",
            return_value=[1, 2, 3],
        ), patch(
            "anima_server.services.agent.sleep_agent.reparse_document",
            side_effect=fake_reparse_document,
        ):
            result = await _task_reparse_pending_documents(
                user_id=1,
                runtime_db_factory=rt_factory,
            )

        # All three candidates attempted — the degraded doc #1 did NOT block
        # #2 and #3 (the head-of-line-blocking bug).
        assert calls == [1, 2, 3]
        assert "reparsed 2" in result
        assert "could not be parsed" in result

    @pytest.mark.asyncio()
    async def test_upgraded_still_commits_and_counts(self, rt_factory, monkeypatch):
        """Regression guard alongside the upgraded_unembedded fix above: a
        normal successful upgrade must still commit and count as reparsed —
        the fix must not accidentally make every reparse roll back."""
        from anima_server.config import settings
        from anima_server.services.documents.reparse import ReparseResult

        monkeypatch.setattr(settings, "document_auto_reparse", "on")
        monkeypatch.setattr(settings, "document_auto_reparse_budget", 5)

        commit_calls: list[int] = []
        rollback_calls: list[int] = []

        def fake_reparse_document(runtime_db, *, user_id, document_id):
            return ReparseResult(status="upgraded", chunk_count=3)

        def spying_factory(*args, **kwargs):
            session = rt_factory(*args, **kwargs)
            original_rollback = session.rollback
            original_commit = session.commit

            def _spy_rollback():
                rollback_calls.append(1)
                return original_rollback()

            def _spy_commit():
                commit_calls.append(1)
                return original_commit()

            session.rollback = _spy_rollback
            session.commit = _spy_commit
            return session

        with patch(
            "anima_server.services.agent.sleep_agent.parsing_pack_ready",
            return_value=True,
        ), patch(
            "anima_server.services.agent.sleep_agent.list_reparse_candidates",
            return_value=[1, 2],
        ), patch(
            "anima_server.services.agent.sleep_agent.reparse_document",
            side_effect=fake_reparse_document,
        ):
            result = await _task_reparse_pending_documents(
                user_id=1,
                runtime_db_factory=spying_factory,
            )

        assert commit_calls == [1, 1]
        assert rollback_calls == []
        assert result == "reparsed 2 documents"

    @pytest.mark.asyncio()
    async def test_session_lifecycle_stays_on_worker_thread(
        self, rt_factory, monkeypatch
    ):
        """The runtime session must be opened, used, and committed entirely
        inside the asyncio.to_thread worker (matching soul_writer's
        convention) — never opened on the event loop and handed across
        threads, which would also pin a pooled connection to the loop for
        the full duration of a minutes-long Docling parse."""
        import threading

        from anima_server.config import settings
        from anima_server.services.documents.reparse import ReparseResult

        monkeypatch.setattr(settings, "document_auto_reparse", "on")

        loop_thread = threading.get_ident()
        factory_threads: list[int] = []
        reparse_threads: list[int] = []

        def spying_factory(*args, **kwargs):
            factory_threads.append(threading.get_ident())
            return rt_factory(*args, **kwargs)

        def fake_reparse_document(runtime_db, *, user_id, document_id):
            reparse_threads.append(threading.get_ident())
            return ReparseResult(status="upgraded", chunk_count=1)

        with patch(
            "anima_server.services.agent.sleep_agent.parsing_pack_ready",
            return_value=True,
        ), patch(
            "anima_server.services.agent.sleep_agent.list_reparse_candidates",
            return_value=[1],
        ), patch(
            "anima_server.services.agent.sleep_agent.reparse_document",
            side_effect=fake_reparse_document,
        ):
            result = await _task_reparse_pending_documents(
                user_id=1,
                runtime_db_factory=spying_factory,
            )

        assert result == "reparsed 1 documents"
        # The session was created off the event loop, on the same worker
        # thread that ran the reparse itself.
        assert factory_threads and reparse_threads
        assert factory_threads[0] != loop_thread
        assert factory_threads[0] == reparse_threads[0]

    @pytest.mark.asyncio()
    async def test_not_found_excluded_from_pending_count(
        self, rt_factory, monkeypatch
    ):
        """A concurrently-deleted document (not_found) is gone, not pending —
        it must not inflate the '(M pending)' arithmetic in the summary."""
        from anima_server.config import settings
        from anima_server.services.documents.reparse import ReparseResult

        monkeypatch.setattr(settings, "document_auto_reparse", "on")
        monkeypatch.setattr(settings, "document_auto_reparse_budget", 5)

        statuses = {1: "upgraded", 2: "not_found", 3: "upgraded"}

        def fake_reparse_document(runtime_db, *, user_id, document_id):
            return ReparseResult(status=statuses[document_id])

        with patch(
            "anima_server.services.agent.sleep_agent.parsing_pack_ready",
            return_value=True,
        ), patch(
            "anima_server.services.agent.sleep_agent.list_reparse_candidates",
            return_value=[1, 2, 3],
        ), patch(
            "anima_server.services.agent.sleep_agent.reparse_document",
            side_effect=fake_reparse_document,
        ):
            result = await _task_reparse_pending_documents(
                user_id=1,
                runtime_db_factory=rt_factory,
            )

        assert result == "reparsed 2 documents"

    @pytest.mark.asyncio()
    async def test_registered_in_always_run_list(self, db_factory, rt_factory):
        """The orchestrator's always-run group includes document_reparse
        and records a RuntimeBackgroundTaskRun for it."""
        with (
            patch(
                "anima_server.services.agent.sleep_agent._task_consolidation",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "anima_server.services.agent.sleep_agent._task_embedding_backfill",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "anima_server.services.agent.sleep_agent._task_graph_ingestion",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "anima_server.services.agent.sleep_agent._task_heat_decay",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "anima_server.services.agent.sleep_agent._task_episode_gen",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "anima_server.services.agent.sleep_agent._should_run_expensive",
                return_value=False,
            ),
            patch(
                "anima_server.services.agent.sleep_tasks._should_run_deep_monologue",
                return_value=False,
            ),
            patch(
                "anima_server.services.agent.companion.get_companion",
                return_value=None,
            ),
        ):
            run_ids = await run_sleeptime_agents(
                user_id=1,
                user_message="hello",
                assistant_response="hi",
                db_factory=db_factory,
                runtime_db_factory=rt_factory,
            )

        assert any(r.startswith("document_reparse:") for r in run_ids)
        with rt_factory() as db:
            run = db.scalar(
                select(RuntimeBackgroundTaskRun).where(
                    RuntimeBackgroundTaskRun.task_type == "document_reparse"
                )
            )
            assert run is not None
            assert run.status == "completed"


# ── RuntimeBackgroundTaskRun model ───────────────────────────────────


class TestBackgroundTaskRunModel:
    def test_default_status(self, rt_factory):
        with rt_factory() as db:
            run = RuntimeBackgroundTaskRun(
                user_id=1,
                task_type="test",
            )
            db.add(run)
            db.commit()
            db.refresh(run)
            assert run.status == "pending"
            assert run.created_at is not None
