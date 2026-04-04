"""Tests for F5 — Async sleep-time agent orchestrator."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from anima_server.db.base import Base
from anima_server.db.runtime_base import RuntimeBase
from anima_server.models.runtime import RuntimeBackgroundTaskRun
from anima_server.services.agent.sleep_agent import (
    _issue_background_task,
    _should_run_expensive,
    _task_episode_gen,
    get_last_processed_message_id,
    run_sleeptime_agents,
    update_last_processed_message_id,
)
from sqlalchemy import create_engine, event, select
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
        """With force=True, expensive tasks run even with no heat."""
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
                user_id=1,
                user_message="test",
                assistant_response="resp",
                db_factory=db_factory,
                runtime_db_factory=rt_factory,
                force=True,
            )

        # With force=True, contradiction_scan, profile_synthesis run.
        # Deep monologue respects 24h throttle (mocked True here).
        assert any("contradiction_scan" in r for r in run_ids)
        assert any("profile_synthesis" in r for r in run_ids)
        assert any("deep_monologue" in r for r in run_ids)


# ── Heat gating ──────────────────────────────────────────────────────


class TestHeatGating:
    def test_no_items_means_no_expensive(self, db_factory):
        with db_factory() as db:
            assert _should_run_expensive(db, user_id=999) is False


# ── Restart cursor ───────────────────────────────────────────────────


class TestRestartCursor:
    def test_no_runs_returns_none(self, rt_factory):
        assert get_last_processed_message_id(
            1, runtime_db_factory=rt_factory) is None

    def test_round_trip(self, rt_factory):
        # Seed a completed consolidation run
        with rt_factory() as db:
            run = RuntimeBackgroundTaskRun(
                user_id=1,
                task_type="consolidation",
                status="completed",
                completed_at=datetime.now(UTC),
                result_json={
                    "thread_id": 10,
                    "last_processed_message_id": 42,
                    "messages_processed": 5,
                },
            )
            db.add(run)
            db.commit()

        msg_id = get_last_processed_message_id(
            1, thread_id=10, runtime_db_factory=rt_factory)
        assert msg_id == 42

    def test_thread_scope_isolation(self, rt_factory):
        """Cursor for thread 10 should not match thread 20."""
        with rt_factory() as db:
            run = RuntimeBackgroundTaskRun(
                user_id=1,
                task_type="consolidation",
                status="completed",
                completed_at=datetime.now(UTC),
                result_json={
                    "thread_id": 10,
                    "last_processed_message_id": 42,
                    "messages_processed": 5,
                },
            )
            db.add(run)
            db.commit()

        # Thread 20 has no cursor
        assert get_last_processed_message_id(
            1, thread_id=20, runtime_db_factory=rt_factory) is None
        # Thread 10 has the cursor
        assert get_last_processed_message_id(
            1, thread_id=10, runtime_db_factory=rt_factory) == 42

    def test_update_cursor(self, rt_factory):
        # Create a completed run first
        with rt_factory() as db:
            run = RuntimeBackgroundTaskRun(
                user_id=1,
                task_type="consolidation",
                status="completed",
                completed_at=datetime.now(UTC),
                result_json={
                    "thread_id": None,
                    "last_processed_message_id": 10,
                    "messages_processed": 3,
                },
            )
            db.add(run)
            db.commit()

        update_last_processed_message_id(
            1,
            thread_id=None,
            message_id=50,
            messages_processed=7,
            runtime_db_factory=rt_factory,
        )

        msg_id = get_last_processed_message_id(
            1, thread_id=None, runtime_db_factory=rt_factory)
        assert msg_id == 50


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

        assert len(run_ids) == 5
        task_types = {r.split(":")[0] for r in run_ids}
        assert task_types == {
            "consolidation",
            "embedding_backfill",
            "graph_ingestion",
            "heat_decay",
            "episode_gen",
        }

        with rt_factory() as db:
            runs = list(db.scalars(select(RuntimeBackgroundTaskRun)).all())
            assert len(runs) == 5
            assert all(r.status == "completed" for r in runs)


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
