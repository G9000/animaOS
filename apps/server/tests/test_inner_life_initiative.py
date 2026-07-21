"""Tests for IL3 — Drive Accumulators and Push Initiative.

Covers: the pure accumulator math (growth/reset/clamp/leaky-decay) in
``drives.py``; the pure gate chain (``compute_gate_states``, ``should_fire``,
``effective_cooldown_hours``, ``dominant_drive``) in ``initiative.py``; the
off-by-default guarantee at both the pure and edge level; drive-tagged
generation (prompt content + forbidden-filler assertion, LLM seam mocked);
provenance reconstruction; delivery + the fetch/ack API route; idle-only
firing via the real presence tick; vault export/import round-tripping; and
eval-reset clearing.
"""

from __future__ import annotations

import inspect
from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from anima_server.db.base import Base
from anima_server.db.runtime_base import RuntimeBase
from anima_server.models import (
    ForesightSignal,
    InitiativeLog,
    MemoryEpisode,
    MemoryItem,
)
from anima_server.models.runtime import RuntimeThread
from anima_server.models.runtime_consciousness import DriveStateRow, PendingInitiative
from anima_server.services.agent.inner_life import drives, initiative
from anima_server.services.agent.inner_life.delivery import (
    DeliveryResult,
    InitiativeDelivery,
    OSNotificationDelivery,
    PendingInitiativeDelivery,
    acknowledge_pending_initiative,
    list_and_mark_delivered,
)
from anima_server.services.agent.inner_life.drives import (
    DRIVE_NAMES,
    DRIVE_NOVELTY,
    DRIVE_PATTERN_INSIGHT,
    DRIVE_RELATIONAL,
    DRIVE_UNRESOLVED_THREAD,
    DriveConfig,
    DriveSignals,
    DriveState,
    advance_drives,
    reset_drive,
)
from anima_server.services.agent.inner_life.initiative import (
    DriveDecision,
    DriveRecord,
    GateConfig,
    compute_gate_states,
    count_recent_fires,
    count_unanswered_initiatives,
    dominant_drive,
    effective_cooldown_hours,
    should_fire,
    tick_initiative_for_user,
)
from anima_server.services.presence_config import get_or_create_presence_config
from anima_server.services.vault import export_database_snapshot, restore_database_snapshot
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def db() -> Session:  # type: ignore[misc]
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _create_soul_engine() -> Engine:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    return engine


def _create_runtime_engine() -> Engine:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    RuntimeBase.metadata.create_all(bind=engine)
    return engine


def _make_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _default_gate_config(**overrides: object) -> GateConfig:
    values: dict[str, object] = dict(
        enabled=True,
        quiet_hours_start=None,
        quiet_hours_end=None,
        cooldown_base_hours=24.0,
        cooldown_min_hours=8.0,
        cooldown_backoff_factor=1.5,
        cooldown_max_hours=168.0,
        max_per_day=1,
        max_per_week=3,
        thetas={name: 0.7 for name in DRIVE_NAMES},
    )
    values.update(overrides)
    return GateConfig(**values)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1. Pure accumulator math (drives.py)
# ---------------------------------------------------------------------------


def test_grow_condition_increases_pressure_proportional_to_delta_hours() -> None:
    state = DriveState()
    signals = DriveSignals(unresolved_thread_open=True)
    config = DriveConfig(growth_unresolved_thread=0.1)
    updated = advance_drives(state, signals, 5.0, config)
    assert updated.unresolved_thread == pytest.approx(0.5)
    # Other drives untouched.
    assert updated.pattern_insight == 0.0
    assert updated.relational == 0.0


def test_pressure_clamps_at_one_even_with_overshoot() -> None:
    state = DriveState(unresolved_thread=0.95)
    signals = DriveSignals(unresolved_thread_open=True)
    config = DriveConfig(growth_unresolved_thread=1.0)
    updated = advance_drives(state, signals, 100.0, config)
    assert updated.unresolved_thread == 1.0


def test_pressure_never_goes_negative() -> None:
    state = DriveState(relational=0.05)
    updated = advance_drives(state, DriveSignals(), 1000.0, DriveConfig(leak_tau_hours=1.0))
    assert updated.relational >= 0.0


def test_leaky_decay_when_grow_condition_absent() -> None:
    import math

    state = DriveState(unresolved_thread=0.5)
    updated = advance_drives(state, DriveSignals(), 50.0, DriveConfig(leak_tau_hours=50.0))
    # exp(-50/50) = exp(-1) ~ 0.36788
    assert updated.unresolved_thread == pytest.approx(0.5 * math.exp(-1.0), rel=1e-9)
    assert updated.unresolved_thread < 0.5


def test_reset_signal_hard_zeroes_regardless_of_growth() -> None:
    state = DriveState(unresolved_thread=0.9, relational=0.9)
    updated = advance_drives(
        state,
        DriveSignals(
            unresolved_thread_open=True, relational_overdue=True, user_turn_occurred=True
        ),
        10.0,
    )
    assert updated.unresolved_thread == 0.0
    assert updated.relational == 0.0


def test_novelty_resets_on_novel_topic_only() -> None:
    state = DriveState(novelty=0.8)
    updated = advance_drives(state, DriveSignals(novel_topic_discussed=True), 5.0)
    assert updated.novelty == 0.0


def test_pattern_insight_and_dream_residue_never_reset_via_signals() -> None:
    # These two only reset via reset_drive at fire time (surfaced), never
    # through advance_drives itself — advancing with no grow signal just
    # leaks, it never hard-zeroes.
    state = DriveState(pattern_insight=0.4, dream_residue=0.4)
    updated = advance_drives(state, DriveSignals(), 0.001, DriveConfig(leak_tau_hours=10_000))
    assert updated.pattern_insight > 0.0
    assert updated.dream_residue > 0.0


def test_reset_drive_zeroes_named_drive_only() -> None:
    state = DriveState(unresolved_thread=0.5, pattern_insight=0.5)
    updated = reset_drive(state, DRIVE_PATTERN_INSIGHT)
    assert updated.pattern_insight == 0.0
    assert updated.unresolved_thread == 0.5


def test_reset_drive_rejects_unknown_drive() -> None:
    with pytest.raises(ValueError):
        reset_drive(DriveState(), "not_a_real_drive")


def test_negative_delta_hours_is_clamped_to_zero() -> None:
    state = DriveState(unresolved_thread=0.3)
    updated = advance_drives(state, DriveSignals(unresolved_thread_open=True), -5.0)
    assert updated.unresolved_thread == pytest.approx(0.3)


def test_as_dict_returns_all_five_drives() -> None:
    state = DriveState(unresolved_thread=0.1, pattern_insight=0.2, relational=0.3, novelty=0.4, dream_residue=0.5)
    snapshot = state.as_dict()
    assert snapshot == {
        "unresolved_thread": 0.1,
        "pattern_insight": 0.2,
        "relational": 0.3,
        "novelty": 0.4,
        "dream_residue": 0.5,
    }


# ---------------------------------------------------------------------------
# 2. Pure gate chain (initiative.py)
# ---------------------------------------------------------------------------


def test_dominant_drive_picks_highest_pressure_above_theta() -> None:
    pressures = DriveState(unresolved_thread=0.8, relational=0.9, novelty=0.5)
    config = _default_gate_config()
    result = dominant_drive(pressures, config)
    assert result == (DRIVE_RELATIONAL, pytest.approx(0.9))


def test_dominant_drive_none_when_nothing_crosses_theta() -> None:
    pressures = DriveState(unresolved_thread=0.5, relational=0.6)
    assert dominant_drive(pressures, _default_gate_config()) is None


def test_dominant_drive_ties_break_by_drive_name_order() -> None:
    pressures = DriveState(unresolved_thread=0.9, pattern_insight=0.9)
    result = dominant_drive(pressures, _default_gate_config())
    assert result[0] == DRIVE_UNRESOLVED_THREAD  # first in DRIVE_NAMES


def test_effective_cooldown_shortens_toward_min_as_closeness_rises() -> None:
    config = _default_gate_config()
    far = effective_cooldown_hours(config, closeness=0.0, unanswered_initiatives=0)
    close = effective_cooldown_hours(config, closeness=1.0, unanswered_initiatives=0)
    assert far == pytest.approx(24.0)
    assert close == pytest.approx(8.0)
    mid = effective_cooldown_hours(config, closeness=0.5, unanswered_initiatives=0)
    assert 8.0 < mid < 24.0


def test_effective_cooldown_lengthens_after_unanswered_initiatives() -> None:
    config = _default_gate_config()
    baseline = effective_cooldown_hours(config, closeness=0.0, unanswered_initiatives=0)
    backed_off = effective_cooldown_hours(config, closeness=0.0, unanswered_initiatives=2)
    assert backed_off > baseline
    assert backed_off <= config.cooldown_max_hours


def test_effective_cooldown_capped_at_max_hours() -> None:
    config = _default_gate_config(cooldown_max_hours=48.0, cooldown_backoff_factor=3.0)
    result = effective_cooldown_hours(config, closeness=0.0, unanswered_initiatives=10)
    assert result == 48.0


def test_gate_states_quiet_hours_blocks() -> None:
    config = _default_gate_config(quiet_hours_start=22, quiet_hours_end=7)
    record = DriveRecord(pressures=DriveState())
    now_in_quiet = datetime(2026, 1, 1, 23, 0, tzinfo=UTC)  # 23:00, inside [22,7)
    gates = compute_gate_states(
        record, config, now_in_quiet, closeness=0.0, fires_today=0, fires_this_week=0
    )
    assert gates.outside_quiet_hours is False
    assert gates.all_pass is False

    now_outside_quiet = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    gates2 = compute_gate_states(
        record, config, now_outside_quiet, closeness=0.0, fires_today=0, fires_this_week=0
    )
    assert gates2.outside_quiet_hours is True


def test_gate_states_no_quiet_hours_configured_always_passes() -> None:
    config = _default_gate_config(quiet_hours_start=None, quiet_hours_end=None)
    record = DriveRecord(pressures=DriveState())
    gates = compute_gate_states(
        record, config, datetime(2026, 1, 1, 3, 0, tzinfo=UTC),
        closeness=0.0, fires_today=0, fires_this_week=0,
    )
    assert gates.outside_quiet_hours is True


def test_gate_states_cooldown_blocks_within_window() -> None:
    config = _default_gate_config()
    now = datetime(2026, 1, 2, 0, 0, tzinfo=UTC)
    record = DriveRecord(pressures=DriveState(), last_fired_at=now - timedelta(hours=1))
    gates = compute_gate_states(
        record, config, now, closeness=0.0, fires_today=0, fires_this_week=0
    )
    assert gates.cooldown_elapsed is False

    record_elapsed = DriveRecord(pressures=DriveState(), last_fired_at=now - timedelta(hours=25))
    gates2 = compute_gate_states(
        record_elapsed, config, now, closeness=0.0, fires_today=0, fires_this_week=0
    )
    assert gates2.cooldown_elapsed is True


