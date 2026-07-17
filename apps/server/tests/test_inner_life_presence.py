"""Tests for the IL2 presence tick loop and offline catch-up."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from anima_server.db.runtime_base import RuntimeBase
from anima_server.models import runtime as _runtime_models  # noqa: F401
from anima_server.models import runtime_consciousness as _runtime_consciousness_models  # noqa: F401
from anima_server.models.runtime import RuntimeRun, RuntimeThread
from anima_server.models.runtime_consciousness import PresenceCatchup
from anima_server.services.agent.inner_life.affect import (
    DEFAULT_AFFECT_CONFIG,
    AffectState,
    _circadian_particular_arousal,
    arousal_threshold_crossing_time,
    relax,
    update_allostatic_shift,
)
from anima_server.services.agent.inner_life.catchup import (
    apply_offline_catchup,
    has_eligible_night_window,
)
from anima_server.services.agent.inner_life.presence import (
    run_presence_tick,
    system_zoneinfo,
)
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


def test_long_turn_in_flight_is_skipped_despite_stale_last_message(
    tmp_path: Path,
) -> None:
    # A turn running longer than the active window leaves last_message_at
    # stale while the run is still generating; the tick must still treat
    # the user as active (skip by design, not just FOR UPDATE lock-safety).
    factory = _factory(tmp_path)
    now = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    original_updated_at = now - timedelta(hours=6)

    with factory() as db:
        save_affect_state(
            db,
            user_id=1,
            state=AffectState(
                valence=0.0, arousal=0.9, energy=0.5, updated_at=original_updated_at
            ),
        )
        thread = RuntimeThread(
            user_id=1,
            status="active",
            last_message_at=now - timedelta(minutes=10),  # outside the 120 s window
        )
        db.add(thread)
        db.flush()
        db.add(
            RuntimeRun(
                thread_id=thread.id,
                user_id=1,
                provider="anthropic",
                model="test-model",
                mode="chat",
                status="running",
                started_at=now - timedelta(seconds=30),  # fresh in-flight run
            )
        )
        db.commit()

    result = run_presence_tick(factory, now=now)

    assert result.users_ticked == 0
    assert result.users_skipped_active == 1

    with factory() as db:
        state = get_affect_state(db, user_id=1)
    assert state.updated_at == original_updated_at  # untouched mid-turn


def test_stale_running_run_does_not_block_tick(tmp_path: Path) -> None:
    # Crashed-run recovery: a run stuck in status "running" longer than
    # presence_run_stale_seconds must not exclude the user from presence
    # forever — the tick proceeds.
    factory = _factory(tmp_path)
    now = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    original_updated_at = now - timedelta(hours=6)

    with factory() as db:
        save_affect_state(
            db,
            user_id=1,
            state=AffectState(
                valence=0.0, arousal=0.9, energy=0.5, updated_at=original_updated_at
            ),
        )
        thread = RuntimeThread(
            user_id=1,
            status="active",
            last_message_at=now - timedelta(hours=3),
        )
        db.add(thread)
        db.flush()
        db.add(
            RuntimeRun(
                thread_id=thread.id,
                user_id=1,
                provider="anthropic",
                model="test-model",
                mode="chat",
                status="running",
                started_at=now - timedelta(hours=2),  # older than the 1800 s cap
            )
        )
        db.commit()

    result = run_presence_tick(factory, now=now)

    assert result.users_ticked == 1
    assert result.users_skipped_active == 0

    with factory() as db:
        state = get_affect_state(db, user_id=1)
    assert state.updated_at == now


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


def _compose_per_minute(
    start: AffectState, start_at: datetime, minutes: int
) -> AffectState:
    composed = start
    t = start_at
    for _ in range(minutes):
        t = t + timedelta(seconds=60)
        composed = relax(composed, t, DEFAULT_AFFECT_CONFIG)
        composed = update_allostatic_shift(composed, 1.0 / 60.0, DEFAULT_AFFECT_CONFIG)
    return composed


# One tick quantum of allostatic divergence: per-minute composition
# quantizes the threshold crossing at 60 s boundaries (the crossing minute
# can flip from accumulate (+1/60 h) to drain (-0.5/60 h)), so exact
# catch-up may differ from composition by up to 1.5 * (60 s in hours).
_TICK_QUANTUM_HOURS = 1.5 * (60.0 / 3600.0)


def test_catchup_equivalence_over_three_weeks(tmp_path: Path) -> None:
    # Starts ABOVE the allostatic threshold (0.9 > 0.7) so the catch-up
    # exercises the threshold-crossing path. Valence/arousal/energy are
    # closed-form and must match per-minute composition to 1e-6; the
    # allostatic accumulator is threshold-indicator state and is
    # quantization-dependent by construction (composition itself quantizes
    # the crossing at 60 s boundaries), so it must match within one tick
    # quantum.
    factory = _factory(tmp_path)
    start_updated_at = datetime(2026, 1, 1, tzinfo=UTC)
    now = start_updated_at + timedelta(days=21)
    start = AffectState(valence=0.8, arousal=0.9, energy=0.1, updated_at=start_updated_at)

    with factory() as db:
        save_affect_state(db, user_id=1, state=start)
        db.commit()

    apply_offline_catchup(factory, now=now, min_gap_seconds=0)

    composed = _compose_per_minute(start, start_updated_at, 30_240)

    with factory() as db:
        state = get_affect_state(db, user_id=1)

    assert abs(state.valence - composed.valence) < 1e-6
    assert abs(state.arousal - composed.arousal) < 1e-6
    assert abs(state.energy - composed.energy) < 1e-6
    assert abs(state.high_arousal_hours - composed.high_arousal_hours) <= _TICK_QUANTUM_HOURS
    assert abs(state.arousal_baseline_shift - composed.arousal_baseline_shift) < 1e-6


def test_catchup_equivalence_below_threshold_regime(tmp_path: Path) -> None:
    # Arousal starts (and stays) below the allostatic threshold for the
    # whole gap, so no crossing quantization exists and BOTH regimes —
    # relaxation and allostatic drain — must match per-minute composition
    # to 1e-6 (the pure-exponential regime).
    factory = _factory(tmp_path)
    start_updated_at = datetime(2026, 1, 1, tzinfo=UTC)
    now = start_updated_at + timedelta(days=21)
    start = AffectState(valence=0.8, arousal=0.5, energy=0.1, updated_at=start_updated_at)

    with factory() as db:
        save_affect_state(db, user_id=1, state=start)
        db.commit()

    apply_offline_catchup(factory, now=now, min_gap_seconds=0)

    composed = _compose_per_minute(start, start_updated_at, 30_240)

    with factory() as db:
        state = get_affect_state(db, user_id=1)

    assert abs(state.valence - composed.valence) < 1e-6
    assert abs(state.arousal - composed.arousal) < 1e-6
    assert abs(state.energy - composed.energy) < 1e-6
    assert abs(state.high_arousal_hours - composed.high_arousal_hours) < 1e-6
    assert abs(state.arousal_baseline_shift - composed.arousal_baseline_shift) < 1e-6


def test_catchup_4h_gap_from_high_arousal_is_piecewise_exact(tmp_path: Path) -> None:
    # Reviewer regression: a 4 h gap starting at arousal 0.9 crosses the
    # allostatic threshold mid-gap (t* ~ 2.5-3 h). A single-branch
    # application keyed off the END-of-gap arousal would pure-drain the
    # whole gap (high_arousal_hours -> 0), diverging from composition by
    # ~2+ accumulator-hours. Catch-up must instead accumulate over [0, t*]
    # and drain over [t*, gap]: exactly t* - 0.5 * (gap - t*).
    factory = _factory(tmp_path)
    start_updated_at = datetime(2026, 1, 1, 5, 0, tzinfo=UTC)
    now = start_updated_at + timedelta(hours=4)
    start = AffectState(valence=0.0, arousal=0.9, energy=0.5, updated_at=start_updated_at)

    with factory() as db:
        save_affect_state(db, user_id=1, state=start)
        db.commit()

    apply_offline_catchup(factory, now=now, min_gap_seconds=0)

    t_star = arousal_threshold_crossing_time(start, DEFAULT_AFFECT_CONFIG)
    assert 0.0 < t_star < 4.0
    expected_high = t_star - 0.5 * (4.0 - t_star)

    with factory() as db:
        state = get_affect_state(db, user_id=1)

    assert state.high_arousal_hours == pytest.approx(expected_high, abs=1e-6)
    # The pre-fix behavior (pure drain over the whole gap) would clamp to 0;
    # the exact value (~1.5-2.5 h) varies with circadian phase.
    assert state.high_arousal_hours > 1.0
    # And composition agrees within one tick quantum.
    composed = _compose_per_minute(start, start_updated_at, 240)
    assert abs(state.high_arousal_hours - composed.high_arousal_hours) <= _TICK_QUANTUM_HOURS


def test_catchup_decays_baseline_shift_over_gap(tmp_path: Path) -> None:
    # Reviewer regression: the shift must not be frozen over the gap. With
    # start arousal 0.5, shift 0.05, load 60 h and a 21-day gap, per-minute
    # composition snaps the shift to f(load)=0.0125 at the first tick,
    # tracks it linearly down to 0 as the load drains through the 48 h
    # sustained threshold (t48 = 24 h), then decays it exponentially —
    # ending at ~0. The pre-fix catch-up relaxed arousal with the shift
    # frozen at 0.05, landing exactly 0.05 above composition. Arousal
    # tolerance is 1e-4 (not 1e-6): discrete composition quantizes shift
    # updates at tick boundaries, so in the nonzero-shift regime the
    # composed reference itself carries O(tick) shift-feed-through noise —
    # 1e-6 is not a coherent target here. Zero-shift regimes keep 1e-6
    # (see the equivalence tests above).
    factory = _factory(tmp_path)
    start_updated_at = datetime(2026, 1, 1, tzinfo=UTC)
    now = start_updated_at + timedelta(days=21)
    start = AffectState(
        valence=0.0,
        arousal=0.5,
        energy=0.5,
        updated_at=start_updated_at,
        arousal_baseline_shift=0.05,
        high_arousal_hours=60.0,
    )

    with factory() as db:
        save_affect_state(db, user_id=1, state=start)
        db.commit()

    apply_offline_catchup(factory, now=now, min_gap_seconds=0)

    composed = _compose_per_minute(start, start_updated_at, 30_240)

    with factory() as db:
        state = get_affect_state(db, user_id=1)

    assert abs(state.arousal - composed.arousal) <= 1e-4  # pre-fix diff: 0.050000
    assert abs(state.arousal_baseline_shift - composed.arousal_baseline_shift) <= 1e-4
    assert state.arousal_baseline_shift == pytest.approx(0.0, abs=1e-4)  # decayed away
    assert abs(state.high_arousal_hours - composed.high_arousal_hours) <= _TICK_QUANTUM_HOURS
    assert abs(state.valence - composed.valence) < 1e-6
    assert abs(state.energy - composed.energy) < 1e-6


def test_crossing_solver_uses_segment_consistent_shift_dynamics(
    tmp_path: Path,
) -> None:
    # PR #104 regression: a boundary state whose STORED shift (0.05)
    # disagrees with the law value f(load)=0 at load exactly 48 h. During
    # [0, t*] load accumulates, so the shift law snaps to f(load) ~ 0 —
    # but a frozen-shift solve keeps arousal 0.05 higher and overestimates
    # t* by ~0.6 h, over-accumulating ~0.9 h vs composition. The solver
    # must bisect A(t) composed over the same accumulation-regime shift
    # segments the relaxation itself applies. (State likely unreachable
    # via real dynamics — the shift is pinned to f(load) while draining —
    # but stored state is untrusted input.)
    factory = _factory(tmp_path)
    start_updated_at = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)  # local noon
    now = start_updated_at + timedelta(hours=8)
    start = AffectState(
        valence=0.0,
        arousal=0.9,
        energy=0.5,
        updated_at=start_updated_at,
        arousal_baseline_shift=0.05,
        high_arousal_hours=48.0,
    )

    with factory() as db:
        save_affect_state(db, user_id=1, state=start)
        db.commit()

    apply_offline_catchup(factory, now=now, min_gap_seconds=0)

    composed = _compose_per_minute(start, start_updated_at, 480)

    with factory() as db:
        state = get_affect_state(db, user_id=1)

    assert abs(state.arousal - composed.arousal) <= 1e-4
    assert abs(state.high_arousal_hours - composed.high_arousal_hours) <= _TICK_QUANTUM_HOURS
    assert abs(state.arousal_baseline_shift - composed.arousal_baseline_shift) <= 1e-4

    # RED evidence: the pre-fix frozen-shift solve (relax() freezes the
    # stored shift, so bisecting it reproduces the old solver exactly)
    # violates the accumulator bound by ~0.9 h.
    lo, hi = 0.0, DEFAULT_AFFECT_CONFIG.tau_arousal_hours
    while relax(start, start_updated_at + timedelta(hours=hi)).arousal > 0.7:
        hi *= 2.0
    while hi - lo > 1e-9:
        mid = 0.5 * (lo + hi)
        if relax(start, start_updated_at + timedelta(hours=mid)).arousal > 0.7:
            lo = mid
        else:
            hi = mid
    frozen_t_star = hi
    prefix_high = 48.0 + frozen_t_star - 0.5 * (8.0 - frozen_t_star)
    assert abs(prefix_high - composed.high_arousal_hours) > _TICK_QUANTUM_HOURS
    # And the law-consistent solver lands well below the frozen estimate.
    assert frozen_t_star - arousal_threshold_crossing_time(start) > 10 * _TICK_QUANTUM_HOURS


def test_arousal_threshold_crossing_time_is_consistent_with_relax() -> None:
    # The solver must agree with relax(): arousal at t* equals the
    # threshold, and a state already at/below threshold has t* = 0.
    start = AffectState(
        valence=0.0, arousal=0.9, energy=0.5,
        updated_at=datetime(2026, 1, 1, 5, 0, tzinfo=UTC),
    )
    t_star = arousal_threshold_crossing_time(start, DEFAULT_AFFECT_CONFIG)
    at_crossing = relax(
        start, start.updated_at + timedelta(hours=t_star), DEFAULT_AFFECT_CONFIG
    )
    assert at_crossing.arousal == pytest.approx(
        DEFAULT_AFFECT_CONFIG.allostatic_arousal_threshold, abs=1e-6
    )

    calm = AffectState(
        valence=0.0, arousal=0.5, energy=0.5,
        updated_at=datetime(2026, 1, 1, 5, 0, tzinfo=UTC),
    )
    assert arousal_threshold_crossing_time(calm, DEFAULT_AFFECT_CONFIG) == 0.0


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


def test_gap_entirely_inside_one_night_counts_overlap() -> None:
    tz = UTC
    # 00:30-05:00 lies wholly inside the 00:00-06:00 window: 4.5 h -> eligible.
    inside_long = has_eligible_night_window(
        datetime(2026, 1, 1, 0, 30, tzinfo=tz), datetime(2026, 1, 1, 5, 0, tzinfo=tz)
    )
    # 00:30-04:00 is also wholly inside but only 3.5 h -> not eligible.
    inside_short = has_eligible_night_window(
        datetime(2026, 1, 1, 0, 30, tzinfo=tz), datetime(2026, 1, 1, 4, 0, tzinfo=tz)
    )
    assert inside_long is True
    assert inside_short is False


def test_night_window_across_dst_spring_forward_uses_real_zone() -> None:
    # US spring-forward night (2026-03-08, America/New_York): the wall
    # clock jumps 02:00 EST -> 03:00 EDT, so the local 00:00-06:00 window
    # spans only 5 REAL hours. A gap covering local 01:30 -> 06:00 that
    # night holds 4.5 WALL hours but only 3.5 real idle hours — below the
    # 4 h floor. A real IANA zone must say "not eligible"; the frozen
    # fixed offset a datetime.now().astimezone() would have captured in
    # winter (-05:00) misplaces the transition and wrongly says "eligible".
    zone = ZoneInfo("America/New_York")
    start_wall = datetime(2026, 3, 8, 1, 30)
    end_wall = datetime(2026, 3, 8, 6, 0)

    assert (
        has_eligible_night_window(
            start_wall.replace(tzinfo=zone), end_wall.replace(tzinfo=zone)
        )
        is False
    )

    frozen_winter_offset = timezone(timedelta(hours=-5))
    assert (
        has_eligible_night_window(
            start_wall.replace(tzinfo=frozen_winter_offset),
            end_wall.replace(tzinfo=frozen_winter_offset),
        )
        is True
    )


def test_catchup_dst_spanning_gap_defers_dream_correctly(tmp_path: Path) -> None:
    # Catch-up level: the same borderline spring-forward gap, with the
    # zone injected via the tz seam. 01:30 EST -> 07:00 EDT is 4.5 real
    # hours (gap passes min-gap), but only 3.5 of them fall inside the
    # real local 00:00-06:00 night window -> no deferred dream. Under the
    # frozen winter offset the same instants read as 01:30 -> 06:00 local
    # (4.5 h overlap) and would wrongly defer one.
    zone = ZoneInfo("America/New_York")
    start_utc = datetime(2026, 3, 8, 6, 30, tzinfo=UTC)  # 01:30 EST
    now_utc = datetime(2026, 3, 8, 11, 0, tzinfo=UTC)  # 07:00 EDT

    factory = _factory(tmp_path, "dst_zone.db")
    with factory() as db:
        save_affect_state(
            db,
            user_id=1,
            state=AffectState(valence=0.0, arousal=0.4, energy=0.5, updated_at=start_utc),
        )
        db.commit()

    results = apply_offline_catchup(factory, now=now_utc, tz=zone, min_gap_seconds=600)
    assert len(results) == 1
    assert results[0].dream_deferred is False

    # Contrast: the frozen fixed winter offset gets it wrong.
    frozen_factory = _factory(tmp_path, "dst_frozen.db")
    with frozen_factory() as db:
        save_affect_state(
            db,
            user_id=1,
            state=AffectState(valence=0.0, arousal=0.4, energy=0.5, updated_at=start_utc),
        )
        db.commit()

    frozen = apply_offline_catchup(
        frozen_factory,
        now=now_utc,
        tz=timezone(timedelta(hours=-5)),
        min_gap_seconds=600,
    )
    assert len(frozen) == 1
    assert frozen[0].dream_deferred is True


def test_system_zoneinfo_prefers_tz_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # system_zoneinfo is lru_cached (so the fixed-offset fallback warning
    # logs once per process, not per tick); clear around the env override.
    monkeypatch.setenv("TZ", "America/New_York")
    system_zoneinfo.cache_clear()
    try:
        zone = system_zoneinfo()
        assert isinstance(zone, ZoneInfo)
        assert zone.key == "America/New_York"
    finally:
        system_zoneinfo.cache_clear()


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
