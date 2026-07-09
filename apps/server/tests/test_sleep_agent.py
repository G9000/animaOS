"""Tests for F5 — Async sleep-time agent orchestrator."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
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

    async def _noop_backfill(user_id, db_factory=None):
        return None

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
        embedding_contract, "mark_user_reembed_complete", lambda *a, **k: None
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

        assert len(run_ids) == 6
        task_types = {r.split(":")[0] for r in run_ids}
        assert task_types == {
            "consolidation",
            "embedding_backfill",
            "graph_ingestion",
            "heat_decay",
            "foresight_lifecycle",
            "episode_gen",
        }

        with rt_factory() as db:
            runs = list(db.scalars(select(RuntimeBackgroundTaskRun)).all())
            assert len(runs) == 6
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
