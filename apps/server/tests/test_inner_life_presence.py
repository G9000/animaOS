"""Tests for the IL2 presence tick loop and offline catch-up."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from anima_server.db.runtime_base import RuntimeBase
from anima_server.models import runtime as _runtime_models  # noqa: F401
from anima_server.models import runtime_consciousness as _runtime_consciousness_models  # noqa: F401
from anima_server.models.runtime import RuntimeThread
from anima_server.models.runtime_consciousness import PresenceCatchup
from anima_server.services.agent.inner_life.affect import (
    DEFAULT_AFFECT_CONFIG,
    AffectState,
    _circadian_particular_arousal,
    relax,
    update_allostatic_shift,
)
from anima_server.services.agent.inner_life.catchup import (
    apply_offline_catchup,
    has_eligible_night_window,
)
from anima_server.services.agent.inner_life.presence import run_presence_tick
from anima_server.services.agent.inner_life.store import get_affect_state, save_affect_state
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

# ---------------------------------------------------------------------------
# Fixtures — file-backed SQLite, mirroring test_inner_life_affect.py's style,
# since run_presence_tick/apply_offline_catchup open a *new* session per
# user via the factory (real cross-session behavior, not just an in-memory
# identity map).
# ---------------------------------------------------------------------------


def _factory(tmp_path: Path, name: str = "runtime.db") -> sessionmaker:
    _engine, factory = _factory_with_engine(tmp_path, name)
    return factory


def _factory_with_engine(
    tmp_path: Path, name: str = "runtime.db"
) -> tuple[Engine, sessionmaker]:
    engine: Engine = create_engine(f"sqlite:///{tmp_path / name}")
    RuntimeBase.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return engine, factory


# ---------------------------------------------------------------------------
# Presence tick
# ---------------------------------------------------------------------------


def test_tick_relaxes_and_persists_idle_user(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    now = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    start = AffectState(
        valence=0.0, arousal=0.9, energy=0.5, updated_at=now - timedelta(hours=6)
    )

    with factory() as db:
        save_affect_state(db, user_id=1, state=start)
        db.commit()

    result = run_presence_tick(factory, now=now)

    assert result.users_ticked == 1
    assert result.users_skipped_active == 0

    with factory() as db:
        state = get_affect_state(db, user_id=1)

    assert state.updated_at == now
    # Arousal started well above the circadian equilibrium band; six hours
    # of relaxation must move it down, toward equilibrium, not further up.
    assert state.arousal < start.arousal


def test_active_user_is_skipped(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    now = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    original_updated_at = now - timedelta(hours=6)
    start = AffectState(valence=0.0, arousal=0.9, energy=0.5, updated_at=original_updated_at)

    with factory() as db:
        save_affect_state(db, user_id=1, state=start)
        db.add(
            RuntimeThread(
                user_id=1,
                status="active",
                last_message_at=now - timedelta(seconds=30),
            )
        )
        db.commit()

    result = run_presence_tick(factory, now=now)

    assert result.users_ticked == 0
    assert result.users_skipped_active == 1

    with factory() as db:
        state = get_affect_state(db, user_id=1)

    assert state.updated_at == original_updated_at
    assert state.arousal == pytest.approx(0.9)


def test_per_user_failure_does_not_abort_sweep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = _factory(tmp_path)
    now = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    earlier = now - timedelta(hours=1)

    with factory() as db:
        save_affect_state(
            db, user_id=1, state=AffectState(valence=0.0, arousal=0.5, energy=0.5, updated_at=earlier)
        )
        save_affect_state(
            db, user_id=2, state=AffectState(valence=0.0, arousal=0.5, energy=0.5, updated_at=earlier)
        )
        db.commit()

    from anima_server.services.agent.inner_life import presence as presence_module

    original_save = presence_module.save_affect_state

    def failing_save(db: object, *, user_id: int, state: object) -> None:
        if user_id == 1:
            raise RuntimeError("simulated failure for user 1")
        original_save(db, user_id=user_id, state=state)

    monkeypatch.setattr(presence_module, "save_affect_state", failing_save)

    result = run_presence_tick(factory, now=now)

    # Only user 2 counts as ticked; user 1's failure is swallowed, not raised.
    assert result.users_ticked == 1
    assert result.users_skipped_active == 0

    with factory() as db:
        state1 = get_affect_state(db, user_id=1)
        state2 = get_affect_state(db, user_id=2)

    assert state1.updated_at == earlier  # untouched by the failed write
    assert state2.updated_at == now  # ticked normally


def test_tick_advances_allostatic_accumulation(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    now = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    start = AffectState(
        valence=0.0,
        arousal=0.95,
        energy=0.5,
        updated_at=now - timedelta(hours=2),
        high_arousal_hours=0.0,
        arousal_baseline_shift=0.0,
    )

    with factory() as db:
        save_affect_state(db, user_id=1, state=start)
        db.commit()

    run_presence_tick(factory, now=now)

    expected_relaxed = relax(start, now, DEFAULT_AFFECT_CONFIG)
    expected = update_allostatic_shift(expected_relaxed, 2.0, DEFAULT_AFFECT_CONFIG)

    with factory() as db:
        state = get_affect_state(db, user_id=1)

    assert state.high_arousal_hours > 0.0
    assert state.high_arousal_hours == pytest.approx(expected.high_arousal_hours, abs=1e-9)


def test_tick_uses_local_hour_not_utc_hour_for_circadian_phase(tmp_path: Path) -> None:
    # Closes IL-001's deferred "true local-time resolution" item: `now`
    # is local-aware (UTC+8), and after a long-enough gap for the transient
    # to fully die out, arousal must sit on the LOCAL-hour equilibrium, not
    # the UTC-hour one — a 07:00 UTC / 15:00 local instant sits near the
    # circadian peak locally and nowhere near it in UTC.
    factory = _factory(tmp_path)
    local_tz = timezone(timedelta(hours=8))
    now_utc = datetime(2026, 1, 1, 7, 0, tzinfo=UTC)
    now_local = now_utc.astimezone(local_tz)
    assert now_local.hour == 15

    start = AffectState(valence=0.0, arousal=0.35, energy=0.5, updated_at=now_utc - timedelta(hours=48))

    with factory() as db:
        save_affect_state(db, user_id=1, state=start)
        db.commit()

    run_presence_tick(factory, now=now_local)

    with factory() as db:
        state = get_affect_state(db, user_id=1)

    expected_local_equilibrium = _circadian_particular_arousal(15.0, 0.0, DEFAULT_AFFECT_CONFIG)
    expected_utc_equilibrium = _circadian_particular_arousal(7.0, 0.0, DEFAULT_AFFECT_CONFIG)

    assert state.arousal == pytest.approx(expected_local_equilibrium, abs=1e-4)
    assert abs(state.arousal - expected_utc_equilibrium) > 1e-3


# ---------------------------------------------------------------------------
# Offline catch-up
# ---------------------------------------------------------------------------


def test_catchup_equivalence_over_three_weeks(tmp_path: Path) -> None:
    # Arousal starts (and stays) below the allostatic threshold for the
    # whole gap, so the allostatic branch is identical in both the
    # composed per-minute loop and the one-shot catch-up; this isolates
    # the closed-form relaxation equivalence at the catch-up level (IL1's
    # own equivalence test exercises `relax` directly — this exercises the
    # `apply_offline_catchup` wiring on top of it).
    factory = _factory(tmp_path)
    start_updated_at = datetime(2026, 1, 1, tzinfo=UTC)
    now = start_updated_at + timedelta(days=21)
    start = AffectState(valence=0.8, arousal=0.5, energy=0.1, updated_at=start_updated_at)

    with factory() as db:
        save_affect_state(db, user_id=1, state=start)
        db.commit()

    apply_offline_catchup(factory, now=now, min_gap_seconds=0)

    composed = start
    t = start_updated_at
    for _ in range(30_240):
        t = t + timedelta(seconds=60)
        composed = relax(composed, t, DEFAULT_AFFECT_CONFIG)
        composed = update_allostatic_shift(composed, 1.0 / 60.0, DEFAULT_AFFECT_CONFIG)

    with factory() as db:
        state = get_affect_state(db, user_id=1)

    assert abs(state.valence - composed.valence) < 1e-6
    assert abs(state.arousal - composed.arousal) < 1e-6
    assert abs(state.energy - composed.energy) < 1e-6
    assert abs(state.high_arousal_hours - composed.high_arousal_hours) < 1e-6
    assert abs(state.arousal_baseline_shift - composed.arousal_baseline_shift) < 1e-6


def test_catchup_below_min_gap_is_skipped(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    start = AffectState(valence=0.2, arousal=0.4, energy=0.5, updated_at=now - timedelta(seconds=300))

    with factory() as db:
        save_affect_state(db, user_id=1, state=start)
        db.commit()

    results = apply_offline_catchup(factory, now=now, min_gap_seconds=600)

    assert results == []

    with factory() as db:
        state = get_affect_state(db, user_id=1)
        audit_count = db.scalar(select(func.count()).select_from(PresenceCatchup))

    assert state.updated_at == start.updated_at
    assert audit_count == 0


def test_catchup_writes_one_audit_row_with_correct_gap_and_dream_deferred(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    start_updated_at = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    now = start_updated_at + timedelta(days=10)
    start = AffectState(valence=0.0, arousal=0.4, energy=0.5, updated_at=start_updated_at)

    with factory() as db:
        save_affect_state(db, user_id=1, state=start)
        db.commit()

    results = apply_offline_catchup(factory, now=now, min_gap_seconds=600)

    expected_gap = (now - start_updated_at).total_seconds()
    assert len(results) == 1
    assert results[0].user_id == 1
    assert results[0].gap_seconds == pytest.approx(expected_gap)
    assert results[0].dream_deferred is True

    with factory() as db:
        rows = db.scalars(select(PresenceCatchup).where(PresenceCatchup.user_id == 1)).all()

    assert len(rows) == 1
    assert rows[0].gap_seconds == pytest.approx(expected_gap)
    assert rows[0].dream_deferred is True
    assert rows[0].components == "affect,allostatic"


def test_catchup_gap_with_no_night_window_sets_dream_deferred_false(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    # 09:00 to 15:00: entirely daytime, no overlap with any 00:00-06:00 span.
    start_updated_at = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    now = start_updated_at + timedelta(hours=6)
    start = AffectState(valence=0.0, arousal=0.4, energy=0.5, updated_at=start_updated_at)

    with factory() as db:
        save_affect_state(db, user_id=1, state=start)
        db.commit()

    results = apply_offline_catchup(factory, now=now, min_gap_seconds=600)

    assert len(results) == 1
    assert results[0].dream_deferred is False


def test_has_eligible_night_window_requires_four_hours_of_overlap() -> None:
    tz = UTC
    # Only 3h of a night window overlap (03:00-06:00) — below the 4h floor.
    short = has_eligible_night_window(
        datetime(2026, 1, 1, 3, 0, tzinfo=tz), datetime(2026, 1, 1, 9, 0, tzinfo=tz)
    )
    # A full 6h night window (00:00-06:00) comfortably inside the gap.
    full = has_eligible_night_window(
        datetime(2025, 12, 31, 20, 0, tzinfo=tz), datetime(2026, 1, 1, 9, 0, tzinfo=tz)
    )
    assert short is False
    assert full is True


def test_catchup_module_has_no_llm_references() -> None:
    import inspect

    from anima_server.services.agent.inner_life import catchup as catchup_module

    source = inspect.getsource(catchup_module)
    assert "create_llm" not in source
    assert "create_extraction_llm" not in source
    assert "import" not in "\n".join(
        line for line in source.splitlines() if "llm" in line.lower()
    )


def test_presence_module_has_no_llm_references() -> None:
    import inspect

    from anima_server.services.agent.inner_life import presence as presence_module

    source = inspect.getsource(presence_module)
    assert "create_llm" not in source
    assert "create_extraction_llm" not in source
    assert "import" not in "\n".join(
        line for line in source.splitlines() if "llm" in line.lower()
    )


def test_catchup_per_user_work_does_not_explode_with_user_count(tmp_path: Path) -> None:
    # Statement-count harness: SQLAlchemy's `before_cursor_execute` hook
    # lets us assert catch-up's per-user DB work stays linear (one SELECT
    # + one flush/insert per user) instead of skipping this check.
    engine, factory = _factory_with_engine(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    num_users = 5

    with factory() as db:
        for user_id in range(1, num_users + 1):
            save_affect_state(
                db,
                user_id=user_id,
                state=AffectState(
                    valence=0.0, arousal=0.4, energy=0.5, updated_at=now - timedelta(days=1)
                ),
            )
        db.commit()

    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    results = apply_offline_catchup(factory, now=now, min_gap_seconds=600)

    assert len(results) == num_users

    non_txn_statements = [
        s
        for s in statements
        if not s.strip().upper().startswith(("BEGIN", "COMMIT", "ROLLBACK", "PRAGMA"))
    ]
    # 1 discovery SELECT + up to ~4 statements/user (select, savepoint
    # release, update, audit insert) — comfortably linear; an O(n^2) or
    # N+1-across-users bug would blow well past this bound at 5 users.
    assert len(non_txn_statements) <= 1 + 4 * num_users


# ---------------------------------------------------------------------------
# Eval reset
# ---------------------------------------------------------------------------


def test_eval_reset_clears_presence_catchup_rows(tmp_path: Path) -> None:
    from anima_server.services.eval_reset import _reset_runtime_state

    factory = _factory(tmp_path)

    with factory() as db:
        db.add_all(
            [
                PresenceCatchup(
                    user_id=1, gap_seconds=700.0, components="affect,allostatic", dream_deferred=False
                ),
                PresenceCatchup(
                    user_id=2, gap_seconds=800.0, components="affect,allostatic", dream_deferred=True
                ),
            ]
        )
        db.commit()

        deleted: dict[str, int] = {}
        _reset_runtime_state(db, user_id=1, deleted=deleted)
        db.commit()

        assert deleted["presence_catchup"] == 1
        remaining = db.scalars(select(PresenceCatchup)).all()

    assert len(remaining) == 1
    assert remaining[0].user_id == 2
