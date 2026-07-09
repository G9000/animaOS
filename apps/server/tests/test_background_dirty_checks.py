"""ARH-007: dirty-checks for background cognition.

The contradiction scan re-bought identical LLM verdicts every cycle,
idle-lull reflection re-ran the full synthesis suite on unchanged inputs,
and emotional-pattern promotion scanned 50 signals on practically every
turn while one conversation could double-count toward a pattern.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from anima_server.db.base import Base
from anima_server.db.runtime_base import RuntimeBase
from anima_server.models import MemoryItem, User
from anima_server.models.runtime import RuntimeBackgroundTaskRun
from anima_server.models.runtime_memory import ContradictionCheck
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture()
def soul_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture()
def rt_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    RuntimeBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    yield factory
    engine.dispose()


def _make_user(soul_factory) -> int:
    with soul_factory() as db:
        user = User(username="dirty-checks", password_hash="x", display_name="DC")
        db.add(user)
        db.commit()
        return user.id


def _add_items(soul_factory, user_id: int, contents: list[str]) -> None:
    with soul_factory() as db:
        for content in contents:
            db.add(
                MemoryItem(
                    user_id=user_id,
                    content=content,
                    category="preference",
                    importance=3,
                    source="extraction",
                )
            )
        db.commit()


# --------------------------------------------------------------------------- #
# Contradiction verdict cache
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_contradiction_scan_does_not_rebuy_verdicts(
    soul_factory, rt_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from anima_server.services.agent import sleep_tasks

    user_id = _make_user(soul_factory)
    # Similar-but-not-duplicate pair (token overlap in the 0.3–0.95 band).
    _add_items(
        soul_factory,
        user_id,
        [
            "Likes green tea in the morning",
            "Likes black tea in the morning",
        ],
    )

    calls = 0

    async def counting_check(content_a: str, content_b: str):
        nonlocal calls
        calls += 1
        return {"verdict": "COMPATIBLE"}

    monkeypatch.setattr(sleep_tasks, "_check_contradiction", counting_check)

    found, _ = await sleep_tasks.scan_contradictions(
        user_id=user_id, db_factory=soul_factory, runtime_db_factory=rt_factory
    )
    assert found >= 1
    first_calls = calls
    assert first_calls >= 1

    # Same items, second scan: every verdict comes from the cache.
    await sleep_tasks.scan_contradictions(
        user_id=user_id, db_factory=soul_factory, runtime_db_factory=rt_factory
    )
    assert calls == first_calls

    with rt_factory() as rt_db:
        cached = rt_db.scalars(select(ContradictionCheck)).all()
    assert len(cached) == first_calls
    assert all(row.verdict == "COMPATIBLE" for row in cached)


@pytest.mark.asyncio
async def test_verdict_not_cached_when_soul_commit_fails(
    soul_factory, rt_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A verdict must be persisted only AFTER its resolution commits.  If the
    soul commit raises, the pair must stay un-cached so it is re-examined next
    cycle rather than being silently marked resolved with the fix lost."""
    from anima_server.services.agent import sleep_tasks

    user_id = _make_user(soul_factory)
    _add_items(
        soul_factory,
        user_id,
        [
            "Likes green tea in the morning",
            "Likes black tea in the morning",
        ],
    )

    async def conflict_check(content_a: str, content_b: str):
        return {"verdict": "CONFLICT", "action": "KEEP_SECOND", "merged": None}

    monkeypatch.setattr(sleep_tasks, "_check_contradiction", conflict_check)

    # Fail every soul commit so no resolution ever becomes durable.
    real_commit = Session.commit

    def boom(self):
        raise RuntimeError("commit failed")

    monkeypatch.setattr(Session, "commit", boom)

    with pytest.raises(RuntimeError):
        await sleep_tasks.scan_contradictions(
            user_id=user_id, db_factory=soul_factory, runtime_db_factory=rt_factory
        )

    monkeypatch.setattr(Session, "commit", real_commit)
    with rt_factory() as rt_db:
        assert rt_db.scalars(select(ContradictionCheck)).all() == []