def test_gate_states_rate_caps_enforced() -> None:
    config = _default_gate_config(max_per_day=1, max_per_week=3)
    record = DriveRecord(pressures=DriveState())
    now = datetime(2026, 1, 1, tzinfo=UTC)
    at_daily_cap = compute_gate_states(
        record, config, now, closeness=0.0, fires_today=1, fires_this_week=1
    )
    assert at_daily_cap.under_daily_cap is False

    at_weekly_cap = compute_gate_states(
        record, config, now, closeness=0.0, fires_today=0, fires_this_week=3
    )
    assert at_weekly_cap.under_weekly_cap is False

    under_caps = compute_gate_states(
        record, config, now, closeness=0.0, fires_today=0, fires_this_week=1
    )
    assert under_caps.under_daily_cap is True
    assert under_caps.under_weekly_cap is True


def test_should_fire_returns_dominant_drive_when_all_gates_pass() -> None:
    config = _default_gate_config()
    record = DriveRecord(pressures=DriveState(relational=0.9))
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    decision = should_fire(record, config, now, 0.0, fires_today=0, fires_this_week=0)
    assert decision is not None
    assert decision.drive == DRIVE_RELATIONAL
    assert decision.pressure == pytest.approx(0.9)
    assert decision.pressure_snapshot == record.pressures.as_dict()
    assert decision.gate_states["enabled"] is True


def test_should_fire_none_when_no_drive_crosses_theta() -> None:
    config = _default_gate_config()
    record = DriveRecord(pressures=DriveState(relational=0.5))
    decision = should_fire(
        record, config, datetime(2026, 1, 1, tzinfo=UTC), 0.0, fires_today=0, fires_this_week=0
    )
    assert decision is None


# ---------------------------------------------------------------------------
# 3. Off-by-default — non-negotiable
# ---------------------------------------------------------------------------


def test_disabled_never_fires_even_at_maximum_pressure_pure_level() -> None:
    config = _default_gate_config(enabled=False)
    record = DriveRecord(pressures=DriveState(*(1.0,) * 5))
    decision = should_fire(
        record, config, datetime(2026, 1, 1, 12, 0, tzinfo=UTC), 1.0, fires_today=0, fires_this_week=0
    )
    assert decision is None


