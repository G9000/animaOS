"""Tests for IL-011 — the grounded held-thought greeting context.

Covers ``proactive._resolve_held_thought``'s four grounding conditions
(consent, real absence, accumulated pressure, existing material), the DEK
gate (``df`` fails open, so no DEK must mean no held thought), truncation,
and the static-greeting rendering. The never-confabulate rule is the point:
flipping ANY single condition off must yield None.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from anima_server.db.base import Base
from anima_server.db.runtime_base import RuntimeBase
from anima_server.models import ForesightSignal, User
from anima_server.models.runtime_consciousness import DriveStateRow
from anima_server.services.agent import proactive
from anima_server.services.agent.proactive import (
    GreetingContext,
    _resolve_held_thought,
    build_static_greeting,
)
from anima_server.services.presence_config import get_or_create_presence_config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

NOW = datetime(2026, 7, 29, 9, 0, tzinfo=UTC)
LONG_AGO = NOW - timedelta(hours=30)  # well past the 8h default gap floor


def _engine(base):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    base.metadata.create_all(bind=engine)
    return engine


@pytest.fixture()
def soul_db():
    engine = _engine(Base)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


@pytest.fixture()
def runtime_db():
    engine = _engine(RuntimeBase)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


@pytest.fixture(autouse=True)
def _crypto_passthrough(monkeypatch: pytest.MonkeyPatch):
    """df passthrough + an active DEK — individual tests override the DEK."""
    monkeypatch.setattr(proactive, "df", lambda user_id, v, **kw: v)
    from anima_server.services import sessions

    monkeypatch.setattr(sessions, "get_active_dek", lambda user_id, domain=None: b"dek")


def _seed_all_conditions(
    soul_db: Session,
    runtime_db: Session,
    *,
    consent: bool = True,
    pressure: float = 0.6,
    content: str = "the gallery opening they were nervous about",
) -> int:
    user = User(username="il011", password_hash="x", display_name="IL011")
    soul_db.add(user)
    soul_db.flush()
    cfg = get_or_create_presence_config(soul_db, user.id)
    cfg.home_greeting_context_enabled = consent
    soul_db.add(
        ForesightSignal(
            user_id=user.id,
            content=content,
            evidence="mentioned twice",
            status="due",
            start_date=NOW.date() + timedelta(days=1),
            confidence=0.9,
        )
    )
    soul_db.commit()
    runtime_db.add(
        DriveStateRow(
            user_id=user.id,
            unresolved_thread=pressure,
            updated_at=LONG_AGO,
            # The drive tick has processed the user's last message (equal
            # timestamps): the stored pressure post-dates the turn reset.
            last_user_turn_at=LONG_AGO,
        )
    )
    runtime_db.commit()
    return user.id


def test_held_thought_present_when_all_conditions_hold(soul_db, runtime_db) -> None:
    user_id = _seed_all_conditions(soul_db, runtime_db)
    thought = _resolve_held_thought(
        soul_db, runtime_db, user_id=user_id, last_message_at=LONG_AGO, now=NOW
    )
    assert thought == "the gallery opening they were nervous about"


def test_no_held_thought_without_greeting_consent(soul_db, runtime_db) -> None:
    user_id = _seed_all_conditions(soul_db, runtime_db, consent=False)
    assert (
        _resolve_held_thought(
            soul_db, runtime_db, user_id=user_id, last_message_at=LONG_AGO, now=NOW
        )
        is None
    )


def test_no_held_thought_when_presence_master_switch_off(soul_db, runtime_db) -> None:
    user_id = _seed_all_conditions(soul_db, runtime_db)
    cfg = get_or_create_presence_config(soul_db, user_id)
    cfg.enabled = False
    soul_db.commit()
    assert (
        _resolve_held_thought(
            soul_db, runtime_db, user_id=user_id, last_message_at=LONG_AGO, now=NOW
        )
        is None
    )


def test_no_held_thought_for_short_gap_or_first_meeting(soul_db, runtime_db) -> None:
    user_id = _seed_all_conditions(soul_db, runtime_db)
    # 2h absence: they were barely gone — claiming preoccupation would be
    # theater, not memory.
    recent = NOW - timedelta(hours=2)
    assert (
        _resolve_held_thought(
            soul_db, runtime_db, user_id=user_id, last_message_at=recent, now=NOW
        )
        is None
    )
    # First meeting: nothing was ever left open.
    assert (
        _resolve_held_thought(
            soul_db, runtime_db, user_id=user_id, last_message_at=None, now=NOW
        )
        is None
    )


def test_no_held_thought_without_accumulated_pressure(soul_db, runtime_db) -> None:
    """The foresight row alone is NOT enough — pressure must have genuinely
    built over the gap, otherwise the greeting would fake preoccupation."""
    user_id = _seed_all_conditions(soul_db, runtime_db, pressure=0.1)
    assert (
        _resolve_held_thought(
            soul_db, runtime_db, user_id=user_id, last_message_at=LONG_AGO, now=NOW
        )
        is None
    )
    # No drive row at all (fresh user) behaves the same as low pressure.
    assert (
        _resolve_held_thought(
            soul_db, runtime_db, user_id=user_id + 1, last_message_at=LONG_AGO, now=NOW
        )
        is None
    )


def test_no_held_thought_without_open_foresight_material(soul_db, runtime_db) -> None:
    """Pressure without material must stay silent (never synthesize)."""
    user_id = _seed_all_conditions(soul_db, runtime_db)
    fs = soul_db.query(ForesightSignal).one()
    fs.status = "resolved"
    soul_db.commit()
    assert (
        _resolve_held_thought(
            soul_db, runtime_db, user_id=user_id, last_message_at=LONG_AGO, now=NOW
        )
        is None
    )


def test_no_held_thought_without_active_dek(
    soul_db, runtime_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """df fails open — without a DEK the read would hand ciphertext to the
    greeting prompt, so the held thought must be skipped entirely."""
    user_id = _seed_all_conditions(soul_db, runtime_db)
    from anima_server.services import sessions

    monkeypatch.setattr(sessions, "get_active_dek", lambda user_id, domain=None: None)
    assert (
        _resolve_held_thought(
            soul_db, runtime_db, user_id=user_id, last_message_at=LONG_AGO, now=NOW
        )
        is None
    )


def test_no_held_thought_without_runtime_db(soul_db) -> None:
    assert (
        _resolve_held_thought(
            soul_db, None, user_id=1, last_message_at=LONG_AGO, now=NOW
        )
        is None
    )


def test_held_thought_is_truncated_to_200_chars(soul_db, runtime_db) -> None:
    user_id = _seed_all_conditions(soul_db, runtime_db, content="x" * 500)
    thought = _resolve_held_thought(
        soul_db, runtime_db, user_id=user_id, last_message_at=LONG_AGO, now=NOW
    )
    assert thought == "x" * 200


def test_static_greeting_renders_held_thought(soul_db, runtime_db) -> None:
    ctx = GreetingContext(
        days_since_last_chat=3, held_thought="the trip you were planning"
    )
    message = build_static_greeting(ctx)
    assert "the trip you were planning" in message
    # And absent when there is no held thought.
    assert "stayed with me" not in build_static_greeting(
        GreetingContext(days_since_last_chat=3)
    )


def test_no_held_thought_when_pressure_predates_the_latest_user_turn(
    soul_db, runtime_db
) -> None:
    """Regression (PR #128 review round 2): a user turn hard-resets
    unresolved_thread, but only when a tick processes it. Pressure whose
    last_user_turn_at is older than the latest message (or missing entirely)
    is a pre-turn leftover — grounding on it would claim a thread 'stayed
    with me' that the user's own last message already addressed."""
    user_id = _seed_all_conditions(soul_db, runtime_db)
    row = runtime_db.query(DriveStateRow).one()

    # Tick lags the latest message: reject.
    row.last_user_turn_at = LONG_AGO - timedelta(hours=1)
    runtime_db.commit()
    assert (
        _resolve_held_thought(
            soul_db, runtime_db, user_id=user_id, last_message_at=LONG_AGO, now=NOW
        )
        is None
    )

    # No tick ever saw a user turn at all: reject too.
    row.last_user_turn_at = None
    runtime_db.commit()
    assert (
        _resolve_held_thought(
            soul_db, runtime_db, user_id=user_id, last_message_at=LONG_AGO, now=NOW
        )
        is None
    )


def test_foresight_horizon_uses_the_local_calendar_date(soul_db, runtime_db) -> None:
    """Regression (PR #128 review round 2): the horizon must be evaluated on
    the LOCAL calendar date, like the drive tick that accumulated the
    pressure. At 23:00 UTC in a UTC+14 zone the local date is already
    tomorrow; a boundary foresight signal is in-horizon locally but
    out-of-horizon by the UTC date."""
    from zoneinfo import ZoneInfo

    from anima_server.config import settings

    tz = ZoneInfo("Etc/GMT-14")  # UTC+14 (POSIX sign convention)
    now = datetime(2026, 7, 29, 23, 0, tzinfo=UTC)  # locally: 2026-07-30 13:00
    last = now - timedelta(hours=30)
    horizon = int(settings.initiative_unresolved_thread_horizon_days)
    boundary = date(2026, 7, 30) + timedelta(days=horizon)  # local_today + horizon

    user_id = _seed_all_conditions(soul_db, runtime_db)
    row = runtime_db.query(DriveStateRow).one()
    row.last_user_turn_at = last
    runtime_db.commit()
    fs = soul_db.query(ForesightSignal).one()
    fs.start_date = boundary
    soul_db.commit()

    thought = _resolve_held_thought(
        soul_db, runtime_db, user_id=user_id, last_message_at=last, now=now, tz=tz
    )
    # In-horizon on the local calendar (boundary == local_today + horizon);
    # the UTC date (still 07-29) would wrongly exclude it.
    assert thought == "the gallery opening they were nervous about"