@pytest.mark.asyncio
async def test_edited_item_invalidates_its_cached_pairs(
    soul_factory, rt_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from anima_server.services.agent import sleep_tasks

    user_id = _make_user(soul_factory)
    _add_items(
        soul_factory,
        user_id,
        [
            "Likes green tea in the morning",
            "Likes black tea in the morning",
        ],
    )

    calls = 0

    async def counting_check(content_a: str, content_b: str):
        nonlocal calls
        calls += 1
        return {"verdict": "COMPATIBLE"}

    monkeypatch.setattr(sleep_tasks, "_check_contradiction", counting_check)

    await sleep_tasks.scan_contradictions(
        user_id=user_id, db_factory=soul_factory, runtime_db_factory=rt_factory
    )
    first_calls = calls

    # Content change → new content hash → the pair is checked again.
    with soul_factory() as db:
        item = db.scalars(select(MemoryItem)).first()
        item.content = "Likes green tea in the evening now"
        db.commit()

    await sleep_tasks.scan_contradictions(
        user_id=user_id, db_factory=soul_factory, runtime_db_factory=rt_factory
    )
    assert calls > first_calls


# --------------------------------------------------------------------------- #
# Input-freshness gate
# --------------------------------------------------------------------------- #


def _record_completed_run(
    rt_factory, *, user_id: int, task_type: str, completed_at: datetime
) -> None:
    with rt_factory() as rt_db:
        rt_db.add(
            RuntimeBackgroundTaskRun(
                user_id=user_id,
                task_type=task_type,
                status="completed",
                completed_at=completed_at,
            )
        )
        rt_db.commit()


class TestInputFreshnessGate:
    def test_runs_when_no_previous_run(self, rt_factory) -> None:
        from anima_server.services.agent.sleep_agent import (
            _inputs_changed_since_last_run,
        )

        assert (
            _inputs_changed_since_last_run(
                user_id=1,
                task_type="profile_synthesis",
                latest_input_at=datetime.now(UTC),
                runtime_db_factory=rt_factory,
            )
            is True
        )

    def test_skips_when_inputs_older_than_last_run(self, rt_factory) -> None:
        from anima_server.services.agent.sleep_agent import (
            _inputs_changed_since_last_run,
        )

        now = datetime.now(UTC)
        _record_completed_run(
            rt_factory, user_id=1, task_type="profile_synthesis", completed_at=now
        )
        assert (
            _inputs_changed_since_last_run(
                user_id=1,
                task_type="profile_synthesis",
                latest_input_at=now - timedelta(hours=1),
                runtime_db_factory=rt_factory,
            )
            is False
        )

    def test_runs_when_inputs_newer_than_last_run(self, rt_factory) -> None:
        from anima_server.services.agent.sleep_agent import (
            _inputs_changed_since_last_run,
        )

        now = datetime.now(UTC)
        _record_completed_run(
            rt_factory,
            user_id=1,
            task_type="profile_synthesis",
            completed_at=now - timedelta(hours=1),
        )
        assert (
            _inputs_changed_since_last_run(
                user_id=1,
                task_type="profile_synthesis",
                latest_input_at=now,
                runtime_db_factory=rt_factory,
            )
            is True
        )

    def test_skips_when_no_inputs_exist(self, rt_factory) -> None:
        from anima_server.services.agent.sleep_agent import (
            _inputs_changed_since_last_run,
        )

        assert (
            _inputs_changed_since_last_run(
                user_id=1,
                task_type="profile_synthesis",
                latest_input_at=None,
                runtime_db_factory=rt_factory,
            )
            is False
        )


@pytest.mark.asyncio
async def test_forced_run_does_not_bypass_contradiction_heat_gate(
    soul_factory, rt_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Idle-lull reflection forces the expensive group, but the contradiction
    scan (the dominant recurring LLM cost) still honors the heat gate."""
    from anima_server.services.agent import sleep_agent

    user_id = _make_user(soul_factory)
    _add_items(soul_factory, user_id, ["Likes green tea"])

    issued: list[str] = []

    async def recording_issue(*, user_id, task_type, task_fn, **kwargs) -> str:
        issued.append(task_type)
        return f"{task_type}:0"

    monkeypatch.setattr(sleep_agent, "_issue_background_task", recording_issue)
    monkeypatch.setattr(sleep_agent, "_should_run_expensive", lambda db, uid: False)

    await sleep_agent.run_sleeptime_agents(
        user_id=user_id,
        user_message="hi",
        assistant_response="hello",
        db_factory=soul_factory,
        runtime_db_factory=rt_factory,
        force=True,
    )

    assert "contradiction_scan" not in issued
    # Other expensive tasks still run under force (inputs are fresh:
    # a memory item exists and no completed runs are recorded).
    assert "profile_synthesis" in issued
    assert "pattern_synthesis" not in issued  # no episodes → no inputs


@pytest.mark.asyncio
async def test_unchanged_inputs_skip_synthesis_tasks(
    soul_factory, rt_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from anima_server.services.agent import sleep_agent

    user_id = _make_user(soul_factory)
    _add_items(soul_factory, user_id, ["Likes green tea"])
    # Synthesis already ran after the newest input.
    _record_completed_run(
        rt_factory,
        user_id=user_id,
        task_type="profile_synthesis",
        completed_at=datetime.now(UTC) + timedelta(seconds=1),
    )

    issued: list[str] = []

    async def recording_issue(*, user_id, task_type, task_fn, **kwargs) -> str:
        issued.append(task_type)
        return f"{task_type}:0"

    monkeypatch.setattr(sleep_agent, "_issue_background_task", recording_issue)
    monkeypatch.setattr(sleep_agent, "_should_run_expensive", lambda db, uid: True)

    await sleep_agent.run_sleeptime_agents(
        user_id=user_id,
        user_message="hi",
        assistant_response="hello",
        db_factory=soul_factory,
        runtime_db_factory=rt_factory,
    )

    assert "profile_synthesis" not in issued


# --------------------------------------------------------------------------- #
# Emotional-pattern promotion gate + signal dedupe
# --------------------------------------------------------------------------- #


class TestEmotionalPromotionGate:
    def _add_signals(self, rt_factory, user_id: int, count: int) -> None:
        from anima_server.models.runtime_consciousness import CurrentEmotion

        with rt_factory() as rt_db:
            for _ in range(count):
                rt_db.add(
                    CurrentEmotion(
                        user_id=user_id,
                        emotion="curious",
                        confidence=0.8,
                        evidence_type="linguistic",
                        trajectory="stable",
                    )
                )
            rt_db.commit()

    def test_no_patterns_requires_min_signals(self, soul_factory, rt_factory) -> None:
        from anima_server.services.agent.emotional_patterns import (
            should_promote_emotional_patterns,
        )

        user_id = _make_user(soul_factory)
        self._add_signals(rt_factory, user_id, 2)
        with soul_factory() as soul_db, rt_factory() as rt_db:
            assert (
                should_promote_emotional_patterns(
                    soul_db=soul_db, pg_db=rt_db, user_id=user_id
                )
                is False
            )

        self._add_signals(rt_factory, user_id, 1)
        with soul_factory() as soul_db, rt_factory() as rt_db:
            assert (
                should_promote_emotional_patterns(
                    soul_db=soul_db, pg_db=rt_db, user_id=user_id
                )
                is True
            )

    def test_distinct_emotions_do_not_trip_gate(
        self, soul_factory, rt_factory
    ) -> None:
        """Three signals of three *different* emotions must not fire the gate:
        promotion needs one emotion to recur, so firing here would scan 50
        SQLCipher rows, promote nothing, and (last_observed unchanged) re-fire
        every turn."""
        from anima_server.models.runtime_consciousness import CurrentEmotion
        from anima_server.services.agent.emotional_patterns import (
            should_promote_emotional_patterns,
        )

        user_id = _make_user(soul_factory)
        with rt_factory() as rt_db:
            for emotion in ("curious", "joyful", "anxious"):
                rt_db.add(
                    CurrentEmotion(
                        user_id=user_id,
                        emotion=emotion,
                        confidence=0.8,
                        evidence_type="linguistic",
                        trajectory="stable",
                    )
                )
            rt_db.commit()

        with soul_factory() as soul_db, rt_factory() as rt_db:
            assert (
                should_promote_emotional_patterns(
                    soul_db=soul_db, pg_db=rt_db, user_id=user_id
                )
                is False
            )

        # A third signal of one repeated emotion crosses the threshold.
        self._add_signals(rt_factory, user_id, 3)  # 3× "curious"
        with soul_factory() as soul_db, rt_factory() as rt_db:
            assert (
                should_promote_emotional_patterns(
                    soul_db=soul_db, pg_db=rt_db, user_id=user_id
                )
                is True
            )

    def test_existing_patterns_gate_on_new_signals(
        self, soul_factory, rt_factory
    ) -> None:
        from anima_server.models.soul_consciousness import CoreEmotionalPattern
        from anima_server.services.agent.emotional_patterns import (
            should_promote_emotional_patterns,
        )

        user_id = _make_user(soul_factory)
        with soul_factory() as soul_db:
            soul_db.add(
                CoreEmotionalPattern(
                    user_id=user_id,
                    pattern="Tends toward curious",
                    dominant_emotion="curious",
                    frequency=3,
                    confidence=0.8,
                    first_observed=datetime.now(UTC),
                    last_observed=datetime.now(UTC) + timedelta(seconds=1),
                )
            )
            soul_db.commit()

        # No new signals since last promotion → skip.
        with soul_factory() as soul_db, rt_factory() as rt_db:
            assert (
                should_promote_emotional_patterns(
                    soul_db=soul_db, pg_db=rt_db, user_id=user_id
                )
                is False
            )


def test_reflection_signal_dedupes_within_window(rt_factory) -> None:
    from anima_server.services.agent.emotional_intelligence import (
        record_emotional_signal,
    )

    with rt_factory() as rt_db:
        first = record_emotional_signal(
            rt_db, user_id=1, emotion="curious", confidence=0.8
        )
        assert first is not None
        rt_db.commit()

        # Reflection-derived duplicate of the same conversation's emotion.
        duplicate = record_emotional_signal(
            rt_db,
            user_id=1,
            emotion="curious",
            confidence=0.8,
            dedupe_window_minutes=15,
        )
        assert duplicate is None

        # A different emotion within the window still records.
        other = record_emotional_signal(
            rt_db,
            user_id=1,
            emotion="excited",
            confidence=0.8,
            dedupe_window_minutes=15,
        )
        assert other is not None