def test_disabled_never_fires_regardless_of_every_other_gate_passing() -> None:
    # Every other gate wide open (no quiet hours, cooldown long elapsed, well
    # under rate caps) — only `enabled=False` should block it.
    config = _default_gate_config(
        enabled=False, quiet_hours_start=None, quiet_hours_end=None
    )
    record = DriveRecord(
        pressures=DriveState(unresolved_thread=1.0),
        last_fired_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    gates = compute_gate_states(
        record, config, datetime(2026, 1, 1, tzinfo=UTC),
        closeness=1.0, fires_today=0, fires_this_week=0,
    )
    assert gates.outside_quiet_hours and gates.cooldown_elapsed
    assert gates.under_daily_cap and gates.under_weekly_cap
    assert gates.all_pass is False  # enabled=False alone sinks it
    assert should_fire(
        record, config, datetime(2026, 1, 1, tzinfo=UTC), 1.0, fires_today=0, fires_this_week=0
    ) is None


def test_disabled_edge_level_never_creates_initiative_or_pending_row() -> None:
    """Full edge-level guarantee: with PresenceConfig.initiative_enabled
    False (the model default), tick_initiative_for_user never writes an
    InitiativeLog or PendingInitiative row no matter how much pressure has
    accumulated."""
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_factory(soul_engine)
    runtime_factory = _make_factory(runtime_engine)

    with runtime_factory() as db_:
        db_.add(
            DriveStateRow(
                user_id=1,
                unresolved_thread=1.0,
                pattern_insight=1.0,
                relational=1.0,
                novelty=1.0,
                dream_residue=1.0,
                updated_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
        db_.commit()

    with soul_factory() as db_:
        get_or_create_presence_config(db_, 1)  # initiative_enabled defaults False
        db_.commit()

    ok = tick_initiative_for_user(
        soul_factory, runtime_factory, user_id=1, local_now=datetime(2026, 1, 2, 12, 0, tzinfo=UTC)
    )
    assert ok is True

    with soul_factory() as db_:
        assert db_.scalars(select(InitiativeLog)).all() == []
    with runtime_factory() as db_:
        assert db_.scalars(select(PendingInitiative)).all() == []

    soul_engine.dispose()
    runtime_engine.dispose()


def test_master_presence_pause_blocks_fire_even_with_initiative_enabled() -> None:
    """Regression (PR review, P2): `PresenceConfig.enabled` is the top-level
    kill switch for ALL proactive notices. A user who paused Presence
    (`enabled=False`) but left `initiative_enabled=True` must never receive an
    initiative — the gate must require BOTH flags."""
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_factory(soul_engine)
    runtime_factory = _make_factory(runtime_engine)

    with runtime_factory() as db_:
        db_.add(
            DriveStateRow(
                user_id=1, relational=1.0,
                updated_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
        db_.commit()

    with soul_factory() as db_:
        cfg = get_or_create_presence_config(db_, 1)
        cfg.enabled = False  # master Presence paused
        cfg.initiative_enabled = True  # but initiative left on
        db_.commit()

    ok = tick_initiative_for_user(
        soul_factory, runtime_factory, user_id=1,
        local_now=datetime(2026, 1, 2, 12, 0, tzinfo=UTC),
    )
    assert ok is True

    with soul_factory() as db_:
        assert db_.scalars(select(InitiativeLog)).all() == []
    with runtime_factory() as db_:
        assert db_.scalars(select(PendingInitiative)).all() == []

    soul_engine.dispose()
    runtime_engine.dispose()


def test_material_backed_drive_with_no_material_does_not_fire_or_call_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression (PR review, P2): a material-backed drive (here
    pattern_insight, which only resets on surfacing) can cross threshold and
    then lose its source before the tick fires — the pattern MemoryItem is
    distilled/superseded, so gather_drive_material returns "". The tick must
    (a) not fire or call the LLM, and (b) RESET the material-less drive so its
    stale pressure can't stay dominant and starve other valid drives for the
    whole leak window (a later PR-review finding)."""
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_factory(soul_engine)
    runtime_factory = _make_factory(runtime_engine)

    _seed_enabled_user(
        soul_factory, runtime_factory,
        pressures={"pattern_insight": 0.99},
        updated_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )
    # A pattern finding existed but has since been distilled away — the
    # active-item filter in gather_drive_material excludes it, so no material.
    with soul_factory() as db_:
        db_.add(
            MemoryItem(
                user_id=1, category="pattern", content="a pattern once shareable",
                distilled_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
        db_.commit()

    async def fake_generate(soul_db, *, user_id, decision, material, affect_line):
        raise AssertionError("LLM must not be called when material is missing")

    monkeypatch.setattr(initiative, "generate_initiative_message", fake_generate)

    ok = tick_initiative_for_user(
        soul_factory, runtime_factory, user_id=1,
        local_now=datetime(2026, 1, 2, 12, 0, tzinfo=UTC),
    )
    assert ok is True  # tick succeeded, it simply didn't fire

    with soul_factory() as db_:
        assert db_.scalars(select(InitiativeLog)).all() == []  # no phantom row
    with runtime_factory() as db_:
        assert db_.scalars(select(PendingInitiative)).all() == []
        # The stale, material-less drive is reset so it stops dominating
        # selection (would otherwise block valid drives for its leak window).
        row = db_.scalars(select(DriveStateRow).where(DriveStateRow.user_id == 1)).one()
        assert row.pattern_insight == 0.0

    soul_engine.dispose()
    runtime_engine.dispose()


def test_material_less_dominant_drive_does_not_block_a_lower_valid_drive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression (PR review, P2): should_fire() picks the highest-pressure
    drive, so a material-less drive stuck above threshold (here pattern_insight
    with no MemoryItem) must not monopolize the tick. The tick resets it and
    re-selects, firing the next-highest drive that DOES have material
    (unresolved_thread, backed by a real in-horizon foresight) in the SAME
    tick."""
    from anima_server.services.data_crypto import ef

    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_factory(soul_engine)
    runtime_factory = _make_factory(runtime_engine)

    now = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    # pattern_insight is highest but has NO pattern item; unresolved_thread is
    # lower but backed by a real, open, in-horizon foresight.
    _seed_enabled_user(
        soul_factory, runtime_factory,
        pressures={"pattern_insight": 0.99, "unresolved_thread": 0.8},
        updated_at=now - timedelta(hours=1),
    )
    with soul_factory() as db_:
        db_.add(
            ForesightSignal(
                user_id=1,
                content=ef(1, "the trip you have coming up", table="foresight_signals", field="content"),
                evidence="mentioned it", status="active",
                start_date=date(2026, 1, 11), confidence=0.9,
            )
        )
        db_.commit()

    async def fake_generate(soul_db, *, user_id, decision, material, affect_line):
        assert decision.drive == DRIVE_UNRESOLVED_THREAD  # never the empty one
        return f"about: {material}"

    monkeypatch.setattr(initiative, "generate_initiative_message", fake_generate)

    ok = tick_initiative_for_user(soul_factory, runtime_factory, user_id=1, local_now=now)
    assert ok is True

    with soul_factory() as db_:
        logs = db_.scalars(select(InitiativeLog)).all()
        assert len(logs) == 1
        assert logs[0].drive == DRIVE_UNRESOLVED_THREAD  # the valid drive fired
    with runtime_factory() as db_:
        row = db_.scalars(select(DriveStateRow).where(DriveStateRow.user_id == 1)).one()
        assert row.pattern_insight == 0.0  # material-less drive was reset
        assert row.unresolved_thread == 0.0  # fired -> reset by the caller

    soul_engine.dispose()
    runtime_engine.dispose()


# ---------------------------------------------------------------------------
# 4. Drive-tagged generation
# ---------------------------------------------------------------------------


def test_prompt_forbids_generic_checkin_filler() -> None:
    from anima_server.services.agent.prompt_loader import PROMPTS_DIR

    source = (PROMPTS_DIR / "initiative_message.md.j2").read_text(encoding="utf-8")
    assert "how are you" in source.lower()
    assert "just checking in" in source.lower()
    assert "forbidden" in source.lower()


def test_generate_initiative_message_carries_drive_and_material(
    monkeypatch: pytest.MonkeyPatch, db: Session
) -> None:
    captured: dict[str, str] = {}

    async def fake_call_llm_for_text(system: str, prompt: str, *, client=None) -> str:
        captured["system"] = system
        captured["prompt"] = prompt
        return "I keep thinking about that trip you mentioned."

    monkeypatch.setattr(
        "anima_server.services.agent.llm_json.call_llm_for_text", fake_call_llm_for_text
    )

    import asyncio

    decision = DriveDecision(
        drive=DRIVE_UNRESOLVED_THREAD,
        pressure=0.9,
        pressure_snapshot={"unresolved_thread": 0.9},
        gate_states={"enabled": True},
    )
    result = asyncio.run(
        initiative.generate_initiative_message(
            db,
            user_id=1,
            decision=decision,
            material="the trip to Kyoto next month",
            affect_line="settled, gently warm",
        )
    )
    assert result == "I keep thinking about that trip you mentioned."
    assert DRIVE_UNRESOLVED_THREAD in captured["prompt"]
    assert "the trip to Kyoto next month" in captured["prompt"]
    assert "settled, gently warm" in captured["prompt"]
    assert "forbidden" in captured["prompt"].lower()


def test_failed_delivery_frees_quota_but_starts_cooldown_and_keeps_pressure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR review (P2): a generated-but-undelivered initiative must NOT consume
    the rate-cap quota (the user never received it), yet must still start the
    cooldown so a persistent delivery failure can't re-generate an LLM message
    every tick. The drive's pressure is left intact (not reset) since nothing
    was actually voiced."""
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_factory(soul_engine)
    runtime_factory = _make_factory(runtime_engine)

    now = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    _seed_enabled_user(
        soul_factory, runtime_factory,
        pressures={"relational": 0.99}, updated_at=now - timedelta(hours=1),
    )

    calls = {"n": 0}

    async def fake_generate(soul_db, *, user_id, decision, material, affect_line):
        calls["n"] += 1
        return "hello"

    monkeypatch.setattr(initiative, "generate_initiative_message", fake_generate)

    failing = _StubDelivery(delivered=False)
    ok = tick_initiative_for_user(
        soul_factory, runtime_factory, user_id=1, local_now=now, delivery=failing
    )
    assert ok is True
    assert calls["n"] == 1  # generation happened once

    with soul_factory() as db_:
        logs = db_.scalars(select(InitiativeLog)).all()
        assert len(logs) == 1
        assert logs[0].delivered is False  # logged the attempt, not delivered
        assert logs[0].generated_text is not None
        # Undelivered -> quota untouched, so the user isn't rate-limited for a
        # message they never received.
        assert count_recent_fires(db_, user_id=1, now=now) == (0, 0)
    with runtime_factory() as db_:
        row = db_.scalars(select(DriveStateRow).where(DriveStateRow.user_id == 1)).one()
        assert row.last_fired_at is not None  # cooldown started (spam guard)
        assert row.relational > 0.9  # pressure NOT reset — nothing was voiced

    # Second tick within the cooldown window must NOT re-generate, even though
    # the quota is free — the cooldown is what bounds LLM spam here.
    ok2 = tick_initiative_for_user(
        soul_factory, runtime_factory, user_id=1,
        local_now=now + timedelta(hours=1), delivery=failing,
    )
    assert ok2 is True
    assert calls["n"] == 1  # still 1 — cooldown blocked re-generation

    soul_engine.dispose()
    runtime_engine.dispose()


def test_generate_initiative_message_returns_none_on_empty_output(
    monkeypatch: pytest.MonkeyPatch, db: Session
) -> None:
    async def empty_call(system: str, prompt: str, *, client=None) -> str:
        return "   "

    monkeypatch.setattr(
        "anima_server.services.agent.llm_json.call_llm_for_text", empty_call
    )
    decision = DriveDecision(
        drive=DRIVE_RELATIONAL, pressure=0.9, pressure_snapshot={}, gate_states={}
    )
    import asyncio

    result = asyncio.run(
        initiative.generate_initiative_message(
            db, user_id=1, decision=decision, material="", affect_line="steady"
        )
    )
    assert result is None


def test_generate_initiative_message_returns_none_on_llm_exception(
    monkeypatch: pytest.MonkeyPatch, db: Session
) -> None:
    async def raising_call(system: str, prompt: str, *, client=None) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "anima_server.services.agent.llm_json.call_llm_for_text", raising_call
    )
    decision = DriveDecision(
        drive=DRIVE_RELATIONAL, pressure=0.9, pressure_snapshot={}, gate_states={}
    )
    import asyncio

    result = asyncio.run(
        initiative.generate_initiative_message(
            db, user_id=1, decision=decision, material="", affect_line="steady"
        )
    )
    assert result is None


# ---------------------------------------------------------------------------
# 5. Full tick: fire, provenance, delivery, generation failure handling
# ---------------------------------------------------------------------------


class _StubDelivery(InitiativeDelivery):
    def __init__(self, delivered: bool = True) -> None:
        self.delivered = delivered
        self.calls: list[dict[str, object]] = []

    def deliver(self, runtime_db, *, user_id, drive, text, initiative_log_id):
        self.calls.append(
            {
                "user_id": user_id,
                "drive": drive,
                "text": text,
                "initiative_log_id": initiative_log_id,
            }
        )
        return DeliveryResult(delivered=self.delivered, pending_initiative_id=None)


def _seed_enabled_user(
    soul_factory: sessionmaker[Session],
    runtime_factory: sessionmaker[Session],
    *,
    user_id: int = 1,
    pressures: dict[str, float] | None = None,
    updated_at: datetime | None = None,
) -> None:
    pressures = pressures or {"relational": 0.9}
    with soul_factory() as db_:
        cfg = get_or_create_presence_config(db_, user_id)
        cfg.initiative_enabled = True
        db_.commit()
    with runtime_factory() as db_:
        db_.add(
            DriveStateRow(
                user_id=user_id,
                updated_at=updated_at or datetime(2026, 1, 1, tzinfo=UTC),
                **pressures,
            )
        )
        db_.commit()


def test_fully_passing_state_fires_exactly_the_dominant_drive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_factory(soul_engine)
    runtime_factory = _make_factory(runtime_engine)

    _seed_enabled_user(
        soul_factory, runtime_factory, pressures={"relational": 0.9, "novelty": 0.75}
    )

    async def fake_generate(soul_db, *, user_id, decision, material, affect_line):
        return f"Message about {decision.drive}"

    monkeypatch.setattr(initiative, "generate_initiative_message", fake_generate)

    now = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)
    ok = tick_initiative_for_user(
        soul_factory, runtime_factory, user_id=1, local_now=now
    )
    assert ok is True

    with soul_factory() as db_:
        logs = db_.scalars(select(InitiativeLog)).all()
        assert len(logs) == 1
        assert logs[0].drive == DRIVE_RELATIONAL
        assert logs[0].delivered is True
        assert logs[0].generated_text is not None

    with runtime_factory() as db_:
        pending = db_.scalars(select(PendingInitiative)).all()
        assert len(pending) == 1
        assert pending[0].drive == DRIVE_RELATIONAL
        row = db_.scalars(select(DriveStateRow).where(DriveStateRow.user_id == 1)).one()
        assert row.relational == 0.0  # fired drive reset
        # novelty wasn't the firing drive, so it isn't reset — but 36h have
        # elapsed with no grow signal, so it DOES leak-decay by construction
        # (a leaky integrator, not frozen state).
        import math

        expected_novelty = 0.75 * math.exp(-36.0 / DriveConfig().leak_tau_hours)
        assert row.novelty == pytest.approx(expected_novelty, rel=1e-6)
        assert row.last_fired_at is not None

    soul_engine.dispose()
    runtime_engine.dispose()


def test_custom_delivery_adapter_receives_the_dominant_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Delivery is a swappable seam — the tick routes the fired decision to
    whatever adapter it's given, not just the default."""
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_factory(soul_engine)
    runtime_factory = _make_factory(runtime_engine)

    _seed_enabled_user(
        soul_factory, runtime_factory, pressures={"relational": 0.9, "novelty": 0.75}
    )

    async def fake_generate(soul_db, *, user_id, decision, material, affect_line):
        return f"Message about {decision.drive}"

    monkeypatch.setattr(initiative, "generate_initiative_message", fake_generate)

    stub_delivery = _StubDelivery()
    now = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)
    tick_initiative_for_user(
        soul_factory, runtime_factory, user_id=1, local_now=now, delivery=stub_delivery
    )
    assert len(stub_delivery.calls) == 1
    assert stub_delivery.calls[0]["drive"] == DRIVE_RELATIONAL  # higher pressure wins
    assert stub_delivery.calls[0]["text"] == f"Message about {DRIVE_RELATIONAL}"

    # The stub never writes a PendingInitiative row itself, so none exists —
    # confirms the tick genuinely delegates delivery rather than hard-coding it.
    with runtime_factory() as db_:
        assert db_.scalars(select(PendingInitiative)).all() == []

    soul_engine.dispose()
    runtime_engine.dispose()


def test_generation_failure_logs_attempt_resets_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_factory(soul_engine)
    runtime_factory = _make_factory(runtime_engine)

    _seed_enabled_user(soul_factory, runtime_factory, pressures={"relational": 0.9})

    async def failing_generate(soul_db, *, user_id, decision, material, affect_line):
        return None

    monkeypatch.setattr(initiative, "generate_initiative_message", failing_generate)

    now = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)
    ok = tick_initiative_for_user(soul_factory, runtime_factory, user_id=1, local_now=now)
    assert ok is True

    with soul_factory() as db_:
        logs = db_.scalars(select(InitiativeLog)).all()
        assert len(logs) == 1  # attempt logged
        assert logs[0].generated_text is None
        assert logs[0].delivered is False

    with runtime_factory() as db_:
        assert db_.scalars(select(PendingInitiative)).all() == []  # nothing delivered
        row = db_.scalars(select(DriveStateRow).where(DriveStateRow.user_id == 1)).one()
        # NOT hard-reset to 0 — a failed generation must never surface the
        # drive. It still leak-decays over the elapsed 36h like any tick.
        import math

        expected_relational = 0.9 * math.exp(-36.0 / DriveConfig().leak_tau_hours)
        assert row.relational == pytest.approx(expected_relational, rel=1e-6)
        assert row.relational > 0.5  # nowhere near a hard reset
        assert row.last_fired_at is None

    soul_engine.dispose()
    runtime_engine.dispose()


def test_rate_cap_blocks_second_fire_same_day(monkeypatch: pytest.MonkeyPatch) -> None:
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_factory(soul_engine)
    runtime_factory = _make_factory(runtime_engine)

    _seed_enabled_user(soul_factory, runtime_factory, pressures={"relational": 0.9})

    async def fake_generate(soul_db, *, user_id, decision, material, affect_line):
        return "hi"

    monkeypatch.setattr(initiative, "generate_initiative_message", fake_generate)

    first_now = datetime(2026, 1, 2, 8, 0, tzinfo=UTC)
    tick_initiative_for_user(soul_factory, runtime_factory, user_id=1, local_now=first_now)

    # Re-seed pressure above threshold again (a fresh growth cycle) for the
    # same day, well past... actually within the SAME day, no cooldown wait.
    with runtime_factory() as db_:
        row = db_.scalars(select(DriveStateRow).where(DriveStateRow.user_id == 1)).one()
        row.relational = 0.95
        row.updated_at = first_now
        db_.commit()

    second_now = first_now + timedelta(hours=1)
    tick_initiative_for_user(soul_factory, runtime_factory, user_id=1, local_now=second_now)

    with soul_factory() as db_:
        logs = db_.scalars(select(InitiativeLog)).all()
        assert len(logs) == 1  # second attempt blocked before ever generating

    soul_engine.dispose()
    runtime_engine.dispose()


def test_unanswered_initiative_increases_cooldown_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_factory(soul_engine)
    runtime_factory = _make_factory(runtime_engine)

    _seed_enabled_user(soul_factory, runtime_factory, pressures={"relational": 0.9})

    async def fake_generate(soul_db, *, user_id, decision, material, affect_line):
        return "hi"

    monkeypatch.setattr(initiative, "generate_initiative_message", fake_generate)

    first_now = datetime(2026, 1, 2, 8, 0, tzinfo=UTC)
    tick_initiative_for_user(soul_factory, runtime_factory, user_id=1, local_now=first_now)

    with soul_factory() as db_:
        log_row = db_.scalars(select(InitiativeLog)).one()
        assert log_row.delivered is True
        assert log_row.answered is False  # never acked

    # Re-grow pressure and advance past the BASE cooldown (24h) but this
    # unanswered initiative should have pushed the effective cooldown
    # (backoff_factor=1.5) well past that.
    with runtime_factory() as db_:
        row = db_.scalars(select(DriveStateRow).where(DriveStateRow.user_id == 1)).one()
        row.relational = 0.95
        row.updated_at = first_now + timedelta(hours=25)
        db_.commit()

    second_now = first_now + timedelta(hours=25)
    tick_initiative_for_user(soul_factory, runtime_factory, user_id=1, local_now=second_now)

    with soul_factory() as db_:
        logs = db_.scalars(select(InitiativeLog)).all()
        # Still blocked by the backed-off cooldown, despite 25h > base 24h.
        assert len(logs) == 1

    soul_engine.dispose()
    runtime_engine.dispose()


# ---------------------------------------------------------------------------
# 6. Provenance reconstruction
# ---------------------------------------------------------------------------


def test_provenance_log_reconstructs_why_it_fired(db: Session) -> None:
    log_row = InitiativeLog(
        user_id=1,
        fired_at=datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
        drive=DRIVE_UNRESOLVED_THREAD,
        pressure_snapshot={"unresolved_thread": 0.82, "relational": 0.4},
        gate_states={
            "enabled": True,
            "outside_quiet_hours": True,
            "cooldown_elapsed": True,
            "under_daily_cap": True,
            "under_weekly_cap": True,
            "idle": True,
        },
        generated_text="I keep thinking about your trip next week.",
        delivered=True,
        answered=False,
    )
    db.add(log_row)
    db.commit()

    fetched = db.get(InitiativeLog, log_row.id)
    assert fetched.drive == DRIVE_UNRESOLVED_THREAD
    assert fetched.pressure_snapshot["unresolved_thread"] == pytest.approx(0.82)
    assert fetched.gate_states["enabled"] is True
    assert fetched.generated_text == "I keep thinking about your trip next week."
    # Every field needed to answer "why did it message me?" is present and
    # reconstructable from this one row.


def test_count_recent_fires_only_counts_delivered_messages(db: Session) -> None:
    now = datetime(2026, 1, 10, tzinfo=UTC)
    db.add_all(
        [
            InitiativeLog(
                user_id=1, fired_at=now - timedelta(hours=1), drive=DRIVE_RELATIONAL,
                pressure_snapshot={}, gate_states={}, generated_text="hi", delivered=True,
            ),
            # Failed generation (no text) — never counts.
            InitiativeLog(
                user_id=1, fired_at=now - timedelta(hours=2), drive=DRIVE_RELATIONAL,
                pressure_snapshot={}, gate_states={}, generated_text=None, delivered=False,
            ),
            # Generated but NOT delivered (delivery adapter returned False /
            # raised) — the user never received it, so it must NOT consume the
            # rate-cap quota (PR review, P2).
            InitiativeLog(
                user_id=1, fired_at=now - timedelta(minutes=30), drive=DRIVE_RELATIONAL,
                pressure_snapshot={}, gate_states={}, generated_text="undelivered", delivered=False,
            ),
            InitiativeLog(
                user_id=1, fired_at=now - timedelta(days=3), drive=DRIVE_NOVELTY,
                pressure_snapshot={}, gate_states={}, generated_text="hey", delivered=True,
            ),
            InitiativeLog(
                user_id=1, fired_at=now - timedelta(days=10), drive=DRIVE_NOVELTY,
                pressure_snapshot={}, gate_states={}, generated_text="old", delivered=True,
            ),
        ]
    )
    db.commit()
    today, week = count_recent_fires(db, user_id=1, now=now)
    # Only delivered rows within the window: 1 today, 2 this week. The
    # failed-generation, the generated-but-undelivered, and the 10-day-old row
    # are all excluded.
    assert today == 1
    assert week == 2


def test_fired_at_is_stored_in_utc_regardless_of_local_tick_offset(db: Session) -> None:
    """Regression (PR review, P2): SQLite's DateTime(timezone=True) drops
    tzinfo on read-back — empirically, a value written with a non-UTC
    offset round-trips as a NAIVE datetime carrying the ORIGINAL WALL-CLOCK
    numbers (not converted to UTC). Since `_as_utc` re-attaches UTC to any
    naive value, writing `fired_at` as the tick's raw local-offset `now`
    would silently shift the daily/weekly rate-cap windows by the server's
    UTC offset in any non-UTC deployment. `_fire` must store
    `now.astimezone(UTC)`, not `now` — asserted here via the real SQLite
    round trip (`db.expire_all()` forces a fresh read from the database,
    not the session identity map)."""
    local_tz = timezone(timedelta(hours=8))
    fired_local = datetime(2026, 1, 2, 20, 0, tzinfo=local_tz)  # 12:00 UTC

    db.add(
        InitiativeLog(
            user_id=1,
            fired_at=fired_local.astimezone(UTC),
            drive=DRIVE_RELATIONAL,
            pressure_snapshot={},
            gate_states={},
            generated_text="hi",
            delivered=True,
        )
    )
    db.commit()
    db.expire_all()  # force the actual DB round trip, not the identity map

    # 20 REAL hours later (expressed in the same local zone) — still well
    # under the real 24h day boundary, so the daily cap must still apply.
    today, _ = count_recent_fires(db, user_id=1, now=fired_local + timedelta(hours=20))
    assert today == 1

    # Just past 24 REAL hours -> correctly falls out of the daily window.
    today_after, _ = count_recent_fires(
        db, user_id=1, now=fired_local + timedelta(hours=24, minutes=1)
    )
    assert today_after == 0


def test_fire_writes_fired_at_in_utc_not_local_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same regression as above, exercised through the real `_fire` edge
    function (not a hand-built row) with a local_now bearing a real
    non-UTC offset, confirming the actual write path stores UTC."""
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_factory(soul_engine)
    runtime_factory = _make_factory(runtime_engine)

    _seed_enabled_user(soul_factory, runtime_factory, pressures={"relational": 0.9})

    async def fake_generate(soul_db, *, user_id, decision, material, affect_line):
        return "hi"

    monkeypatch.setattr(initiative, "generate_initiative_message", fake_generate)

    local_tz = timezone(timedelta(hours=8))
    local_now = datetime(2026, 1, 2, 20, 0, tzinfo=local_tz)  # 12:00 UTC
    tick_initiative_for_user(soul_factory, runtime_factory, user_id=1, local_now=local_now)

    with soul_factory() as db_:
        db_.expire_all()
        log_row = db_.scalars(select(InitiativeLog)).one()
        # Stored value's WALL-CLOCK numbers must be the UTC ones (12:00),
        # never the local ones (20:00) with UTC silently re-assumed.
        assert log_row.fired_at.replace(tzinfo=UTC) == local_now.astimezone(UTC)

    soul_engine.dispose()
    runtime_engine.dispose()


def test_count_unanswered_initiatives(db: Session) -> None:
    db.add_all(
        [
            InitiativeLog(
                user_id=1, fired_at=datetime(2026, 1, 1, tzinfo=UTC), drive=DRIVE_RELATIONAL,
                pressure_snapshot={}, gate_states={}, generated_text="a", delivered=True, answered=False,
            ),
            InitiativeLog(
                user_id=1, fired_at=datetime(2026, 1, 2, tzinfo=UTC), drive=DRIVE_RELATIONAL,
                pressure_snapshot={}, gate_states={}, generated_text="b", delivered=True, answered=True,
            ),
        ]
    )
    db.commit()
    assert count_unanswered_initiatives(db, user_id=1) == 1


# ---------------------------------------------------------------------------
# 7. Delivery + fetch/ack
# ---------------------------------------------------------------------------


def test_pending_initiative_delivery_creates_row() -> None:
    engine = _create_runtime_engine()
    factory = _make_factory(engine)
    with factory() as db_:
        result = PendingInitiativeDelivery().deliver(
            db_, user_id=1, drive=DRIVE_RELATIONAL, text="hello", initiative_log_id=42
        )
        db_.commit()
        assert result.delivered is True
        assert result.pending_initiative_id is not None
        row = db_.get(PendingInitiative, result.pending_initiative_id)
        assert row.drive == DRIVE_RELATIONAL
        assert row.text == "hello"
        assert row.initiative_log_id == 42
        assert row.acknowledged is False
    engine.dispose()


def test_os_notification_delivery_is_not_implemented() -> None:
    engine = _create_runtime_engine()
    factory = _make_factory(engine)
    with factory() as db_, pytest.raises(NotImplementedError):
        OSNotificationDelivery().deliver(
            db_, user_id=1, drive=DRIVE_RELATIONAL, text="hi", initiative_log_id=1
        )
    engine.dispose()


def test_fetch_marks_delivered_and_ack_clears_it(db: Session) -> None:
    runtime_engine = _create_runtime_engine()
    runtime_factory = _make_factory(runtime_engine)

    log_row = InitiativeLog(
        user_id=1, fired_at=datetime(2026, 1, 1, tzinfo=UTC), drive=DRIVE_RELATIONAL,
        pressure_snapshot={}, gate_states={}, generated_text="hi", delivered=True, answered=False,
    )
    db.add(log_row)
    db.commit()

    with runtime_factory() as rdb:
        rdb.add(
            PendingInitiative(
                user_id=1, initiative_log_id=log_row.id, drive=DRIVE_RELATIONAL, text="hi"
            )
        )
        rdb.commit()

        fetched = list_and_mark_delivered(rdb, user_id=1)
        rdb.commit()
        assert len(fetched) == 1
        assert fetched[0].delivered is True
        assert fetched[0].acknowledged is False

        acked = acknowledge_pending_initiative(
            rdb, soul_db=db, user_id=1, pending_id=fetched[0].id
        )
        rdb.commit()
        db.commit()
        assert acked is not None
        assert acked.acknowledged is True

        remaining = list_and_mark_delivered(rdb, user_id=1)
        assert remaining == []  # acknowledged rows are excluded

    refreshed_log = db.get(InitiativeLog, log_row.id)
    assert refreshed_log.answered is True

    runtime_engine.dispose()


def test_ack_reconciles_delivered_flag_on_the_soul_log(db: Session) -> None:
    """Regression (PR review, P2): an acknowledgement is definitive proof the
    user received the message, so it must reconcile InitiativeLog.delivered,
    not only `answered`. Otherwise a two-phase under-claim (delivered=False
    left after a failed post-runtime commit) stays uncounted by
    count_recent_fires (which filters delivered=True), letting a close user get
    another initiative inside the 24h cap."""
    now = datetime(2026, 1, 5, tzinfo=UTC)
    runtime_engine = _create_runtime_engine()
    runtime_factory = _make_factory(runtime_engine)

    # Under-claim: the soul log says NOT delivered, yet the pending row IS
    # durable (the exact state a failed delivered-flip commit leaves behind).
    log_row = InitiativeLog(
        user_id=1, fired_at=now, drive=DRIVE_RELATIONAL,
        pressure_snapshot={}, gate_states={}, generated_text="hi",
        delivered=False, answered=False,
    )
    db.add(log_row)
    db.commit()
    assert count_recent_fires(db, user_id=1, now=now) == (0, 0)  # uncounted pre-ack

    with runtime_factory() as rdb:
        rdb.add(
            PendingInitiative(
                user_id=1, initiative_log_id=log_row.id, drive=DRIVE_RELATIONAL, text="hi"
            )
        )
        rdb.commit()
        pid = rdb.scalars(select(PendingInitiative)).one().id
        acknowledge_pending_initiative(rdb, soul_db=db, user_id=1, pending_id=pid)
        rdb.commit()
        db.commit()

    refreshed = db.get(InitiativeLog, log_row.id)
    assert refreshed.delivered is True  # reconciled by the ack
    assert refreshed.answered is True
    assert count_recent_fires(db, user_id=1, now=now) == (1, 1)  # now counted

    runtime_engine.dispose()


def test_poll_reconciles_delivered_flag_on_the_soul_log(db: Session) -> None:
    """Regression (PR review, P2): the poll (GET /initiatives) is the FIRST
    proof of delivery, so list_and_mark_delivered must also best-effort
    reconcile InitiativeLog.delivered — not rely on a later ack. Otherwise a
    two-phase under-claim (delivered=False) that the client polls but doesn't
    ack stays uncounted by count_recent_fires."""
    now = datetime(2026, 1, 5, tzinfo=UTC)
    runtime_engine = _create_runtime_engine()
    runtime_factory = _make_factory(runtime_engine)

    log_row = InitiativeLog(
        user_id=1, fired_at=now, drive=DRIVE_RELATIONAL,
        pressure_snapshot={}, gate_states={}, generated_text="hi",
        delivered=False, answered=False,
    )
    db.add(log_row)
    db.commit()

    with runtime_factory() as rdb:
        rdb.add(
            PendingInitiative(
                user_id=1, initiative_log_id=log_row.id, drive=DRIVE_RELATIONAL, text="hi"
            )
        )
        rdb.commit()
        fetched = list_and_mark_delivered(rdb, user_id=1, soul_db=db)
        rdb.commit()
        db.commit()
        assert len(fetched) == 1

    refreshed = db.get(InitiativeLog, log_row.id)
    assert refreshed.delivered is True  # reconciled by the poll, before any ack
    assert refreshed.answered is False  # poll is delivery, not acknowledgement
    assert count_recent_fires(db, user_id=1, now=now) == (1, 1)

    runtime_engine.dispose()


def test_ack_soul_failure_is_isolated_and_keeps_session_usable(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression (PR review, P2): if the soul-store update during ack raises
    (e.g. a transient locked/corrupt per-user DB), it must be savepoint-
    isolated so the soul session is NOT left in a failed transaction state —
    the API route calls db.commit() right after, which must still succeed —
    and the runtime ack must still stand."""
    now = datetime(2026, 1, 6, tzinfo=UTC)
    runtime_engine = _create_runtime_engine()
    runtime_factory = _make_factory(runtime_engine)

    log_row = InitiativeLog(
        user_id=1, fired_at=now, drive=DRIVE_RELATIONAL,
        pressure_snapshot={}, gate_states={}, generated_text="hi", delivered=False,
    )
    db.add(log_row)
    db.commit()

    with runtime_factory() as rdb:
        rdb.add(
            PendingInitiative(
                user_id=1, initiative_log_id=log_row.id, drive=DRIVE_RELATIONAL, text="hi"
            )
        )
        rdb.commit()
        pid = rdb.scalars(select(PendingInitiative)).one().id

        def boom(*args: object, **kwargs: object) -> object:
            raise RuntimeError("simulated locked/corrupt soul DB")

        monkeypatch.setattr(db, "get", boom)
        row = acknowledge_pending_initiative(rdb, soul_db=db, user_id=1, pending_id=pid)
        rdb.commit()
        assert row is not None
        assert row.acknowledged is True  # runtime ack stood despite soul failure

    # The soul session must not be poisoned — the route commits right after.
    db.commit()  # would raise if the savepoint hadn't isolated the failure

    runtime_engine.dispose()


def test_runtime_biginteger_pk_autoincrements_on_sqlite() -> None:
    """Regression (PR review, P2): runtime models use BigInteger PKs sized for
    PostgreSQL, but SQLite only aliases rowid (autoincrement) for a column
    declared exactly INTEGER — a BIGINT PK fails the first id-less insert with
    'NOT NULL constraint failed'. runtime_base registers a PRODUCTION @compiles
    hook so BigInteger emits INTEGER on SQLite; without it every runtime table
    (not just IL3's) breaks on a SQLite runtime backend."""
    from sqlalchemy import BigInteger
    from sqlalchemy.dialects import sqlite
    from sqlalchemy.schema import CreateTable

    from anima_server.db.runtime_base import _compile_biginteger_sqlite

    # The production hook exists and maps BigInteger -> INTEGER on SQLite.
    assert _compile_biginteger_sqlite(BigInteger(), None) == "INTEGER"

    # And it takes effect in the new IL3 runtime tables' SQLite DDL: the id
    # column renders INTEGER (rowid alias -> autoincrements), never BIGINT.
    for model in (DriveStateRow, PendingInitiative):
        ddl = str(CreateTable(model.__table__).compile(dialect=sqlite.dialect()))
        assert "BIGINT" not in ddl
        assert "id INTEGER" in ddl


def test_acknowledge_unknown_pending_id_returns_none() -> None:
    engine = _create_runtime_engine()
    factory = _make_factory(engine)
    with factory() as db_:
        result = acknowledge_pending_initiative(
            db_, soul_db=None, user_id=1, pending_id=99999
        )
        assert result is None
    engine.dispose()


def test_fetch_ack_route_end_to_end() -> None:
    """The real API route (not just the service functions): firing creates
    a PendingInitiative, GET returns + marks it delivered, POST .../ack
    clears it and marks the soul-store InitiativeLog answered."""
    from conftest import managed_test_client

    with managed_test_client("anima-il3-initiative-test-") as client:
        resp = client.post(
            "/api/auth/register",
            json={"username": "il3test", "password": "pw123456", "name": "IL3 Test"},
        )
        assert resp.status_code == 201
        reg = resp.json()
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        # No pending initiatives yet.
        resp = client.get(f"/api/presence/{user_id}/initiatives", headers=headers)
        assert resp.status_code == 200
        assert resp.json() == {"userId": user_id, "initiatives": []}

        from anima_server.db.runtime import get_runtime_session_factory
        from anima_server.services.agent.inner_life.delivery import (
            PendingInitiativeDelivery,
        )

        runtime_factory = get_runtime_session_factory()
        with runtime_factory() as runtime_db:
            result = PendingInitiativeDelivery().deliver(
                runtime_db, user_id=user_id, drive=DRIVE_RELATIONAL,
                text="I've been thinking about you.", initiative_log_id=1,
            )
            runtime_db.commit()
        pending_id = result.pending_initiative_id

        resp = client.get(f"/api/presence/{user_id}/initiatives", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["initiatives"]) == 1
        assert body["initiatives"][0]["id"] == pending_id
        assert body["initiatives"][0]["drive"] == DRIVE_RELATIONAL
        assert body["initiatives"][0]["text"] == "I've been thinking about you."
        assert body["initiatives"][0]["delivered"] is True
        assert body["initiatives"][0]["acknowledged"] is False

        resp = client.post(
            f"/api/presence/{user_id}/initiatives/{pending_id}/ack", headers=headers
        )
        assert resp.status_code == 200
        assert resp.json()["acknowledged"] is True

        # Now cleared from the fetch list.
        resp = client.get(f"/api/presence/{user_id}/initiatives", headers=headers)
        assert resp.json() == {"userId": user_id, "initiatives": []}

        # Acking an unknown id 404s.
        resp = client.post(
            f"/api/presence/{user_id}/initiatives/999999/ack", headers=headers
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 7b. Signal resolution (edge reads feeding the pure accumulator signals)
# ---------------------------------------------------------------------------


def test_resolve_signals_detects_approaching_foresight_horizon(db: Session) -> None:
    runtime_engine = _create_runtime_engine()
    runtime_factory = _make_factory(runtime_engine)
    today = date(2026, 1, 1)

    db.add(
        ForesightSignal(
            user_id=1, content="dentist appointment", evidence="mentioned it",
            status="active", start_date=today + timedelta(days=1), confidence=0.9,
        )
    )
    db.commit()

    with runtime_factory() as rdb:
        signals, _ = initiative.resolve_drive_signals(
            db, rdb, user_id=1,
            now=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            last_user_turn_at=None, pattern_marker=None,
        )
    assert signals.unresolved_thread_open is True
    runtime_engine.dispose()


def test_resolve_signals_ignores_resolved_foresight_signal(db: Session) -> None:
    runtime_engine = _create_runtime_engine()
    runtime_factory = _make_factory(runtime_engine)
    today = date(2026, 1, 1)

    db.add(
        ForesightSignal(
            user_id=1, content="dentist appointment", evidence="mentioned it",
            status="resolved", start_date=today + timedelta(days=1), confidence=0.9,
        )
    )
    db.commit()

    with runtime_factory() as rdb:
        signals, _ = initiative.resolve_drive_signals(
            db, rdb, user_id=1,
            now=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            last_user_turn_at=None, pattern_marker=None,
        )
    assert signals.unresolved_thread_open is False
    runtime_engine.dispose()


def test_resolve_signals_due_and_occurred_foresight_still_count_as_open(
    db: Session,
) -> None:
    """Regression (PR review, P2): `sweep_foresight_lifecycle` promotes an
    item active -> due the moment it enters its window (the most timely
    moment), then -> occurred once past its end. Both are still OPEN
    unresolved threads and must keep the drive growing; an active-only filter
    would drop the signal exactly when the event becomes relevant. The
    material lookup uses the same open set, so it resolves too."""
    from anima_server.services.data_crypto import ef

    runtime_engine = _create_runtime_engine()
    runtime_factory = _make_factory(runtime_engine)
    today = date(2026, 1, 10)

    for status, start in (("due", today), ("occurred", today - timedelta(days=2))):
        db.add(
            ForesightSignal(
                user_id=1,
                content=ef(1, f"{status} thread", table="foresight_signals", field="content"),
                evidence="mentioned it", status=status, start_date=start, confidence=0.9,
            )
        )
    db.commit()

    with runtime_factory() as rdb:
        signals, _ = initiative.resolve_drive_signals(
            db, rdb, user_id=1,
            now=datetime(2026, 1, 10, 12, 0, tzinfo=UTC),
            last_user_turn_at=None, pattern_marker=None,
        )
    assert signals.unresolved_thread_open is True
    assert signals.unresolved_thread_resolved is False
    # The material lookup agrees with the grow signal (open set, not active-only).
    material = initiative.gather_drive_material(
        db, user_id=1, drive=DRIVE_UNRESOLVED_THREAD,
        now=datetime(2026, 1, 10, 12, 0, tzinfo=UTC),
    )
    assert "thread" in material
    runtime_engine.dispose()


def test_foresight_horizon_uses_local_tick_date_not_utc(db: Session) -> None:
    """Regression (PR review, P2): the horizon must compare foresight
    start_date (a user-local calendar date) against the LOCAL tick date, not
    the UTC date. At 00:30 local in UTC+8 the UTC date is the previous day, so
    an item at the far edge of the horizon (start_date == local_today +
    horizon) is in-window locally but would fall out if the UTC date were
    used. Both the grow signal and the material lookup must agree on local."""
    from anima_server.config import settings
    from anima_server.services.data_crypto import ef

    runtime_engine = _create_runtime_engine()
    runtime_factory = _make_factory(runtime_engine)

    tz8 = timezone(timedelta(hours=8))
    local_now = datetime(2026, 7, 21, 0, 30, tzinfo=tz8)  # UTC: 2026-07-20 16:30
    horizon = settings.initiative_unresolved_thread_horizon_days
    # Far edge of the horizon relative to the LOCAL date. Under the local date
    # (Jul 21) this is exactly in-window; under the UTC date (Jul 20) it is one
    # day past the edge and would be excluded.
    edge_start = date(2026, 7, 21) + timedelta(days=horizon)

    db.add(
        ForesightSignal(
            user_id=1,
            content=ef(1, "horizon-edge thread", table="foresight_signals", field="content"),
            evidence="mentioned it", status="active", start_date=edge_start, confidence=0.9,
        )
    )
    db.commit()

    with runtime_factory() as rdb:
        signals, _ = initiative.resolve_drive_signals(
            db, rdb, user_id=1, now=local_now,
            last_user_turn_at=None, pattern_marker=None,
        )
    assert signals.unresolved_thread_open is True  # in-window on the LOCAL date
    material = initiative.gather_drive_material(
        db, user_id=1, drive=DRIVE_UNRESOLVED_THREAD, now=local_now,
    )
    assert material == "horizon-edge thread"
    runtime_engine.dispose()


def test_material_matches_the_in_horizon_item_not_an_unrelated_open_row(
    db: Session,
) -> None:
    """Regression (PR review, P2): `gather_drive_material` must scope to the
    SAME open+in-horizon window the grow signal used. With an in-horizon row
    (which actually drove the pressure) plus an unrelated open row that either
    has no `start_date` or sits beyond the horizon, an order-by-start_date-only
    lookup could surface the wrong row (SQLite sorts NULLs first) — making the
    fired message talk about the wrong future item."""
    from anima_server.config import settings
    from anima_server.services.data_crypto import ef

    today = date(2026, 1, 10)
    horizon = settings.initiative_unresolved_thread_horizon_days

    # (a) an open row with NO start_date — would sort first under a naive ASC.
    db.add(
        ForesightSignal(
            user_id=1,
            content=ef(1, "no-date thread", table="foresight_signals", field="content"),
            evidence="x", status="active", start_date=None, confidence=0.9,
        )
    )
    # (b) an open row far beyond the horizon.
    db.add(
        ForesightSignal(
            user_id=1,
            content=ef(1, "far-future thread", table="foresight_signals", field="content"),
            evidence="x", status="active",
            start_date=today + timedelta(days=horizon + 30), confidence=0.9,
        )
    )
    # (c) the real in-horizon item that accumulated the drive.
    db.add(
        ForesightSignal(
            user_id=1,
            content=ef(1, "in-horizon thread", table="foresight_signals", field="content"),
            evidence="x", status="active", start_date=today + timedelta(days=1), confidence=0.9,
        )
    )
    db.commit()

    material = initiative.gather_drive_material(
        db, user_id=1, drive=DRIVE_UNRESOLVED_THREAD,
        now=datetime(2026, 1, 10, 12, 0, tzinfo=UTC),
    )
    assert material == "in-horizon thread"


def test_resolve_signals_closed_source_marks_thread_resolved(db: Session) -> None:
    """Regression (PR review, P2): when the foresight source closes
    (cancelled/stale, or none remains open in the horizon), the edge must
    signal `unresolved_thread_resolved` so the drive resets instead of
    lingering — otherwise leftover pressure could fire with no material."""
    runtime_engine = _create_runtime_engine()
    runtime_factory = _make_factory(runtime_engine)
    today = date(2026, 1, 10)

    db.add(
        ForesightSignal(
            user_id=1, content="cancelled plan", evidence="mentioned it",
            status="cancelled", start_date=today + timedelta(days=1), confidence=0.9,
        )
    )
    db.commit()

    with runtime_factory() as rdb:
        signals, _ = initiative.resolve_drive_signals(
            db, rdb, user_id=1,
            now=datetime(2026, 1, 10, 12, 0, tzinfo=UTC),
            last_user_turn_at=None, pattern_marker=None,
        )
    assert signals.unresolved_thread_open is False
    assert signals.unresolved_thread_resolved is True
    runtime_engine.dispose()


def test_closed_foresight_source_resets_pressure_and_blocks_fire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression (PR review, P2): high `unresolved_thread` pressure whose
    source has closed must reset (not slow-leak) so it cannot fire an
    initiative with no material. Pre-fix, `reset` was `user_turn_occurred`
    only, so a cancelled/stale source left the pressure near its old value
    and it would fire on empty material; post-fix the tick zeroes it."""
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_factory(soul_engine)
    runtime_factory = _make_factory(runtime_engine)

    # Pressure is already above threshold, but the only foresight source is
    # cancelled — the classic "fired the drive, then the plan fell through".
    _seed_enabled_user(
        soul_factory, runtime_factory,
        pressures={"unresolved_thread": 0.99},
        updated_at=datetime(2026, 1, 9, 12, 0, tzinfo=UTC),
    )
    with soul_factory() as db_:
        db_.add(
            ForesightSignal(
                user_id=1, content="cancelled plan", evidence="mentioned it",
                status="cancelled", start_date=date(2026, 1, 11), confidence=0.9,
            )
        )
        db_.commit()

    async def fake_generate(soul_db, *, user_id, decision, material, affect_line):
        raise AssertionError("must not fire — source closed, no material")

    monkeypatch.setattr(initiative, "generate_initiative_message", fake_generate)

    ok = tick_initiative_for_user(
        soul_factory, runtime_factory, user_id=1,
        local_now=datetime(2026, 1, 10, 12, 0, tzinfo=UTC),
    )
    assert ok is True  # tick succeeded (it just didn't fire)

    with soul_factory() as db_:
        assert db_.scalars(select(InitiativeLog)).all() == []
    with runtime_factory() as db_:
        row = db_.scalars(select(DriveStateRow).where(DriveStateRow.user_id == 1)).one()
        assert row.unresolved_thread == 0.0  # reset, not left lingering

    soul_engine.dispose()
    runtime_engine.dispose()


def test_resolve_signals_pattern_shareable_respects_surfaced_marker(db: Session) -> None:
    runtime_engine = _create_runtime_engine()
    runtime_factory = _make_factory(runtime_engine)

    item = MemoryItem(
        user_id=1, content="tends to avoid conflict", category="pattern",
        importance=3, source="pattern_synthesis",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    db.add(item)
    db.commit()

    with runtime_factory() as rdb:
        signals_before, _ = initiative.resolve_drive_signals(
            db, rdb, user_id=1,
            now=datetime(2026, 1, 2, tzinfo=UTC),
            last_user_turn_at=None, pattern_marker=None,
        )
        assert signals_before.pattern_shareable is True

        # A surfaced marker AFTER the item's creation means it's already
        # been shared — no longer shareable.
        signals_after, _ = initiative.resolve_drive_signals(
            db, rdb, user_id=1,
            now=datetime(2026, 1, 3, tzinfo=UTC),
            last_user_turn_at=None,
            pattern_marker=datetime(2026, 1, 2, 12, 0, tzinfo=UTC),
        )
        assert signals_after.pattern_shareable is False
    runtime_engine.dispose()


def test_resolve_signals_relational_overdue_and_user_turn_detection(db: Session) -> None:
    runtime_engine = _create_runtime_engine()
    runtime_factory = _make_factory(runtime_engine)
    last_contact = datetime(2026, 1, 1, tzinfo=UTC)

    with runtime_factory() as rdb:
        rdb.add(RuntimeThread(user_id=1, status="closed", last_message_at=last_contact))
        rdb.commit()

        signals, latest = initiative.resolve_drive_signals(
            db, rdb, user_id=1,
            now=last_contact + timedelta(days=10),
            last_user_turn_at=None, pattern_marker=None,
        )
        assert signals.relational_overdue is True
        assert signals.user_turn_occurred is True  # newer than the (None) marker
        assert latest == last_contact

        # Once the marker catches up to the latest message, it's no longer
        # a NEW turn.
        signals2, _ = initiative.resolve_drive_signals(
            db, rdb, user_id=1,
            now=last_contact + timedelta(days=10),
            last_user_turn_at=last_contact, pattern_marker=None,
        )
        assert signals2.user_turn_occurred is False
    runtime_engine.dispose()


def test_resolve_closeness_signal_saturates_with_relationship_age() -> None:
    engine = _create_runtime_engine()
    factory = _make_factory(engine)
    full_days = 120  # settings.initiative_closeness_full_days default

    with factory() as rdb:
        # No RuntimeThread at all yet -> no signal, closeness 0.
        assert initiative.resolve_closeness_signal(
            rdb, user_id=1, now=datetime(2026, 1, 1, tzinfo=UTC)
        ) == 0.0

        first_contact = datetime(2026, 1, 1, tzinfo=UTC)
        rdb.add(RuntimeThread(user_id=1, status="closed", created_at=first_contact))
        rdb.commit()

        # Deterministic under an INJECTED now — never the wall clock. Half
        # the saturation window in -> ~0.5 closeness.
        halfway_now = first_contact + timedelta(days=full_days / 2)
        closeness = initiative.resolve_closeness_signal(rdb, user_id=1, now=halfway_now)
        assert closeness == pytest.approx(0.5, abs=0.02)

        # Well past the saturation window -> clamped at 1.0, not runaway.
        far_now = first_contact + timedelta(days=full_days * 10)
        assert initiative.resolve_closeness_signal(rdb, user_id=1, now=far_now) == 1.0

        # Same instant re-queried with a DIFFERENT injected `now` gives a
        # different, reproducible result — proving it isn't reading the
        # real wall clock internally.
        earlier_now = first_contact + timedelta(days=1)
        assert initiative.resolve_closeness_signal(rdb, user_id=1, now=earlier_now) < closeness
    engine.dispose()


def test_resolve_signals_novelty_repetitive_requires_high_energy(db: Session) -> None:
    from anima_server.services.agent.inner_life.affect import AffectState
    from anima_server.services.agent.inner_life.store import save_affect_state

    runtime_engine = _create_runtime_engine()
    runtime_factory = _make_factory(runtime_engine)

    for idx in range(4):
        db.add(
            MemoryEpisode(
                user_id=1, date="2026-01-0" + str(idx + 1),
                topics_json=["work stress"], summary="talked about work again",
            )
        )
    db.commit()
    now = datetime(2026, 1, 5, tzinfo=UTC)

    with runtime_factory() as rdb:
        save_affect_state(
            rdb, user_id=1,
            state=AffectState(valence=0.0, arousal=0.5, energy=0.2, updated_at=now),
        )
        rdb.commit()

        signals_low_energy, _ = initiative.resolve_drive_signals(
            db, rdb, user_id=1, now=now, last_user_turn_at=None, pattern_marker=None,
        )
        assert signals_low_energy.novelty_repetitive is False

        save_affect_state(
            rdb, user_id=1,
            state=AffectState(valence=0.0, arousal=0.5, energy=0.9, updated_at=now),
        )
        rdb.commit()

        signals_high_energy, _ = initiative.resolve_drive_signals(
            db, rdb, user_id=1, now=now, last_user_turn_at=None, pattern_marker=None,
        )
        assert signals_high_energy.novelty_repetitive is True
    runtime_engine.dispose()


def test_resolve_signals_novel_topic_discovered_resets_novelty(db: Session) -> None:
    runtime_engine = _create_runtime_engine()
    runtime_factory = _make_factory(runtime_engine)

    db.add(MemoryEpisode(user_id=1, date="2026-01-01", topics_json=["work"], summary="a"))
    db.add(MemoryEpisode(user_id=1, date="2026-01-02", topics_json=["cooking"], summary="b"))
    db.commit()

    with runtime_factory() as rdb:
        signals, _ = initiative.resolve_drive_signals(
            db, rdb, user_id=1, now=datetime(2026, 1, 3, tzinfo=UTC),
            last_user_turn_at=None, pattern_marker=None,
        )
        # Newest episode's topic ("cooking" — highest id, since ordered
        # desc) doesn't appear in any older episode -> a genuinely new topic.
        assert signals.novel_topic_discussed is True
    runtime_engine.dispose()


# ---------------------------------------------------------------------------
# 8. Idle-only firing via the real presence tick
# ---------------------------------------------------------------------------


def test_active_user_never_fires_via_presence_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    from anima_server.services.agent.inner_life.affect import AffectState
    from anima_server.services.agent.inner_life.presence import run_presence_tick
    from anima_server.services.agent.inner_life.store import save_affect_state

    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_factory(soul_engine)
    runtime_factory = _make_factory(runtime_engine)

    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    _seed_enabled_user(
        soul_factory, runtime_factory, pressures={"relational": 0.99}, updated_at=now - timedelta(hours=1)
    )

    with runtime_factory() as db_:
        save_affect_state(
            db_, user_id=1,
            state=AffectState(valence=0.0, arousal=0.5, energy=0.5, updated_at=now - timedelta(hours=1)),
        )
        db_.add(
            RuntimeThread(
                user_id=1, status="active", last_message_at=now - timedelta(seconds=10)
            )
        )
        db_.commit()

    async def fake_generate(soul_db, *, user_id, decision, material, affect_line):
        return "should never be called"

    monkeypatch.setattr(initiative, "generate_initiative_message", fake_generate)

    result = run_presence_tick(runtime_factory, now=now, soul_db_factory_for=lambda _uid: soul_factory)
    assert result.users_skipped_active == 1
    assert result.users_ticked == 0

    with soul_factory() as db_:
        assert db_.scalars(select(InitiativeLog)).all() == []
    with runtime_factory() as db_:
        assert db_.scalars(select(PendingInitiative)).all() == []
        row = db_.scalars(select(DriveStateRow).where(DriveStateRow.user_id == 1)).one()
        assert row.relational == pytest.approx(0.99)  # never even advanced

    soul_engine.dispose()
    runtime_engine.dispose()


def test_idle_user_fires_via_presence_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    from anima_server.services.agent.inner_life.affect import AffectState
    from anima_server.services.agent.inner_life.presence import run_presence_tick
    from anima_server.services.agent.inner_life.store import save_affect_state

    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_factory(soul_engine)
    runtime_factory = _make_factory(runtime_engine)

    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    _seed_enabled_user(
        soul_factory, runtime_factory, pressures={"relational": 0.99}, updated_at=now - timedelta(hours=1)
    )
    with runtime_factory() as db_:
        save_affect_state(
            db_, user_id=1,
            state=AffectState(valence=0.0, arousal=0.5, energy=0.5, updated_at=now - timedelta(hours=1)),
        )
        db_.commit()

    async def fake_generate(soul_db, *, user_id, decision, material, affect_line):
        return "hello from idle tick"

    monkeypatch.setattr(initiative, "generate_initiative_message", fake_generate)

    result = run_presence_tick(runtime_factory, now=now, soul_db_factory_for=lambda _uid: soul_factory)
    assert result.users_ticked == 1
    assert result.users_skipped_active == 0

    with soul_factory() as db_:
        logs = db_.scalars(select(InitiativeLog)).all()
        assert len(logs) == 1
        assert logs[0].generated_text is not None

    soul_engine.dispose()
    runtime_engine.dispose()


def test_presence_tick_resolves_soul_factory_per_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: the soul store is physically per-user in the SQLite
    deployment, so `run_presence_tick` must resolve each user's OWN soul
    factory via the resolver, not reuse one shared factory for everyone. Two
    users are seeded into two SEPARATE soul databases; each user's provenance
    must land in its own DB. A resolver that ignored `user_id` (the original
    wiring bug, which passed a single static `SessionLocal`) would route both
    users to one database and fail this test."""
    from anima_server.services.agent.inner_life.affect import AffectState
    from anima_server.services.agent.inner_life.presence import run_presence_tick
    from anima_server.services.agent.inner_life.store import save_affect_state

    soul_engine_1 = _create_soul_engine()
    soul_engine_2 = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory_1 = _make_factory(soul_engine_1)
    soul_factory_2 = _make_factory(soul_engine_2)
    runtime_factory = _make_factory(runtime_engine)
    soul_factories = {1: soul_factory_1, 2: soul_factory_2}

    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    for user_id, soul_factory in soul_factories.items():
        _seed_enabled_user(
            soul_factory, runtime_factory, user_id=user_id,
            pressures={"relational": 0.99}, updated_at=now - timedelta(hours=1),
        )
        with runtime_factory() as db_:
            save_affect_state(
                db_, user_id=user_id,
                state=AffectState(valence=0.0, arousal=0.5, energy=0.5, updated_at=now - timedelta(hours=1)),
            )
            db_.commit()

    resolved_for: list[int] = []

    def resolver(user_id: int) -> sessionmaker[Session]:
        resolved_for.append(user_id)
        return soul_factories[user_id]

    async def fake_generate(soul_db, *, user_id, decision, material, affect_line):
        return f"hello user {user_id}"

    monkeypatch.setattr(initiative, "generate_initiative_message", fake_generate)

    result = run_presence_tick(runtime_factory, now=now, soul_db_factory_for=resolver)
    assert result.users_ticked == 2
    # The resolver was consulted once per idle user, with the real user_id.
    assert sorted(resolved_for) == [1, 2]

    # Each user's provenance lands in ITS OWN soul database, never the other's.
    with soul_factory_1() as db_:
        logs = db_.scalars(select(InitiativeLog)).all()
        assert [row.user_id for row in logs] == [1]
        assert logs[0].generated_text == "hello user 1"
    with soul_factory_2() as db_:
        logs = db_.scalars(select(InitiativeLog)).all()
        assert [row.user_id for row in logs] == [2]
        assert logs[0].generated_text == "hello user 2"

    soul_engine_1.dispose()
    soul_engine_2.dispose()
    runtime_engine.dispose()


def test_run_presence_tick_without_soul_factory_is_unchanged() -> None:
    """Backward compatibility: omitting soul_db_factory must not touch the
    soul store at all — existing callers/tests see no new behavior."""
    from anima_server.services.agent.inner_life.affect import AffectState
    from anima_server.services.agent.inner_life.presence import run_presence_tick
    from anima_server.services.agent.inner_life.store import save_affect_state

    runtime_engine = _create_runtime_engine()
    runtime_factory = _make_factory(runtime_engine)
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    with runtime_factory() as db_:
        save_affect_state(
            db_, user_id=1,
            state=AffectState(valence=0.0, arousal=0.5, energy=0.5, updated_at=now - timedelta(hours=1)),
        )
        db_.commit()

    result = run_presence_tick(runtime_factory, now=now)
    assert result.users_ticked == 1
    runtime_engine.dispose()


def test_per_user_initiative_failure_does_not_abort_the_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One user's initiative tick blowing up must never abort the sweep or
    prevent another idle user's initiative tick (or affect tick) from
    running — mirrors presence.py's own
    test_per_user_failure_does_not_abort_sweep, extended to IL3."""
    from anima_server.services.agent.inner_life.affect import AffectState
    from anima_server.services.agent.inner_life.presence import run_presence_tick
    from anima_server.services.agent.inner_life.store import save_affect_state

    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_factory(soul_engine)
    runtime_factory = _make_factory(runtime_engine)

    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    for user_id in (1, 2):
        _seed_enabled_user(
            soul_factory, runtime_factory, user_id=user_id,
            pressures={"relational": 0.99}, updated_at=now - timedelta(hours=1),
        )
        with runtime_factory() as db_:
            save_affect_state(
                db_, user_id=user_id,
                state=AffectState(valence=0.0, arousal=0.5, energy=0.5, updated_at=now - timedelta(hours=1)),
            )
            db_.commit()

    async def fake_generate(soul_db, *, user_id, decision, material, affect_line):
        if user_id == 1:
            raise RuntimeError("simulated generation failure for user 1")
        return "hello from user 2's tick"

    monkeypatch.setattr(initiative, "generate_initiative_message", fake_generate)

    result = run_presence_tick(runtime_factory, now=now, soul_db_factory_for=lambda _uid: soul_factory)
    # The affect tick itself never touches the soul store, so both users
    # still tick fine there regardless of IL3's failure.
    assert result.users_ticked == 2

    with soul_factory() as db_:
        logs = {row.user_id: row for row in db_.scalars(select(InitiativeLog)).all()}
        # User 1's blown-up generation still logs a best-effort failed
        # attempt (per-item isolation inside _fire itself catches the
        # exception) rather than losing the row entirely.
        assert 1 in logs
        assert logs[1].generated_text is None
        # User 2's tick is completely unaffected by user 1's failure.
        assert 2 in logs
        assert logs[2].generated_text == "hello from user 2's tick"

    soul_engine.dispose()
    runtime_engine.dispose()


def test_hard_crash_before_fire_is_isolated_to_one_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crash OUTSIDE `_fire`'s own savepoint isolation (e.g. signal
    resolution itself raising) must still be swallowed by
    `tick_initiative_for_user`'s own top-level try/except — never escaping
    to abort `run_presence_tick`'s sweep or poison another user's session."""
    from anima_server.services.agent.inner_life.affect import AffectState
    from anima_server.services.agent.inner_life.presence import run_presence_tick
    from anima_server.services.agent.inner_life.store import save_affect_state

    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_factory(soul_engine)
    runtime_factory = _make_factory(runtime_engine)

    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    for user_id in (1, 2):
        _seed_enabled_user(
            soul_factory, runtime_factory, user_id=user_id,
            pressures={"relational": 0.99}, updated_at=now - timedelta(hours=1),
        )
        with runtime_factory() as db_:
            save_affect_state(
                db_, user_id=user_id,
                state=AffectState(valence=0.0, arousal=0.5, energy=0.5, updated_at=now - timedelta(hours=1)),
            )
            db_.commit()

    original_resolve = initiative.resolve_drive_signals

    def failing_resolve(soul_db, runtime_db, *, user_id, **kwargs):
        if user_id == 1:
            raise RuntimeError("simulated hard crash resolving signals for user 1")
        return original_resolve(soul_db, runtime_db, user_id=user_id, **kwargs)

    monkeypatch.setattr(initiative, "resolve_drive_signals", failing_resolve)

    async def fake_generate(soul_db, *, user_id, decision, material, affect_line):
        return "hello from user 2's tick"

    monkeypatch.setattr(initiative, "generate_initiative_message", fake_generate)

    result = run_presence_tick(runtime_factory, now=now, soul_db_factory_for=lambda _uid: soul_factory)
    assert result.users_ticked == 2  # affect tick unaffected either way

    with soul_factory() as db_:
        logs = {row.user_id: row for row in db_.scalars(select(InitiativeLog)).all()}
        assert 1 not in logs  # user 1's crash never even reached the fire step
        assert 2 in logs
        assert logs[2].generated_text == "hello from user 2's tick"

    soul_engine.dispose()
    runtime_engine.dispose()


def test_per_user_soul_resolver_failure_does_not_abort_the_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression (PR review, P2): the per-user soul resolver runs as an
    argument to `tick_initiative_for_user`, BEFORE that function's own
    try/except. If it raises for one user (e.g. `get_user_session_factory`
    on a corrupt DB), the presence loop must still isolate the failure and
    tick every later idle user, not abort the whole sweep."""
    from anima_server.services.agent.inner_life.affect import AffectState
    from anima_server.services.agent.inner_life.presence import run_presence_tick
    from anima_server.services.agent.inner_life.store import save_affect_state

    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_factory(soul_engine)
    runtime_factory = _make_factory(runtime_engine)

    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    for user_id in (1, 2):
        _seed_enabled_user(
            soul_factory, runtime_factory, user_id=user_id,
            pressures={"relational": 0.99}, updated_at=now - timedelta(hours=1),
        )
        with runtime_factory() as db_:
            save_affect_state(
                db_, user_id=user_id,
                state=AffectState(valence=0.0, arousal=0.5, energy=0.5, updated_at=now - timedelta(hours=1)),
            )
            db_.commit()

    async def fake_generate(soul_db, *, user_id, decision, material, affect_line):
        return "hello from user 2's tick"

    monkeypatch.setattr(initiative, "generate_initiative_message", fake_generate)

    def failing_resolver(user_id: int):
        if user_id == 1:
            raise RuntimeError("simulated corrupt soul DB / migration failure")
        return soul_factory

    result = run_presence_tick(runtime_factory, now=now, soul_db_factory_for=failing_resolver)
    # The sweep completed for both users (affect tick never touches the soul
    # store) — user 1's resolver failure did not abort it.
    assert result.users_ticked == 2

    with soul_factory() as db_:
        logs = {row.user_id: row for row in db_.scalars(select(InitiativeLog)).all()}
        assert 1 not in logs  # resolver blew up before user 1 could fire
        assert 2 in logs  # user 2 still fired despite user 1's failure
        assert logs[2].generated_text == "hello from user 2's tick"

    soul_engine.dispose()
    runtime_engine.dispose()


# ---------------------------------------------------------------------------
# 9. Vault round-trip + eval reset
# ---------------------------------------------------------------------------


def test_vault_round_trip_preserves_initiative_log_full_scope(db: Session) -> None:
    log_row = InitiativeLog(
        user_id=1, fired_at=datetime(2026, 1, 1, tzinfo=UTC), drive=DRIVE_RELATIONAL,
        pressure_snapshot={"relational": 0.9}, gate_states={"enabled": True},
        generated_text="I have been thinking about you.", delivered=True, answered=False,
    )
    db.add(log_row)
    db.commit()

    snapshot = export_database_snapshot(db, user_id=1)
    assert len(snapshot["initiativeLog"]) == 1
    assert snapshot["initiativeLog"][0]["generated_text"] == "I have been thinking about you."

    db.execute(InitiativeLog.__table__.delete())
    db.expunge_all()  # drop the stale identity-mapped row the raw DELETE bypassed
    db.commit()

    restore_database_snapshot(db, snapshot, scope="full")
    db.commit()

    restored = db.scalars(select(InitiativeLog)).all()
    assert len(restored) == 1
    assert restored[0].drive == DRIVE_RELATIONAL
    assert restored[0].generated_text == "I have been thinking about you."
    assert restored[0].pressure_snapshot == {"relational": 0.9}


def test_vault_memories_scope_export_includes_initiative_log(db: Session) -> None:
    from anima_server.services.vault import _build_vault_payload

    log_row = InitiativeLog(
        user_id=1, fired_at=datetime(2026, 1, 1, tzinfo=UTC), drive=DRIVE_NOVELTY,
        pressure_snapshot={"novelty": 0.9}, gate_states={}, generated_text="hey",
        delivered=True,
    )
    db.add(log_row)
    db.commit()

    scoped = _build_vault_payload(db, scope="memories")
    assert "initiativeLog" in scoped["database"]
    assert len(scoped["database"]["initiativeLog"]) == 1

    db.execute(InitiativeLog.__table__.delete())
    db.expunge_all()
    db.commit()

    restore_database_snapshot(db, scoped["database"], scope="memories")
    db.commit()

    assert len(db.scalars(select(InitiativeLog)).all()) == 1


def test_full_scope_restore_clears_preexisting_initiative_log_rows(db: Session) -> None:
    """Regression (PR review, P1): restoring a FULL-scope vault must clear
    existing `initiative_log` rows first, exactly like its sibling
    provenance ledgers (`ReconsolidationLog`/`TendencyContribution`) already
    do — otherwise importing into a database that already has initiatives
    fired either collides on a reused id (`UNIQUE constraint failed:
    initiative_log.id`) or leaves stale rows behind (no FK to scrub via
    cascade, since InitiativeLog has none)."""
    # An initiative that already fired in THIS database before the import.
    db.add(
        InitiativeLog(
            id=1, user_id=1, fired_at=datetime(2026, 1, 1, tzinfo=UTC),
            drive=DRIVE_RELATIONAL, pressure_snapshot={}, gate_states={},
            generated_text="pre-existing local row", delivered=True,
        )
    )
    db.commit()

    snapshot = export_database_snapshot(db, user_id=1)
    assert snapshot["initiativeLog"][0]["id"] == 1

    # Simulate importing a vault whose row happens to reuse id=1 (e.g. an
    # export taken from a different point in time or a different device) —
    # exactly the "restoring into a DB that already has rows" scenario.
    snapshot["initiativeLog"][0]["generated_text"] = "restored from vault"

    restore_database_snapshot(db, snapshot, scope="full")  # must not raise
    db.commit()

    rows = db.scalars(select(InitiativeLog)).all()
    assert len(rows) == 1  # the stale local row was cleared, not duplicated
    assert rows[0].generated_text == "restored from vault"


def test_import_clears_runtime_proactive_state_for_the_user() -> None:
    """Regression (PR review, P2): a vault import replaces the soul-store
    InitiativeLog but the vault never carries runtime-tier proactive state, so
    pre-import PendingInitiative / DriveStateRow rows would otherwise survive
    and let /initiatives serve a stale message whose provenance was replaced.
    _clear_runtime_proactive_state must delete that runtime state for the
    imported user only, leaving other users untouched."""
    from anima_server.services.vault import _clear_runtime_proactive_state

    runtime_engine = _create_runtime_engine()
    runtime_factory = _make_factory(runtime_engine)

    with runtime_factory() as db_:
        for uid in (1, 2):
            db_.add(DriveStateRow(user_id=uid, relational=0.9, updated_at=datetime(2026, 1, 1, tzinfo=UTC)))
            db_.add(
                PendingInitiative(
                    user_id=uid, drive=DRIVE_RELATIONAL, text="stale pending message",
                    initiative_log_id=999, created_at=datetime(2026, 1, 1, tzinfo=UTC),
                )
            )
        db_.commit()

    _clear_runtime_proactive_state(1, runtime_factory=runtime_factory)

    with runtime_factory() as db_:
        # User 1's runtime proactive state is gone; user 2 is untouched.
        assert db_.scalars(select(PendingInitiative).where(PendingInitiative.user_id == 1)).all() == []
        assert db_.scalars(select(DriveStateRow).where(DriveStateRow.user_id == 1)).all() == []
        assert len(db_.scalars(select(PendingInitiative).where(PendingInitiative.user_id == 2)).all()) == 1
        assert len(db_.scalars(select(DriveStateRow).where(DriveStateRow.user_id == 2)).all()) == 1

    runtime_engine.dispose()


def test_eval_reset_clears_initiative_log(db: Session) -> None:
    from anima_server.services.eval_reset import _reset_soul_state

    db.add(
        InitiativeLog(
            user_id=1, fired_at=datetime(2026, 1, 1, tzinfo=UTC), drive=DRIVE_RELATIONAL,
            pressure_snapshot={}, gate_states={}, generated_text="hi", delivered=True,
        )
    )
    db.commit()
    assert db.scalars(select(InitiativeLog)).all()  # precondition

    deleted: dict[str, int] = {}
    _reset_soul_state(db, user_id=1, deleted=deleted)
    db.commit()

    assert deleted.get("initiative_log", 0) >= 1
    assert db.scalars(select(InitiativeLog)).all() == []


def test_eval_reset_clears_drive_state_and_pending_initiatives() -> None:
    from anima_server.services.eval_reset import _reset_runtime_state

    engine = _create_runtime_engine()
    factory = _make_factory(engine)
    with factory() as db_:
        db_.add(DriveStateRow(user_id=1, relational=0.5))
        db_.add(
            PendingInitiative(
                user_id=1, initiative_log_id=1, drive=DRIVE_RELATIONAL, text="hi"
            )
        )
        db_.commit()

        deleted: dict[str, int] = {}
        _reset_runtime_state(db_, user_id=1, deleted=deleted)
        db_.commit()

        assert deleted.get("drive_states", 0) >= 1
        assert deleted.get("pending_initiatives", 0) >= 1
        assert db_.scalars(select(DriveStateRow)).all() == []
        assert db_.scalars(select(PendingInitiative)).all() == []
    engine.dispose()


# ---------------------------------------------------------------------------
# 10. Zero LLM outside the single generation seam
# ---------------------------------------------------------------------------


_LLM_SEAMS = ("call_llm_for_json", "create_llm", "create_extraction_llm", "generate_initiative_message")


def test_drives_module_has_no_llm_references() -> None:
    source = inspect.getsource(drives)
    for seam in _LLM_SEAMS:
        assert seam not in source


def test_gate_chain_functions_have_no_llm_references() -> None:
    for fn in (compute_gate_states, dominant_drive, should_fire, effective_cooldown_hours):
        source = inspect.getsource(fn)
        for seam in _LLM_SEAMS:
            assert seam not in source


def test_only_generate_initiative_message_references_the_llm_seam() -> None:
    source = inspect.getsource(initiative.generate_initiative_message)
    assert "call_llm_for_text" in source

    for fn in (
        initiative.resolve_drive_signals,
        initiative.resolve_closeness_signal,
        initiative.gather_drive_material,
        initiative.count_recent_fires,
        initiative.count_unanswered_initiatives,
        initiative._get_or_seed_drive_row,
    ):
        assert "llm" not in inspect.getsource(fn).lower()
