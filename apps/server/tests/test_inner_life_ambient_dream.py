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
    real_values = proactive.get_presence_config_values

    def consent_with_rival(db_, uid):
        # Fires between the loser's consent read and its single-statement
        # conditional claim: a rival session claims the dream first.
        out = real_values(db_, uid)
        if not rival_ran["done"]:
            rival_ran["done"] = True
            with soul_factory() as rival:
                assert _resolve_ambient_dream(rival, user_id=user_id) is not None
        return out

    monkeypatch.setattr(proactive, "get_presence_config_values", consent_with_rival)
    try:
        assert _resolve_ambient_dream(loser, user_id=user_id) is None
    finally:
        loser.close()
    with soul_factory() as db:
        assert db.scalars(select(DreamJournal)).one().surfaced is True


def test_ambient_surfacing_resets_dream_residue_pressure(
    soul_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression (PR #130 round 3): surfacing a dream through the ambient
    greeting must drain the runtime dream_residue pressure (and its
    starvation history) exactly like the initiative fire path — otherwise
    pressure accumulated FOR the voiced dream lingers and transfers to the
    next unrelated dream, firing it prematurely."""
    import asyncio
    from datetime import UTC, datetime

    from anima_server.config import settings
    from anima_server.db.runtime_base import RuntimeBase
    from anima_server.models.runtime_consciousness import DriveStateRow
    from anima_server.services.agent.proactive import generate_greeting
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    monkeypatch.setattr(settings, "agent_provider", "scaffold")
    user_id = _seed(soul_db)

    rt_engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    RuntimeBase.metadata.create_all(bind=rt_engine)
    rt_factory = sessionmaker(bind=rt_engine, autoflush=False, expire_on_commit=False)
    with rt_factory() as rt:
        rt.add(
            DriveStateRow(
                user_id=user_id,
                dream_residue=0.55,
                updated_at=datetime(2026, 7, 30, tzinfo=UTC),
                starvation_losses={"dream_residue": 3, "relational": 1},
            )
        )
        rt.commit()

    with rt_factory() as rt:
        result = asyncio.run(
            generate_greeting(soul_db, user_id=user_id, runtime_db=rt)
        )
    assert "I dreamt about something recently" in result.message

    with rt_factory() as rt:
        row = rt.scalars(
            select(DriveStateRow).where(DriveStateRow.user_id == user_id)
        ).one()
        assert row.dream_residue == 0.0  # drained like the fire path
        assert row.starvation_losses == {"relational": 1}  # history cleared
    rt_engine.dispose()


def test_optout_during_the_greeting_blocks_the_claim(
    soul_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression (PR #130 round 5, P1): the consent pre-check is unlocked,
    so a presence-config PUT could commit between it and the claim and the
    stale read would authorize voicing a dream AFTER the opt-out. The claim
    now re-reads consent on fresh state while holding the same per-user
    consent lock the config PUT holds through its commit."""
    with soul_factory() as db:
        user_id = _seed(db)

    claimer = soul_factory()
    flipped = {"done": False}
    real_values = proactive.get_presence_config_values

    def optout_after_precheck(db_, uid):
        out = real_values(db_, uid)
        # Fires on the UNLOCKED pre-check: another session opts out and
        # commits before the locked re-read runs.
        if not flipped["done"]:
            flipped["done"] = True
            with soul_factory() as other:
                cfg = get_or_create_presence_config(other, uid)
                cfg.dream_sharing = "off"
                other.commit()
        return out

    monkeypatch.setattr(proactive, "get_presence_config_values", optout_after_precheck)
    try:
        assert _resolve_ambient_dream(claimer, user_id=user_id) is None
    finally:
        claimer.close()

    with soul_factory() as db:
        # The dream was NOT consumed: the opt-out wins and it stays available
        # for whenever the user turns ambient sharing back on.
        assert db.scalars(select(DreamJournal)).one().surfaced is False


def test_undecryptable_narrative_does_not_burn_the_claim(
    soul_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression (PR #130 round 6): the claim used to commit BEFORE the
    narrative was decrypted, so a corrupt ciphertext/AAD or a DEK revoked
    since the gate burned the dream on a claim nothing could voice. Decrypt
    and validate pre-commit; an unreadable narrative rolls back and the
    entry stays retriable."""
    user_id = _seed(soul_db)

    def df_raises(uid, value, **kw):
        raise RuntimeError("AAD mismatch")

    monkeypatch.setattr(proactive, "df", df_raises)
    assert _resolve_ambient_dream(soul_db, user_id=user_id) is None
    assert soul_db.scalars(select(DreamJournal)).one().surfaced is False

    # df failing OPEN (DEK gone -> stored value returned unchanged) is also
    # caught: an intact ciphertext envelope means "not decrypted".
    from anima_server.services.crypto import ENCRYPTED_PREFIX

    row = soul_db.scalars(select(DreamJournal)).one()
    row.narrative = f"{ENCRYPTED_PREFIX}:nonce:tag:ciphertext"
    soul_db.commit()
    monkeypatch.setattr(proactive, "df", lambda uid, v, **kw: v)
    assert _resolve_ambient_dream(soul_db, user_id=user_id) is None
    assert soul_db.scalars(select(DreamJournal)).one().surfaced is False


def _pill_context_fields(ctx) -> str:
    """The ctx fields generate_thought_pills renders into its prompt."""
    return " ".join(
        str(v or "")
        for v in (
            ctx.current_focus,
            ctx.emotional_summary,
            ctx.recent_episode_summary,
            ctx.working_memory_summary,
        )
    )


def test_dream_never_reaches_the_pill_llm_request(
    soul_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression (PR #130 round 7, P1): generate_thought_pills issues a
    SECOND LLM request. Appending the dream before that call shipped the
    decrypted narrative to whatever provider is configured — including cloud
    providers, against the on-device promise and this feature's own rule
    that the dream never enters an LLM prompt. Pills are generated from the
    model's own greeting; the dream is appended afterwards."""
    import asyncio

    from anima_server.config import settings
    from anima_server.services.agent.proactive import generate_greeting

    user_id = _seed(soul_db)
    seen: dict[str, str] = {}

    async def fake_llm_greeting(messages, **kw):
        return "Welcome back."

    async def spy_pills(prompt_loader, *, greeting_message, ctx):
        seen["greeting_message"] = greeting_message
        return [{"kind": "topic", "label": "welcome"}]

    monkeypatch.setattr(settings, "agent_provider", "ollama")
    monkeypatch.setattr(proactive, "_invoke_ollama_native_chat", fake_llm_greeting)
    monkeypatch.setattr(proactive, "generate_thought_pills", spy_pills)

    result = asyncio.run(generate_greeting(soul_db, user_id=user_id, runtime_db=None))

    # The dream IS voiced to the user...
    assert "a blurred dream about the boat you restored" in result.message
    # ...but never appeared in the text handed to the pill model.
    assert seen["greeting_message"] == "Welcome back."
    assert "boat you restored" not in seen["greeting_message"]
    # generate_thought_pills also renders four ctx fields; none may carry the
    # dream, asserted so a future change that starts doing so fails loudly.
    assert "boat you restored" not in _pill_context_fields(result.context)
