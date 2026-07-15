"""Tests for the IL1 affect state vector: dynamics, persistence, wiring."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from anima_server.db.runtime_base import RuntimeBase
from anima_server.models import runtime_consciousness as _runtime_consciousness_models  # noqa: F401
from anima_server.services.agent.inner_life.affect import (
    AffectConfig,
    AffectState,
    apply_turn_deltas,
    circadian_arousal_baseline,
    relax,
    render_affect,
    update_allostatic_shift,
)
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@contextmanager
def _runtime_session() -> Generator[Session, None, None]:
    engine: Engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    RuntimeBase.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        RuntimeBase.metadata.drop_all(bind=engine)
        engine.dispose()


def _state(
    *,
    valence: float = 0.0,
    arousal: float = 0.35,
    energy: float = 0.6,
    updated_at: datetime | None = None,
    arousal_baseline_shift: float = 0.0,
    high_arousal_hours: float = 0.0,
) -> AffectState:
    return AffectState(
        valence=valence,
        arousal=arousal,
        energy=energy,
        updated_at=updated_at or datetime(2026, 1, 1, tzinfo=UTC),
        arousal_baseline_shift=arousal_baseline_shift,
        high_arousal_hours=high_arousal_hours,
    )


# --- Determinism -------------------------------------------------------


def test_determinism_same_event_sequence_same_trajectory() -> None:
    start = _state(valence=0.1, arousal=0.4, energy=0.5)
    events = [
        (0.1, 0.05, -0.02, timedelta(hours=1)),
        (-0.2, 0.1, 0.03, timedelta(hours=3)),
        (0.05, -0.1, 0.01, timedelta(hours=12)),
    ]

    def run() -> AffectState:
        s = start
        now = s.updated_at
        for v, a, e, dt in events:
            now = now + dt
            s = relax(s, now)
            s = apply_turn_deltas(s, v, a, e)
        return s

    result1 = run()
    result2 = run()

    assert result1 == result2


# --- Closed-form equivalence --------------------------------------------


def test_relax_closed_form_equivalence_over_three_weeks() -> None:
    # Isolate the pure exponential-decay math from circadian modulation:
    # with amplitude=0 the arousal baseline is constant regardless of hour,
    # so composing many small relax() calls must equal one large call
    # exactly (relaxation toward a FIXED baseline is a semigroup: applying
    # exp(-dt1/tau) then exp(-dt2/tau) equals exp(-(dt1+dt2)/tau)). Testing
    # circadian modulation itself is a separate, dedicated test below.
    config = AffectConfig(circadian_amplitude=0.0)
    start = _state(valence=0.8, arousal=0.9, energy=0.1)

    one_shot = relax(start, start.updated_at + timedelta(days=21), config)

    composed = start
    now = start.updated_at
    for _ in range(30_240):
        now = now + timedelta(seconds=60)
        composed = relax(composed, now, config)

    assert abs(one_shot.valence - composed.valence) < 1e-6
    assert abs(one_shot.arousal - composed.arousal) < 1e-6
    assert abs(one_shot.energy - composed.energy) < 1e-6


def test_relax_moves_toward_baseline() -> None:
    start = _state(valence=0.9, arousal=0.35, energy=0.6)
    later = relax(start, start.updated_at + timedelta(hours=36))
    assert later.valence < start.valence
    assert later.valence > 0.0


def test_relax_no_time_elapsed_is_noop() -> None:
    start = _state(valence=0.5, arousal=0.5, energy=0.5)
    same = relax(start, start.updated_at)
    assert same.valence == start.valence
    assert same.arousal == start.arousal
    assert same.energy == start.energy


# --- Clamping ------------------------------------------------------------


def test_turn_deltas_clamped_to_015() -> None:
    start = _state(valence=0.0, arousal=0.5, energy=0.5)
    result = apply_turn_deltas(start, 10.0, -10.0, 10.0)
    assert result.valence == pytest.approx(0.15)
    assert result.arousal == pytest.approx(0.35)
    assert result.energy == pytest.approx(0.65)


def test_components_never_exit_bounds_under_extreme_sequence() -> None:
    state = _state(valence=0.0, arousal=0.0, energy=0.0)
    now = state.updated_at
    for _ in range(200):
        state = apply_turn_deltas(state, 5.0, 5.0, 5.0)
        now = now + timedelta(minutes=1)
        state = relax(state, now)
        state = update_allostatic_shift(state, 1.0 / 60.0)
        assert -1.0 <= state.valence <= 1.0
        assert 0.0 <= state.arousal <= 1.0
        assert 0.0 <= state.energy <= 1.0
        assert 0.0 <= state.arousal_baseline_shift <= 0.05
        assert 0.0 <= state.high_arousal_hours <= 96.0

    state2 = _state(valence=0.0, arousal=1.0, energy=1.0)
    now2 = state2.updated_at
    for _ in range(200):
        state2 = apply_turn_deltas(state2, -5.0, -5.0, -5.0)
        now2 = now2 + timedelta(minutes=1)
        state2 = relax(state2, now2)
        assert -1.0 <= state2.valence <= 1.0
        assert 0.0 <= state2.arousal <= 1.0
        assert 0.0 <= state2.energy <= 1.0


# --- Circadian -------------------------------------------------------------


def test_circadian_trough_midline_peak_ordering() -> None:
    trough = circadian_arousal_baseline(3.0)
    midline = circadian_arousal_baseline(9.0)
    peak = circadian_arousal_baseline(15.0)
    assert trough < midline < peak


def test_circadian_amplitude_is_01() -> None:
    peak = circadian_arousal_baseline(15.0)
    trough = circadian_arousal_baseline(3.0)
    assert peak == pytest.approx(0.45)
    assert trough == pytest.approx(0.25)
    assert (peak - trough) == pytest.approx(0.2)


# --- Allostatic load ---------------------------------------------------


def test_allostatic_shift_rises_after_48h_sustained_high_arousal() -> None:
    state = _state(arousal=0.9, arousal_baseline_shift=0.0, high_arousal_hours=0.0)
    for _ in range(50):
        state = update_allostatic_shift(state, 1.0)

    assert state.high_arousal_hours > 48.0
    assert 0.0 < state.arousal_baseline_shift <= 0.05


def test_allostatic_shift_stays_zero_under_48h() -> None:
    state = _state(arousal=0.9, arousal_baseline_shift=0.0, high_arousal_hours=0.0)
    for _ in range(10):
        state = update_allostatic_shift(state, 1.0)

    assert state.high_arousal_hours == pytest.approx(10.0)
    assert state.arousal_baseline_shift == 0.0


def test_allostatic_shift_decays_when_arousal_normal() -> None:
    state = _state(arousal=0.2, arousal_baseline_shift=0.05, high_arousal_hours=60.0)
    decayed = update_allostatic_shift(state, 24.0)

    assert decayed.high_arousal_hours == 0.0
    assert decayed.arousal_baseline_shift < 0.05
    assert decayed.arousal_baseline_shift > 0.0


# --- Rendering -----------------------------------------------------------


def test_render_affect_never_contains_digits() -> None:
    for v, a in [(-0.8, 0.9), (0.0, 0.5), (0.9, 0.1), (0.9, 0.9)]:
        phrase = render_affect(_state(valence=v, arousal=a))
        assert not any(ch.isdigit() for ch in phrase)


def test_render_affect_distinct_for_distinct_states() -> None:
    calm_positive = render_affect(_state(valence=0.8, arousal=0.1))
    agitated_negative = render_affect(_state(valence=-0.8, arousal=0.9))
    assert calm_positive != agitated_negative


def test_render_affect_reflects_drift_direction() -> None:
    previous = _state(valence=-0.5)
    current = _state(valence=0.5)
    rising = render_affect(current, previous=previous)
    falling = render_affect(previous, previous=current)
    assert "brightening" in rising
    assert "dimming" in falling


def test_render_affect_stable_without_drift_info() -> None:
    phrase = render_affect(_state(valence=0.0, arousal=0.5))
    assert "holding steady" in phrase


# --- No-LLM guarantee ------------------------------------------------------


def test_affect_module_has_no_llm_references() -> None:
    import inspect

    from anima_server.services.agent.inner_life import affect as affect_module

    source = inspect.getsource(affect_module)
    assert "create_llm" not in source
    assert "create_extraction_llm" not in source
    assert "import" not in "\n".join(
        line for line in source.splitlines() if "llm" in line.lower()
    )


# --- Store roundtrip ---------------------------------------------------


def test_store_roundtrip_save_and_load() -> None:
    from anima_server.services.agent.inner_life.store import (
        get_affect_state,
        save_affect_state,
    )

    with _runtime_session() as runtime_db:
        state = _state(valence=0.3, arousal=0.6, energy=0.4)
        save_affect_state(runtime_db, user_id=1, state=state)
        runtime_db.commit()

        loaded = get_affect_state(runtime_db, user_id=1)
        assert loaded.valence == pytest.approx(0.3)
        assert loaded.arousal == pytest.approx(0.6)
        assert loaded.energy == pytest.approx(0.4)


def test_store_first_read_creates_default_row() -> None:
    from anima_server.services.agent.inner_life.affect import DEFAULT_AFFECT_CONFIG
    from anima_server.services.agent.inner_life.store import get_affect_state

    with _runtime_session() as runtime_db:
        state = get_affect_state(runtime_db, user_id=42)
        runtime_db.commit()

        assert state.valence == pytest.approx(DEFAULT_AFFECT_CONFIG.baseline_valence)
        assert state.energy == pytest.approx(DEFAULT_AFFECT_CONFIG.baseline_energy)

        again = get_affect_state(runtime_db, user_id=42)
        assert again.valence == state.valence


def test_store_missing_runtime_db_returns_default() -> None:
    from anima_server.services.agent.inner_life.affect import DEFAULT_AFFECT_CONFIG
    from anima_server.services.agent.inner_life.store import get_affect_state

    state = get_affect_state(None, user_id=1)
    assert state.valence == pytest.approx(DEFAULT_AFFECT_CONFIG.baseline_valence)
    assert state.energy == pytest.approx(DEFAULT_AFFECT_CONFIG.baseline_energy)
