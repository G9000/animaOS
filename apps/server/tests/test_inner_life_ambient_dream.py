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


def test_ambient_mode_claims_the_dream_without_surfacing_it(soul_db) -> None:
    """IL-015: a claim suppresses re-offering but is not proof of
    delivery — only an acknowledged receipt sets `surfaced`."""
    user_id = _seed(soul_db)
    claim = _resolve_ambient_dream(soul_db, user_id=user_id)
    assert claim is not None
    assert claim.narrative == "a blurred dream about the boat you restored"
    row = soul_db.scalars(select(DreamJournal)).one()
    assert row.claimed_at is not None  # claimed: not re-offered meanwhile
    assert row.surfaced is False  # not yet acknowledged by any client


def test_claim_is_committed_not_just_flushed(soul_factory) -> None:
    """The mark must survive the resolving session — a crash or rollback
    after the greeting is served must not let the dream be voiced twice."""
    with soul_factory() as db:
        user_id = _seed(db)
        assert _resolve_ambient_dream(db, user_id=user_id) is not None
        db.rollback()  # anything uncommitted would be lost here
    with soul_factory() as db:
        assert db.scalars(select(DreamJournal)).one().claimed_at is not None


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
    assert _resolve_ambient_dream(soul_db, user_id=user_id).narrative == "newer dream"


def test_narrative_is_truncated_to_240_chars(soul_db) -> None:
    user_id = _seed(soul_db, narrative="x" * 600)
    assert _resolve_ambient_dream(soul_db, user_id=user_id).narrative == "x" * 240


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
    row = soul_db.scalars(select(DreamJournal)).one()
    assert row.claimed_at is not None  # claimed by this greeting
    assert row.surfaced is False  # until the client acknowledges receipt


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
        assert db.scalars(select(DreamJournal)).one().claimed_at is not None


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


