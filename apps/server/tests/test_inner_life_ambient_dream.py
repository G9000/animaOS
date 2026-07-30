"""Tests for IL-010 — the ambient dream-sharing greeting consumer.

Covers ``proactive._resolve_ambient_dream``: the mode gate (`ambient` only —
``on_ask`` and ``off`` stay untouched), the Presence master switch, the DEK
gate, consume-once semantics (marked surfaced + committed on hand-off, never
voiced twice), share-worthy/unsurfaced selection, truncation, and the
static-greeting rendering.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from anima_server.db.base import Base
from anima_server.models import DreamJournal, User
from anima_server.services.agent import proactive
from anima_server.services.agent.proactive import (
    GreetingContext,
    _resolve_ambient_dream,
    build_static_greeting,
)
from anima_server.services.presence_config import get_or_create_presence_config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

NIGHT = datetime(2026, 7, 28, 2, 0, tzinfo=UTC)


@pytest.fixture()
def soul_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    engine.dispose()


@pytest.fixture()
def soul_db(soul_factory):
    with soul_factory() as session:
        yield session


@pytest.fixture(autouse=True)
def _crypto_passthrough(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(proactive, "df", lambda user_id, v, **kw: v)
    from anima_server.services import sessions

    monkeypatch.setattr(sessions, "get_active_dek", lambda user_id, domain=None: b"dek")


def _seed(
    soul_db: Session,
    *,
    dream_sharing: str = "ambient",
    share_worthy: bool = True,
    surfaced: bool = False,
    narrative: str = "a blurred dream about the boat you restored",
) -> int:
    user = User(username="il010", password_hash="x", display_name="IL010")
    soul_db.add(user)
    soul_db.flush()
    cfg = get_or_create_presence_config(soul_db, user.id)
    cfg.dream_sharing = dream_sharing
    soul_db.add(
        DreamJournal(
            user_id=user.id,
            dreamt_at=NIGHT,
            narrative=narrative,
            share_worthy=share_worthy,
            surfaced=surfaced,
            source_refs={"memory_item_ids": [1]},
            affect_delta={"valence": 0.1, "arousal": 0.0, "energy": -0.05},
        )
    )
    soul_db.commit()
    return user.id


def test_ambient_mode_returns_dream_and_marks_it_surfaced(soul_db) -> None:
    user_id = _seed(soul_db)
    dream = _resolve_ambient_dream(soul_db, user_id=user_id)
    assert dream == "a blurred dream about the boat you restored"
    row = soul_db.scalars(select(DreamJournal)).one()
    assert row.surfaced is True  # consume-once: stops re-raising dream_residue


def test_surfaced_mark_is_committed_not_just_flushed(soul_factory) -> None:
    """The mark must survive the resolving session — a crash or rollback
    after the greeting is served must not let the dream be voiced twice."""
    with soul_factory() as db:
        user_id = _seed(db)
        assert _resolve_ambient_dream(db, user_id=user_id) is not None
        db.rollback()  # anything uncommitted would be lost here
    with soul_factory() as db:
        assert db.scalars(select(DreamJournal)).one().surfaced is True


def test_second_greeting_gets_nothing_after_consumption(soul_db) -> None:
    user_id = _seed(soul_db)
    assert _resolve_ambient_dream(soul_db, user_id=user_id) is not None
    assert _resolve_ambient_dream(soul_db, user_id=user_id) is None


def test_on_ask_and_off_modes_never_touch_the_dream(soul_db) -> None:
    """on_ask stays ask-or-IL3-fire only; off stays fully suppressed —
    neither returns a dream nor marks anything surfaced."""
    user_id = _seed(soul_db, dream_sharing="on_ask")
    assert _resolve_ambient_dream(soul_db, user_id=user_id) is None
    cfg = get_or_create_presence_config(soul_db, user_id)
    cfg.dream_sharing = "off"
    soul_db.commit()
    assert _resolve_ambient_dream(soul_db, user_id=user_id) is None
    assert soul_db.scalars(select(DreamJournal)).one().surfaced is False


def test_presence_master_switch_off_suppresses_ambient(soul_db) -> None:
    user_id = _seed(soul_db)
    cfg = get_or_create_presence_config(soul_db, user_id)
    cfg.enabled = False
    soul_db.commit()
    assert _resolve_ambient_dream(soul_db, user_id=user_id) is None
    assert soul_db.scalars(select(DreamJournal)).one().surfaced is False


def test_no_dek_means_no_dream_and_no_consumption(
    soul_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """df fails open: without a DEK the narrative read would return
    ciphertext into the greeting — and the dream must NOT be burned."""
    user_id = _seed(soul_db)
    from anima_server.services import sessions

    monkeypatch.setattr(sessions, "get_active_dek", lambda user_id, domain=None: None)
    assert _resolve_ambient_dream(soul_db, user_id=user_id) is None
    assert soul_db.scalars(select(DreamJournal)).one().surfaced is False


def test_non_share_worthy_or_already_surfaced_dreams_are_skipped(soul_db) -> None:
    user_id = _seed(soul_db, share_worthy=False)
    assert _resolve_ambient_dream(soul_db, user_id=user_id) is None

    soul_db.add(
        DreamJournal(
            user_id=user_id,
            dreamt_at=NIGHT + timedelta(days=1),
            narrative="already voiced",
            share_worthy=True,
            surfaced=True,
            source_refs={"memory_item_ids": [2]},
            affect_delta={"valence": 0.0, "arousal": 0.0, "energy": 0.0},
        )
    )
    soul_db.commit()
    assert _resolve_ambient_dream(soul_db, user_id=user_id) is None


def test_most_recent_share_worthy_unsurfaced_dream_wins(soul_db) -> None:
    user_id = _seed(soul_db, narrative="older dream")
    soul_db.add(
        DreamJournal(
            user_id=user_id,
            dreamt_at=NIGHT + timedelta(days=2),
            narrative="newer dream",
            share_worthy=True,
            surfaced=False,
            source_refs={"memory_item_ids": [3]},
            affect_delta={"valence": 0.0, "arousal": 0.0, "energy": 0.0},
        )
    )
    soul_db.commit()
    assert _resolve_ambient_dream(soul_db, user_id=user_id) == "newer dream"


def test_narrative_is_truncated_to_240_chars(soul_db) -> None:
    user_id = _seed(soul_db, narrative="x" * 600)
    assert _resolve_ambient_dream(soul_db, user_id=user_id) == "x" * 240


def test_static_greeting_renders_ambient_dream(soul_db) -> None:
    message = build_static_greeting(
        GreetingContext(days_since_last_chat=1, ambient_dream="the boat dream")
    )
    assert "the boat dream" in message
    assert "dreamt" in message
    assert "dreamt" not in build_static_greeting(GreetingContext(days_since_last_chat=1))


def test_gather_greeting_context_never_consumes_the_dream(soul_db) -> None:
    """Regression (PR #130 review, P1): gather_greeting_context is shared
    with non-greeting paths (agent state, reflection) that never render the
    dream — it must NOT resolve/claim. Only generate_greeting consumes."""
    from anima_server.services.agent.proactive import gather_greeting_context

    user_id = _seed(soul_db)
    ctx = gather_greeting_context(soul_db, user_id=user_id, runtime_db=None)
    assert ctx.ambient_dream is None
    assert soul_db.scalars(select(DreamJournal)).one().surfaced is False


def test_generate_greeting_voices_and_consumes_the_claimed_dream(
    soul_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one consuming path guarantees voicing: the greeting message
    always contains the dream sentence (static path here; the LLM path
    appends the SAME sentence deterministically rather than trusting the
    model), and only then is the dream consumed."""
    import asyncio

    from anima_server.config import settings
    from anima_server.services.agent.proactive import generate_greeting

    monkeypatch.setattr(settings, "agent_provider", "scaffold")
    user_id = _seed(soul_db)
    result = asyncio.run(generate_greeting(soul_db, user_id=user_id, runtime_db=None))
    assert "a blurred dream about the boat you restored" in result.message
    assert "I dreamt about something recently" in result.message
    assert soul_db.scalars(select(DreamJournal)).one().surfaced is True


def test_concurrent_claim_returns_the_dream_to_exactly_one_caller(
    soul_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression (PR #130 review): the claim is a CONDITIONAL update. If a
    rival claims the row between this session's select and its update (here:
    interleaved via the df seam), the update's rowcount is 0 and the loser
    returns None instead of double-voicing the same narrative."""
    with soul_factory() as db:
        user_id = _seed(db)

    loser = soul_factory()
    rival_ran = {"done": False}

    def df_with_rival(uid, v, **kw):
        # Fires between the loser's SELECT and its conditional UPDATE:
        # a rival session claims the dream first.
        if not rival_ran["done"]:
            rival_ran["done"] = True
            with soul_factory() as rival:
                assert _resolve_ambient_dream(rival, user_id=user_id) is not None
        return v

    monkeypatch.setattr(proactive, "df", df_with_rival)
    try:
        assert _resolve_ambient_dream(loser, user_id=user_id) is None
    finally:
        loser.close()
    with soul_factory() as db:
        assert db.scalars(select(DreamJournal)).one().surfaced is True