def test_greeting_exposes_a_dream_free_handoff_copy(
    soul_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression (PR #130 round 8, P1): the dashboard "explore" action
    forwards the greeting into chat context, which the server places in the
    model history — so the dream would reach the main chat LLM by a second
    route. GreetingResult now carries a dream-free copy for handoff
    surfaces, distinct from the displayed message."""
    import asyncio

    from anima_server.config import settings
    from anima_server.services.agent.proactive import generate_greeting

    monkeypatch.setattr(settings, "agent_provider", "scaffold")
    user_id = _seed(soul_db)
    result = asyncio.run(generate_greeting(soul_db, user_id=user_id, runtime_db=None))

    assert "a blurred dream about the boat you restored" in result.message
    assert result.handoff_message is not None
    assert "boat you restored" not in result.handoff_message
    assert "dreamt" not in result.handoff_message


def test_handoff_copy_is_absent_when_no_dream_was_woven(
    soul_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No dream means the displayed message is already safe — None signals
    'use message as-is' rather than forcing callers to compare strings."""
    import asyncio

    from anima_server.config import settings
    from anima_server.services.agent.proactive import generate_greeting

    monkeypatch.setattr(settings, "agent_provider", "scaffold")
    user_id = _seed(soul_db, dream_sharing="off")
    result = asyncio.run(generate_greeting(soul_db, user_id=user_id, runtime_db=None))
    assert result.handoff_message is None


def test_optout_during_generation_releases_the_claim_unvoiced(
    soul_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression (PR #130 round 9, P1): the claim commits BEFORE the
    greeting and pill LLM calls (~14s combined). An opt-out committing in
    that window used to still be answered with a dream. Consent is now
    re-checked after generation; because the server knows the narrative has
    not reached the user, the claim is RELEASED rather than burned — the
    dream stays available for a later greeting."""
    import asyncio

    from anima_server.config import settings
    from anima_server.services.agent.proactive import generate_greeting

    monkeypatch.setattr(settings, "agent_provider", "scaffold")
    user_id = _seed(soul_db)

    def optout_mid_generation(runtime_db, *, user_id):
        # Runs between the claim commit and the consent revalidation — the
        # same position the greeting/pill LLM calls occupy in real requests.
        cfg = get_or_create_presence_config(soul_db, user_id)
        cfg.dream_sharing = "off"
        soul_db.commit()

    monkeypatch.setattr(
        proactive, "_reset_dream_residue_after_surfacing", optout_mid_generation
    )
    result = asyncio.run(generate_greeting(soul_db, user_id=user_id, runtime_db=None))

    assert "boat you restored" not in result.message  # never voiced
    assert result.context.ambient_dream is None
    # Released, not burned: still available once the user re-enables ambient.
    assert soul_db.scalars(select(DreamJournal)).one().surfaced is False


def test_final_consent_decision_is_serialized_with_config_updates(
    soul_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression (PR #130 round 10, P1): round 9's post-generation recheck
    was an UNLOCKED read, so an opt-out could still commit between that read
    and the append. The whole final decision — re-read, then release-or-append
    — now runs under the same per-user presence_consent_lock the config PUT
    holds through its commit, so an opt-out cannot interleave."""
    import inspect

    from anima_server.services.agent.proactive import _finalize_ambient_dream

    source = inspect.getsource(_finalize_ambient_dream)
    # The append must happen INSIDE the lock, not after it.
    assert "with presence_consent_lock(user_id):" in source
    lock_at = source.index("with presence_consent_lock(user_id):")
    assert source.index("_ambient_dream_sentence(ctx.ambient_dream)") > lock_at
    assert source.index("_release_ambient_dream_claim") > lock_at


def test_optout_between_recheck_and_append_cannot_voice_the_dream(
    soul_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The behavioral half of the above: an opt-out committing while the
    final decision holds the lock must not produce a dream-bearing
    greeting, and the claim is released rather than burned."""
    import asyncio

    from anima_server.config import settings
    from anima_server.services.agent.proactive import generate_greeting

    monkeypatch.setattr(settings, "agent_provider", "scaffold")
    user_id = _seed(soul_db)

    def optout_mid_generation(runtime_db, *, user_id):
        cfg = get_or_create_presence_config(soul_db, user_id)
        cfg.dream_sharing = "off"
        soul_db.commit()

    monkeypatch.setattr(
        proactive, "_reset_dream_residue_after_surfacing", optout_mid_generation
    )
    result = asyncio.run(generate_greeting(soul_db, user_id=user_id, runtime_db=None))

    assert "boat you restored" not in result.message
    assert result.handoff_message is None
    assert soul_db.scalars(select(DreamJournal)).one().surfaced is False


# ---------------------------------------------------------------------------
# IL-015 — claim / acknowledge / expiry
# ---------------------------------------------------------------------------


def test_acknowledgement_surfaces_the_dream_and_clears_the_claim(soul_db) -> None:
    """The client's receipt is what makes surfacing durable."""
    from anima_server.services.agent.inner_life.dream_receipt import (
        acknowledge_dream,
        claim_token,
    )

    user_id = _seed(soul_db)
    claim = _resolve_ambient_dream(soul_db, user_id=user_id)
    assert claim is not None

    assert acknowledge_dream(
        soul_db, user_id=user_id, dream_id=claim.dream_id,
        token=claim_token(claim.claimed_at),
    ) is True
    soul_db.commit()
    row = soul_db.scalars(select(DreamJournal)).one()
    assert row.surfaced is True
    assert row.claimed_at is None  # no expiry logic ever revisits it


def test_acknowledgement_is_idempotent_and_ownership_scoped(soul_db) -> None:
    """A retried ack, or one for someone else's dream, is a no-op — the
    client acks best-effort and must not be penalised for retrying."""
    from anima_server.services.agent.inner_life.dream_receipt import (
        acknowledge_dream,
        claim_token,
    )

    user_id = _seed(soul_db)
    claim = _resolve_ambient_dream(soul_db, user_id=user_id)
    assert claim is not None
    assert acknowledge_dream(
        soul_db, user_id=user_id, dream_id=claim.dream_id,
        token=claim_token(claim.claimed_at),
    ) is True
    soul_db.commit()

    # Second ack: already surfaced, nothing to do.
    assert acknowledge_dream(
        soul_db, user_id=user_id, dream_id=claim.dream_id,
        token=claim_token(claim.claimed_at),
    ) is False
    # Another user's ack must never touch this row.
    assert acknowledge_dream(
        soul_db, user_id=user_id + 99, dream_id=claim.dream_id,
        token=claim_token(claim.claimed_at),
    ) is False
    soul_db.commit()
    assert soul_db.scalars(select(DreamJournal)).one().surfaced is True


def test_unacknowledged_claim_expires_and_the_dream_is_offered_again(soul_db) -> None:
    """THE point of IL-015: a greeting whose response never reached the
    browser used to consume the dream forever. The claim now expires and the
    dream comes back."""
    from anima_server.config import settings

    user_id = _seed(soul_db)
    first = _resolve_ambient_dream(soul_db, user_id=user_id)
    assert first is not None

    # Inside the TTL the dream stays held — no double-voicing.
    assert _resolve_ambient_dream(soul_db, user_id=user_id) is None

    # Age the claim past the TTL: the response demonstrably never landed.
    row = soul_db.scalars(select(DreamJournal)).one()
    row.claimed_at = datetime.now(UTC) - timedelta(
        minutes=settings.dream_claim_ttl_minutes + 1
    )
    soul_db.commit()

    second = _resolve_ambient_dream(soul_db, user_id=user_id)
    assert second is not None
    assert second.dream_id == first.dream_id  # the same dream, offered again


def test_acknowledged_dream_is_never_offered_again_even_after_the_ttl(soul_db) -> None:
    """Expiry must only rescue UNDELIVERED dreams — a dream the user
    actually saw stays gone, however much time passes."""
    from anima_server.config import settings
    from anima_server.services.agent.inner_life.dream_receipt import (
        acknowledge_dream,
        claim_token,
    )

    user_id = _seed(soul_db)
    claim = _resolve_ambient_dream(soul_db, user_id=user_id)
    assert claim is not None
    acknowledge_dream(
        soul_db, user_id=user_id, dream_id=claim.dream_id,
        token=claim_token(claim.claimed_at),
    )
    soul_db.commit()

    row = soul_db.scalars(select(DreamJournal)).one()
    row.claimed_at = datetime.now(UTC) - timedelta(
        minutes=settings.dream_claim_ttl_minutes + 1000
    )
    soul_db.commit()
    assert _resolve_ambient_dream(soul_db, user_id=user_id) is None


def test_generate_greeting_returns_the_dream_id_for_acknowledgement(
    soul_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The client cannot ack what it cannot identify."""
    import asyncio

    from anima_server.config import settings
    from anima_server.services.agent.proactive import generate_greeting

    monkeypatch.setattr(settings, "agent_provider", "scaffold")
    user_id = _seed(soul_db)
    result = asyncio.run(generate_greeting(soul_db, user_id=user_id, runtime_db=None))

    assert result.ambient_dream_id is not None
    assert "boat you restored" in result.message
    row = soul_db.scalars(select(DreamJournal)).one()
    assert result.ambient_dream_id == row.id


def test_no_dream_means_no_dream_id(soul_db, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from anima_server.config import settings
    from anima_server.services.agent.proactive import generate_greeting

    monkeypatch.setattr(settings, "agent_provider", "scaffold")
    user_id = _seed(soul_db, dream_sharing="off")
    result = asyncio.run(generate_greeting(soul_db, user_id=user_id, runtime_db=None))
    assert result.ambient_dream_id is None


def test_initiative_cannot_consume_a_live_greeting_claim(soul_db) -> None:
    """Regression (PR #135 review, P1): IL-015 made the greeting path claim
    without surfacing, but IL-003's dream_residue paths still selected on
    `surfaced` alone — so an initiative tick overlapping an unacknowledged
    greeting could voice the SAME intimate dream through a second channel.
    All three initiative paths now use the offerable predicate."""
    from anima_server.config import settings
    from anima_server.services.agent.inner_life import initiative as il

    user_id = _seed(soul_db)
    now = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)

    # Before any claim the initiative path can see the dream.
    assert il.gather_drive_material(
        soul_db, user_id=user_id, drive="dream_residue", now=now, dream_sharing="ambient"
    ).strip()

    # A greeting claims it — disclosure is in flight through the ambient
    # channel and the initiative must not duplicate it.
    claim = _resolve_ambient_dream(soul_db, user_id=user_id)
    assert claim is not None
    assert soul_db.scalars(select(DreamJournal)).one().surfaced is False  # claim only
    assert (
        il.gather_drive_material(
            soul_db, user_id=user_id, drive="dream_residue", now=now,
            dream_sharing="ambient",
        )
        == ""
    )

    # Once the claim expires (the greeting never landed), the initiative may
    # speak it — the dream isn't lost to the failed delivery.
    row = soul_db.scalars(select(DreamJournal)).one()
    row.claimed_at = datetime.now(UTC) - timedelta(
        minutes=settings.dream_claim_ttl_minutes + 1
    )
    soul_db.commit()
    assert il.gather_drive_material(
        soul_db, user_id=user_id, drive="dream_residue", now=now, dream_sharing="ambient"
    ).strip()


def test_acknowledged_dream_is_invisible_to_the_initiative_path(soul_db) -> None:
    """The converse: a dream the ambient channel actually delivered must
    never be re-voiced as an initiative."""
    from anima_server.services.agent.inner_life import initiative as il
    from anima_server.services.agent.inner_life.dream_receipt import (
        acknowledge_dream,
        claim_token,
    )

    user_id = _seed(soul_db)
    claim = _resolve_ambient_dream(soul_db, user_id=user_id)
    assert claim is not None
    acknowledge_dream(
        soul_db, user_id=user_id, dream_id=claim.dream_id,
        token=claim_token(claim.claimed_at),
    )
    soul_db.commit()

    assert (
        il.gather_drive_material(
            soul_db, user_id=user_id, drive="dream_residue",
            now=datetime(2026, 7, 30, 12, 0, tzinfo=UTC), dream_sharing="ambient",
        )
        == ""
    )


def test_greeting_states_when_its_claim_expires(
    soul_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression (PR #135 review, P1): the client may STORE a dream-bearing
    greeting for a later mount, and that stored copy must die with the claim
    behind it — past the deadline the same dream becomes offerable again, so
    replaying the stored greeting would disclose it twice. The client cannot
    compute the deadline (the TTL is server config), so the response carries
    it."""
    import asyncio

    from anima_server.config import settings
    from anima_server.services.agent.proactive import generate_greeting

    monkeypatch.setattr(settings, "agent_provider", "scaffold")
    user_id = _seed(soul_db)
    before = datetime.now(UTC)
    result = asyncio.run(generate_greeting(soul_db, user_id=user_id, runtime_db=None))

    assert result.ambient_dream_id is not None
    expires_at = result.ambient_dream_expires_at
    assert expires_at is not None
    ttl = timedelta(minutes=settings.dream_claim_ttl_minutes)
    # The stated deadline is exactly the claim's: claimed_at + TTL, and the
    # claim was taken during this call.
    row = soul_db.scalars(select(DreamJournal)).one()
    claimed_at = row.claimed_at
    if claimed_at.tzinfo is None:
        claimed_at = claimed_at.replace(tzinfo=UTC)
    assert expires_at == claimed_at + ttl
    assert before + ttl <= expires_at <= datetime.now(UTC) + ttl
    # The token names THIS claim generation, so the client can confirm and
    # acknowledge against the exact row state it was handed.
    from anima_server.services.agent.inner_life.dream_receipt import (
        claim_token,
        confirm_claim,
    )

    assert result.ambient_dream_claim_token == claim_token(claimed_at)
    assert (
        confirm_claim(
            soul_db,
            user_id=user_id,
            dream_id=result.ambient_dream_id,
            token=result.ambient_dream_claim_token,
        )
        is not None
    )


def test_expiry_is_the_same_instant_the_server_re_offers_the_dream(soul_db) -> None:
    """The deadline handed to the client must not be looser than the one the
    server enforces, or a stored greeting could still be voiced after another
    channel took the dream."""
    from anima_server.services.agent.inner_life.dream_receipt import (
        claim_expires_at,
        offerable_dream_query,
    )

    user_id = _seed(soul_db)
    claim = _resolve_ambient_dream(soul_db, user_id=user_id)
    assert claim is not None
    deadline = claim_expires_at(claim.claimed_at)

    # Up to and including the stated deadline the claim still suppresses
    # re-offering; only past it does the dream become offerable again. The
    # client therefore stops showing its stored copy no LATER than the server
    # starts re-offering — never the reverse, which is the direction that
    # would allow a double disclosure.
    assert soul_db.scalars(offerable_dream_query(user_id, now=deadline)).first() is None
    assert (
        soul_db.scalars(
            offerable_dream_query(user_id, now=deadline + timedelta(microseconds=1))
        ).first()
        is not None
    )


def test_no_dream_means_no_expiry(soul_db, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from anima_server.config import settings
    from anima_server.services.agent.proactive import generate_greeting

    monkeypatch.setattr(settings, "agent_provider", "scaffold")
    user_id = _seed(soul_db, dream_sharing="off")
    result = asyncio.run(generate_greeting(soul_db, user_id=user_id, runtime_db=None))
    assert result.ambient_dream_expires_at is None
    assert result.ambient_dream_claim_token is None


def test_released_claim_reports_no_expiry(
    soul_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Consent withdrawn mid-generation releases the claim unvoiced — there
    is nothing for the client to store, so there is no deadline either."""
    import asyncio

    from anima_server.config import settings
    from anima_server.services.agent.proactive import generate_greeting

    monkeypatch.setattr(settings, "agent_provider", "scaffold")
    user_id = _seed(soul_db)

    def optout_mid_generation(runtime_db, *, user_id):
        cfg = get_or_create_presence_config(soul_db, user_id)
        cfg.dream_sharing = "off"
        soul_db.commit()

    monkeypatch.setattr(
        proactive, "_reset_dream_residue_after_surfacing", optout_mid_generation
    )
    result = asyncio.run(generate_greeting(soul_db, user_id=user_id, runtime_db=None))

    assert result.ambient_dream_id is None
    assert result.ambient_dream_expires_at is None
    assert result.ambient_dream_claim_token is None
    assert "boat you restored" not in result.message


def test_confirm_renews_a_live_claim_and_hands_back_a_new_token(soul_db) -> None:
    """The client asks the row itself, not its own clock, whether the dream
    is still its to voice (PR #135 review, P1)."""
    from anima_server.services.agent.inner_life.dream_receipt import (
        claim_expires_at,
        claim_token,
        confirm_claim,
    )

    user_id = _seed(soul_db)
    claim = _resolve_ambient_dream(soul_db, user_id=user_id)
    assert claim is not None
    token = claim_token(claim.claimed_at)

    confirmed = confirm_claim(
        soul_db, user_id=user_id, dream_id=claim.dream_id, token=token
    )
    soul_db.commit()
    assert confirmed is not None
    # Renewed, NOT surfaced: a client that dies before painting loses nothing.
    row = soul_db.scalars(select(DreamJournal)).one()
    assert row.surfaced is False
    assert confirmed.token != token
    assert confirmed.expires_at > claim_expires_at(claim.claimed_at)
    # The old token is spent — a replay of the pre-confirm state cannot voice.
    assert (
        confirm_claim(soul_db, user_id=user_id, dream_id=claim.dream_id, token=token)
        is None
    )


def test_confirm_refuses_a_claim_the_server_already_re_offered(soul_db) -> None:
    """THE duplicate-disclosure guard. A stashed greeting whose claim lapsed
    while the user was away — and which a second greeting has since claimed —
    must not voice its copy, however the device clock reads."""
    from anima_server.config import settings
    from anima_server.services.agent.inner_life.dream_receipt import (
        claim_token,
        confirm_claim,
    )

    user_id = _seed(soul_db)
    first = _resolve_ambient_dream(soul_db, user_id=user_id)
    assert first is not None
    stale_token = claim_token(first.claimed_at)

    # The user never saw it; the claim lapses and a later greeting takes it.
    row = soul_db.scalars(select(DreamJournal)).one()
    row.claimed_at = datetime.now(UTC) - timedelta(
        minutes=settings.dream_claim_ttl_minutes + 1
    )
    soul_db.commit()
    second = _resolve_ambient_dream(soul_db, user_id=user_id)
    assert second is not None
    second_token = claim_token(second.claimed_at)
    assert second_token != stale_token

    # The first client returns and tries to voice its copy: refused.
    assert (
        confirm_claim(
            soul_db, user_id=user_id, dream_id=first.dream_id, token=stale_token
        )
        is None
    )
    # And the live claim is untouched — the second greeting still owns it.
    assert (
        confirm_claim(
            soul_db, user_id=user_id, dream_id=second.dream_id, token=second_token
        )
        is not None
    )


def test_a_stale_ack_cannot_surface_the_dream_or_clear_a_newer_claim(soul_db) -> None:
    """Regression (PR #135 review, P1): the ack used to check only
    user/id/surfaced, so a client whose claim had lapsed could mark the dream
    surfaced and wipe the claim of the greeting currently disclosing it."""
    from anima_server.config import settings
    from anima_server.services.agent.inner_life.dream_receipt import (
        acknowledge_dream,
        claim_token,
    )

    user_id = _seed(soul_db)
    first = _resolve_ambient_dream(soul_db, user_id=user_id)
    assert first is not None
    stale_token = claim_token(first.claimed_at)

    row = soul_db.scalars(select(DreamJournal)).one()
    row.claimed_at = datetime.now(UTC) - timedelta(
        minutes=settings.dream_claim_ttl_minutes + 1
    )
    soul_db.commit()
    second = _resolve_ambient_dream(soul_db, user_id=user_id)
    assert second is not None

    assert (
        acknowledge_dream(
            soul_db, user_id=user_id, dream_id=first.dream_id, token=stale_token
        )
        is False
    )
    soul_db.commit()
    row = soul_db.scalars(select(DreamJournal)).one()
    assert row.surfaced is False  # not consumed by the stale client
    assert row.claimed_at is not None  # the live claim survives
    # The current holder can still complete its own receipt.
    assert (
        acknowledge_dream(
            soul_db,
            user_id=user_id,
            dream_id=second.dream_id,
            token=claim_token(second.claimed_at),
        )
        is True
    )


def test_confirm_refuses_after_acknowledgement_and_on_garbage_tokens(soul_db) -> None:
    from anima_server.services.agent.inner_life.dream_receipt import (
        acknowledge_dream,
        claim_token,
        confirm_claim,
    )

    user_id = _seed(soul_db)
    claim = _resolve_ambient_dream(soul_db, user_id=user_id)
    assert claim is not None
    token = claim_token(claim.claimed_at)

    assert (
        confirm_claim(
            soul_db, user_id=user_id, dream_id=claim.dream_id, token="not-a-timestamp"
        )
        is None
    )
    assert (
        confirm_claim(
            soul_db, user_id=user_id + 99, dream_id=claim.dream_id, token=token
        )
        is None
    )
    acknowledge_dream(
        soul_db, user_id=user_id, dream_id=claim.dream_id, token=token
    )
    soul_db.commit()
    # Acknowledged dreams are gone for good — no confirmation revives them.
    assert (
        confirm_claim(soul_db, user_id=user_id, dream_id=claim.dream_id, token=token)
        is None
    )


def test_confirming_extends_the_window_the_dream_stays_unofferable(soul_db) -> None:
    """Renewal is what makes the confirm safe to do before rendering: the
    caller gets a fresh TTL, so a slow paint cannot race its own deadline."""
    from anima_server.services.agent.inner_life.dream_receipt import (
        claim_expires_at,
        claim_token,
        confirm_claim,
        offerable_dream_query,
    )

    user_id = _seed(soul_db)
    claim = _resolve_ambient_dream(soul_db, user_id=user_id)
    assert claim is not None
    original_deadline = claim_expires_at(claim.claimed_at)

    confirmed = confirm_claim(
        soul_db,
        user_id=user_id,
        dream_id=claim.dream_id,
        token=claim_token(claim.claimed_at),
        now=original_deadline - timedelta(seconds=1),
    )
    soul_db.commit()
    assert confirmed is not None
    # Past the ORIGINAL deadline the dream would have been offerable; the
    # renewal keeps it held.
    assert (
        soul_db.scalars(
            offerable_dream_query(user_id, now=original_deadline + timedelta(minutes=1))
        ).first()
        is None
    )
    assert (
        soul_db.scalars(
            offerable_dream_query(
                user_id, now=confirmed.expires_at + timedelta(microseconds=1)
            )
        ).first()
        is not None
    )


def test_confirm_refuses_after_consent_is_withdrawn(soul_db) -> None:
    """Regression (PR #135 review, P1): the claim was taken when the greeting
    was generated — for a stashed greeting, up to a whole TTL earlier. Owning
    the claim proves nothing about CONTINUING consent, so an opt-out in that
    window must stop the voicing exactly as it does during generation."""
    from anima_server.services.agent.inner_life.dream_receipt import (
        claim_token,
        confirm_claim,
    )

    user_id = _seed(soul_db)
    claim = _resolve_ambient_dream(soul_db, user_id=user_id)
    assert claim is not None
    token = claim_token(claim.claimed_at)

    cfg = get_or_create_presence_config(soul_db, user_id)
    cfg.dream_sharing = "off"
    soul_db.commit()

    assert (
        confirm_claim(soul_db, user_id=user_id, dream_id=claim.dream_id, token=token)
        is None
    )
    # Not surfaced and still claimed: the claim lapses on its own rather than
    # being cleared, since clearing unconditionally would also drop a NEWER
    # greeting's claim.
    row = soul_db.scalars(select(DreamJournal)).one()
    assert row.surfaced is False
    assert row.claimed_at is not None

    # Re-enabling ambient makes the very same claim voiceable again.
    cfg.dream_sharing = "ambient"
    soul_db.commit()
    assert (
        confirm_claim(soul_db, user_id=user_id, dream_id=claim.dream_id, token=token)
        is not None
    )


def test_confirm_refuses_when_the_presence_master_switch_is_off(soul_db) -> None:
    from anima_server.services.agent.inner_life.dream_receipt import (
        claim_token,
        confirm_claim,
    )

    user_id = _seed(soul_db)
    claim = _resolve_ambient_dream(soul_db, user_id=user_id)
    assert claim is not None

    cfg = get_or_create_presence_config(soul_db, user_id)
    cfg.enabled = False
    soul_db.commit()

    assert (
        confirm_claim(
            soul_db,
            user_id=user_id,
            dream_id=claim.dream_id,
            token=claim_token(claim.claimed_at),
        )
        is None
    )


def test_confirmation_is_serialized_with_consent_updates(soul_db) -> None:
    """The consent re-read and the renewal run under the same per-user lock
    the presence-config PUT holds through its commit, so an opt-out cannot
    interleave between them (the round-10 lesson from PR #130)."""
    import inspect

    from anima_server.services.agent.inner_life.dream_receipt import confirm_claim

    source = inspect.getsource(confirm_claim)
    assert "with presence_consent_lock(user_id):" in source
    lock_at = source.index("with presence_consent_lock(user_id):")
    assert source.index("get_presence_config_values(db, user_id)") > lock_at
    assert source.index("update(DreamJournal)") > lock_at


def test_confirm_refuses_an_expired_claim_generation(soul_db) -> None:
    """Regression (PR #135 review, P1): the confirm matched the claim
    generation but not its liveness. Between expiry and the next writer the
    row already satisfies `offerable_dream_query`, so an initiative may have
    selected the same narrative — renewing there would authorise a second,
    concurrent disclosure of it."""
    from anima_server.config import settings
    from anima_server.services.agent.inner_life.dream_receipt import (
        claim_token,
        confirm_claim,
        offerable_dream_query,
    )

    user_id = _seed(soul_db)
    claim = _resolve_ambient_dream(soul_db, user_id=user_id)
    assert claim is not None
    token = claim_token(claim.claimed_at)

    # The claim lapses; nothing has re-claimed it yet, so the token still
    # matches the stored value exactly.
    expired_at = datetime.now(UTC) - timedelta(
        minutes=settings.dream_claim_ttl_minutes + 1
    )
    row = soul_db.scalars(select(DreamJournal)).one()
    row.claimed_at = expired_at
    soul_db.commit()
    stale_token = claim_token(expired_at)
    assert soul_db.scalars(offerable_dream_query(user_id)).first() is not None

    assert (
        confirm_claim(
            soul_db, user_id=user_id, dream_id=claim.dream_id, token=stale_token
        )
        is None
    )
    # And the row is untouched, so whoever claims it next still can.
    row = soul_db.scalars(select(DreamJournal)).one()
    assert row.surfaced is False
    assert soul_db.scalars(offerable_dream_query(user_id)).first() is not None
    del token


def test_a_late_acknowledgement_is_still_honoured(soul_db) -> None:
    """The deliberate asymmetry (PR #135 review): confirmation authorises a
    FUTURE disclosure, so an expired generation must lose it. An
    acknowledgement reports one that already happened — the user has read
    this narrative — so refusing it moments after the deadline would leave
    the dream offerable and guarantee it is voiced at them a second time."""
    from anima_server.config import settings
    from anima_server.services.agent.inner_life.dream_receipt import (
        acknowledge_dream,
        claim_token,
    )

    user_id = _seed(soul_db)
    claim = _resolve_ambient_dream(soul_db, user_id=user_id)
    assert claim is not None

    expired_at = datetime.now(UTC) - timedelta(
        minutes=settings.dream_claim_ttl_minutes + 1
    )
    row = soul_db.scalars(select(DreamJournal)).one()
    row.claimed_at = expired_at
    soul_db.commit()

    assert (
        acknowledge_dream(
            soul_db,
            user_id=user_id,
            dream_id=claim.dream_id,
            token=claim_token(expired_at),
        )
        is True
    )
    soul_db.commit()
    assert soul_db.scalars(select(DreamJournal)).one().surfaced is True
