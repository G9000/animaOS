"""IL7 dream-cycle edge/integration tests (services/agent/inner_life/dream_edge.py).

Covers run_dream_for_user end-to-end with the LLM + crypto stubbed: the three
eligibility gates, the active-DEK gate, the four effects (journal row + rolling
cap, 25% affect nudge, η=0.02 reconsolidation, share-worthy flag), identity
exclusion, the catch-up-marker path, the IL3 dream_residue integration, and
vault round-trip + eval-reset.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from anima_server.db.base import Base
from anima_server.db.runtime_base import RuntimeBase
from anima_server.models import DreamJournal, MemoryItem
from anima_server.models.agent_runtime import ReconsolidationLog
from anima_server.models.runtime import RuntimeThread
from anima_server.models.runtime_consciousness import AffectStateRow, PresenceCatchup
from anima_server.services.agent.inner_life import dream_edge
from anima_server.services.agent.inner_life.dream import DreamConfig
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

NIGHT = datetime(2026, 1, 2, 2, 0, tzinfo=UTC)  # 02:00 — inside the night window
DAY = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)  # noon — outside


def _soul_engine() -> Engine:
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=e)
    return e


def _runtime_engine() -> Engine:
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    RuntimeBase.metadata.create_all(bind=e)
    return e


def _factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture(autouse=True)
def _stub_crypto_and_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate the edge orchestration from real crypto/LLM: an active fake DEK,
    passthrough field crypto, and a canned dream narrative. Individual tests
    override any of these (e.g. DEK -> None) as needed."""
    monkeypatch.setattr(dream_edge, "get_active_dek", lambda user_id, domain=None: b"dek")
    monkeypatch.setattr(dream_edge, "df", lambda user_id, value, **kw: value)
    monkeypatch.setattr(dream_edge, "ef", lambda user_id, value, **kw: value)

    async def fake_generate(soul_db, *, user_id, material, latent_topics, affect_line, client=None):
        return {
            "narrative": "a hallway of half-finished letters",
            "valence_delta": 0.4,
            "arousal_delta": 0.2,
            "energy_delta": -0.2,
        }

    monkeypatch.setattr(dream_edge, "generate_dream_narrative", fake_generate)


def _seed_user(
    soul_factory: sessionmaker[Session],
    runtime_factory: sessionmaker[Session],
    *,
    user_id: int = 1,
    n_items: int = 3,
    importance: int = 5,
    memory_class: str = "casual",
    seed_affect: bool = True,
) -> None:
    with soul_factory() as db_:
        for i in range(n_items):
            db_.add(
                MemoryItem(
                    user_id=user_id,
                    content=f"a cold but important memory {i}",
                    category="fact",
                    importance=importance,
                    emotional_salience=0.5,
                    memory_class=memory_class,
                    heat=0.02,
                )
            )
        db_.commit()
    if seed_affect:
        with runtime_factory() as db_:
            db_.add(
                AffectStateRow(
                    user_id=user_id, valence=0.1, arousal=0.4, energy=0.5,
                    updated_at=NIGHT - timedelta(hours=5),
                )
            )
            db_.commit()


# --------------------------------------------------------------------------
# Eligibility gates
# --------------------------------------------------------------------------


def test_short_idle_does_not_dream() -> None:
    se, re = _soul_engine(), _runtime_engine()
    sf, rf = _factory(se), _factory(re)
    _seed_user(sf, rf)
    with rf() as db_:  # a very recent thread -> only ~1h idle
        db_.add(RuntimeThread(user_id=1, status="idle", last_message_at=NIGHT - timedelta(hours=1)))
        db_.commit()

    assert dream_edge.run_dream_for_user(sf, rf, user_id=1, local_now=NIGHT) is False
    with sf() as db_:
        assert db_.scalars(select(DreamJournal)).all() == []
    se.dispose()
    re.dispose()


def test_outside_night_window_does_not_dream() -> None:
    se, re = _soul_engine(), _runtime_engine()
    sf, rf = _factory(se), _factory(re)
    _seed_user(sf, rf)  # no thread -> maximally idle, but it is noon
    assert dream_edge.run_dream_for_user(sf, rf, user_id=1, local_now=DAY) is False
    with sf() as db_:
        assert db_.scalars(select(DreamJournal)).all() == []
    se.dispose()
    re.dispose()


def test_per_night_cap_blocks_second_dream() -> None:
    se, re = _soul_engine(), _runtime_engine()
    sf, rf = _factory(se), _factory(re)
    _seed_user(sf, rf)
    with sf() as db_:  # a dream already recorded this night
        db_.add(DreamJournal(
            user_id=1, dreamt_at=NIGHT - timedelta(minutes=30),
            narrative="earlier", source_refs={}, affect_delta={}, share_worthy=False,
        ))
        db_.commit()
    assert dream_edge.run_dream_for_user(sf, rf, user_id=1, local_now=NIGHT) is False
    with sf() as db_:
        assert len(db_.scalars(select(DreamJournal)).all()) == 1  # no new dream
    se.dispose()
    re.dispose()


def test_no_active_dek_skips_dream(monkeypatch: pytest.MonkeyPatch) -> None:
    se, re = _soul_engine(), _runtime_engine()
    sf, rf = _factory(se), _factory(re)
    _seed_user(sf, rf)
    monkeypatch.setattr(dream_edge, "get_active_dek", lambda user_id, domain=None: None)
    assert dream_edge.run_dream_for_user(sf, rf, user_id=1, local_now=NIGHT) is False
    with sf() as db_:
        assert db_.scalars(select(DreamJournal)).all() == []
    se.dispose()
    re.dispose()


# --------------------------------------------------------------------------
# Effects on a successful dream
# --------------------------------------------------------------------------


def test_eligible_dream_writes_journal_and_all_effects() -> None:
    se, re = _soul_engine(), _runtime_engine()
    sf, rf = _factory(se), _factory(re)
    _seed_user(sf, rf, importance=5)

    assert dream_edge.run_dream_for_user(sf, rf, user_id=1, local_now=NIGHT) is True

    with sf() as db_:
        dreams = db_.scalars(select(DreamJournal)).all()
        assert len(dreams) == 1
        d = dreams[0]
        assert d.narrative == "a hallway of half-finished letters"  # ef passthrough
        assert d.share_worthy is True  # importance 5 -> significant material
        assert d.surfaced is False
        assert d.source_refs["memory_item_ids"]  # provenance recorded
        # affect_delta scaled to 25% (raw 0.4 -> 0.1) and clamped to 0.0375 cap
        assert d.affect_delta["valence"] == pytest.approx(0.0375)
        # Effect 3: reconsolidation touched the sampled memories.
        assert db_.scalars(select(ReconsolidationLog)).all()
    with rf() as db_:
        affect = db_.scalars(select(AffectStateRow).where(AffectStateRow.user_id == 1)).one()
        assert affect.valence > 0.1  # nudged upward by the warm dream
    se.dispose()
    re.dispose()


def test_latent_topic_keys_are_encrypted_in_source_refs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression (PR review, P1): derive_topic_key embeds a slug of the user's
    value/content, so latent_topic_keys must be field-encrypted in source_refs,
    not stored raw in this otherwise-numeric JSON."""
    from anima_server.models.agent_runtime import LatentTrace

    se, re = _soul_engine(), _runtime_engine()
    sf, rf = _factory(se), _factory(re)
    _seed_user(sf, rf)
    with sf() as db_:  # a latent trace above the weight threshold to be included
        db_.add(LatentTrace(
            user_id=1, topic_key="user:profile:employer:acme-corp", kind="minor_observation", weight=0.9,
        ))
        db_.commit()

    # A spy ef that marks what it encrypted (overrides the autouse passthrough).
    monkeypatch.setattr(dream_edge, "ef", lambda user_id, value, **kw: f"ENC:{value}")

    assert dream_edge.run_dream_for_user(sf, rf, user_id=1, local_now=NIGHT) is True
    with sf() as db_:
        d = db_.scalars(select(DreamJournal)).one()
        keys = d.source_refs["latent_topic_keys"]
        assert keys == ["ENC:user:profile:employer:acme-corp"]  # encrypted, not raw
        # The raw content slug never appears unencrypted in the JSON provenance.
        assert "acme-corp" not in str({k: v for k, v in d.source_refs.items()
                                       if k != "latent_topic_keys"})
    se.dispose()
    re.dispose()


def test_low_significance_material_is_not_share_worthy() -> None:
    se, re = _soul_engine(), _runtime_engine()
    sf, rf = _factory(se), _factory(re)
    _seed_user(sf, rf, importance=1)  # trivial material
    assert dream_edge.run_dream_for_user(sf, rf, user_id=1, local_now=NIGHT) is True
    with sf() as db_:
        d = db_.scalars(select(DreamJournal)).one()
        assert d.share_worthy is False
    se.dispose()
    re.dispose()


def test_identity_only_memories_yield_no_dream() -> None:
    se, re = _soul_engine(), _runtime_engine()
    sf, rf = _factory(se), _factory(re)
    _seed_user(sf, rf, memory_class="identity")  # excluded from material
    assert dream_edge.run_dream_for_user(sf, rf, user_id=1, local_now=NIGHT) is False
    with sf() as db_:
        assert db_.scalars(select(DreamJournal)).all() == []
    se.dispose()
    re.dispose()


def test_rolling_cap_prunes_oldest() -> None:
    se, re = _soul_engine(), _runtime_engine()
    sf, rf = _factory(se), _factory(re)
    _seed_user(sf, rf)
    cfg = DreamConfig(journal_cap=3, max_dreams_per_night=99)  # small cap, allow many
    with sf() as db_:
        for i in range(3):  # 3 existing dreams, oldest first
            db_.add(DreamJournal(
                user_id=1, dreamt_at=NIGHT - timedelta(days=3 - i),
                narrative=f"old {i}", source_refs={}, affect_delta={}, share_worthy=False,
            ))
        db_.commit()
    assert dream_edge.run_dream_for_user(sf, rf, user_id=1, local_now=NIGHT, config=cfg) is True
    with sf() as db_:
        rows = db_.scalars(select(DreamJournal).order_by(DreamJournal.dreamt_at)).all()
        assert len(rows) == 3  # capped
        assert rows[-1].narrative == "a hallway of half-finished letters"  # newest kept
        assert "old 0" not in [r.narrative for r in rows]  # oldest pruned
    se.dispose()
    re.dispose()


# --------------------------------------------------------------------------
# Catch-up marker path
# --------------------------------------------------------------------------


def test_catchup_marker_fires_outside_night_and_is_cleared() -> None:
    se, re = _soul_engine(), _runtime_engine()
    sf, rf = _factory(se), _factory(re)
    _seed_user(sf, rf)  # no thread -> maximally idle
    with rf() as db_:
        db_.add(PresenceCatchup(user_id=1, gap_seconds=90000, components="", dream_deferred=True))
        db_.commit()

    # Noon (outside the night window) — normally ineligible, but the deferred
    # catch-up marker permits one wake-up dream.
    assert dream_edge.run_dream_for_user(sf, rf, user_id=1, local_now=DAY) is True
    with sf() as db_:
        assert len(db_.scalars(select(DreamJournal)).all()) == 1
    with rf() as db_:
        row = db_.scalars(select(PresenceCatchup).where(PresenceCatchup.user_id == 1)).one()
        assert row.dream_deferred is False  # marker consumed
    se.dispose()
    re.dispose()


def test_catchup_marker_persists_when_transiently_ineligible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: the catch-up marker must NOT be consumed when a dream can't
    actually run (here the session is locked — no DEK). Otherwise the deferred
    wake-up dream would be silently lost instead of retrying next idle window."""
    se, re = _soul_engine(), _runtime_engine()
    sf, rf = _factory(se), _factory(re)
    _seed_user(sf, rf)
    monkeypatch.setattr(dream_edge, "get_active_dek", lambda user_id, domain=None: None)
    with rf() as db_:
        db_.add(PresenceCatchup(user_id=1, gap_seconds=90000, components="", dream_deferred=True))
        db_.commit()

    assert dream_edge.run_dream_for_user(sf, rf, user_id=1, local_now=DAY) is False
    with sf() as db_:
        assert db_.scalars(select(DreamJournal)).all() == []
    with rf() as db_:
        row = db_.scalars(select(PresenceCatchup).where(PresenceCatchup.user_id == 1)).one()
        assert row.dream_deferred is True  # marker PRESERVED for a later retry
    se.dispose()
    re.dispose()


# --------------------------------------------------------------------------
# IL3 dream_residue integration
# --------------------------------------------------------------------------


def test_share_worthy_dream_raises_dream_residue_signal() -> None:
    from anima_server.services.agent.inner_life import initiative
    from anima_server.services.agent.inner_life.drives import DRIVE_DREAM_RESIDUE

    se, re = _soul_engine(), _runtime_engine()
    sf, rf = _factory(se), _factory(re)
    with sf() as db_:
        db_.add(DreamJournal(
            user_id=1, dreamt_at=NIGHT, narrative="a shared-worthy dream",
            source_refs={}, affect_delta={}, share_worthy=True, surfaced=False,
        ))
        db_.commit()

    with sf() as soul_db, rf() as runtime_db:
        signals, _ = initiative.resolve_drive_signals(
            soul_db, runtime_db, user_id=1, now=NIGHT,
            last_user_turn_at=None, pattern_marker=None, pattern_marker_id=None,
        )
        assert signals.dream_residue_present is True
        material = initiative.gather_drive_material(
            soul_db, user_id=1, drive=DRIVE_DREAM_RESIDUE, now=NIGHT,
        )
        assert "dream" in material  # the narrative (df passthrough at import time)

    # A surfaced dream no longer raises the drive.
    with sf() as db_:
        db_.scalars(select(DreamJournal)).one().surfaced = True
        db_.commit()
    with sf() as soul_db, rf() as runtime_db:
        signals, _ = initiative.resolve_drive_signals(
            soul_db, runtime_db, user_id=1, now=NIGHT,
            last_user_turn_at=None, pattern_marker=None, pattern_marker_id=None,
        )
        assert signals.dream_residue_present is False
    se.dispose()
    re.dispose()


def test_dream_sharing_off_suppresses_dream_residue() -> None:
    """Regression (PR review, P2): dreamSharing='off' means the user never wants
    dreams surfaced, so a share-worthy unsurfaced dream must NOT raise the
    dream_residue grow signal, and its material lookup must return empty (so any
    pressure that accumulated while it was on is reset by the material-less-drive
    guard instead of firing an unsolicited dream initiative)."""
    from anima_server.services.agent.inner_life import initiative
    from anima_server.services.agent.inner_life.drives import DRIVE_DREAM_RESIDUE

    se, re = _soul_engine(), _runtime_engine()
    sf, rf = _factory(se), _factory(re)
    with sf() as db_:
        db_.add(DreamJournal(
            user_id=1, dreamt_at=NIGHT, narrative="a shareworthy dream",
            source_refs={}, affect_delta={}, share_worthy=True, surfaced=False,
        ))
        db_.commit()

    with sf() as soul_db, rf() as runtime_db:
        signals, _ = initiative.resolve_drive_signals(
            soul_db, runtime_db, user_id=1, now=NIGHT,
            last_user_turn_at=None, pattern_marker=None, pattern_marker_id=None,
            dream_sharing="off",
        )
        assert signals.dream_residue_present is False  # opted out -> no grow
        material = initiative.gather_drive_material(
            soul_db, user_id=1, drive=DRIVE_DREAM_RESIDUE, now=NIGHT,
            dream_sharing="off",
        )
        assert material == ""  # opted out -> no material -> material-less reset
        # Sanity: with sharing on, the same dream DOES raise the signal.
        signals_on, _ = initiative.resolve_drive_signals(
            soul_db, runtime_db, user_id=1, now=NIGHT,
            last_user_turn_at=None, pattern_marker=None, pattern_marker_id=None,
            dream_sharing="on_ask",
        )
        assert signals_on.dream_residue_present is True
    se.dispose()
    re.dispose()


def test_forgetting_scrubs_dreams_built_on_the_forgotten_memory() -> None:
    """Regression (PR review, P1): a dream's narrative is derived from decrypted
    memory content and the row is vault-exported, so forgetting a source memory
    must delete any dream seeded from it. Dreams that don't reference the
    forgotten id are untouched."""
    from anima_server.services.agent.forgetting import _scrub_dream_journal_for_forget

    se = _soul_engine()
    sf = _factory(se)
    with sf() as db_:
        db_.add(DreamJournal(
            user_id=1, dreamt_at=NIGHT, narrative="dream about item 5",
            source_refs={"memory_item_ids": [5, 9]}, affect_delta={}, share_worthy=True,
        ))
        db_.add(DreamJournal(
            user_id=1, dreamt_at=NIGHT, narrative="unrelated dream",
            source_refs={"memory_item_ids": [42]}, affect_delta={}, share_worthy=False,
        ))
        db_.commit()

    with sf() as db_:
        scrubbed = _scrub_dream_journal_for_forget(
            db_, user_id=1, forgotten_memory_item_ids={5}
        )
        db_.commit()
        assert scrubbed == 1

    with sf() as db_:
        narratives = [r.narrative for r in db_.scalars(select(DreamJournal)).all()]
        assert narratives == ["unrelated dream"]  # the item-5 dream is gone
    se.dispose()


def test_scrub_dreams_by_forgotten_latent_topic_keys() -> None:
    """Regression (PR review, P1): dreams are also seeded from latent-trace
    topics, so purging a latent topic must delete dreams referencing it — the
    narrative is derived from those topics and the row is vault-exported."""
    from anima_server.services.agent.forgetting import _scrub_dream_journal_for_forget

    se = _soul_engine()
    sf = _factory(se)
    with sf() as db_:
        db_.add(DreamJournal(
            user_id=1, dreamt_at=NIGHT, narrative="dream on a recurring theme",
            source_refs={"memory_item_ids": [], "latent_topic_keys": ["topic:commute-stress"]},
            affect_delta={}, share_worthy=True,
        ))
        db_.add(DreamJournal(
            user_id=1, dreamt_at=NIGHT, narrative="other-topic dream",
            source_refs={"memory_item_ids": [], "latent_topic_keys": ["topic:gardening"]},
            affect_delta={}, share_worthy=False,
        ))
        db_.commit()

    with sf() as db_:
        scrubbed = _scrub_dream_journal_for_forget(
            db_, user_id=1, forgotten_topic_keys={"topic:commute-stress"}
        )
        db_.commit()
        assert scrubbed == 1
    with sf() as db_:
        assert [r.narrative for r in db_.scalars(select(DreamJournal)).all()] == ["other-topic dream"]
    se.dispose()


def test_topic_purge_scrubs_dreams_built_on_that_topic() -> None:
    """Regression (PR review, P1): the topic-purge path
    (purge_latent_traces_matching_topic) must scrub dreams built on the purged
    topic, not just the LatentTrace rows."""
    from anima_server.models.agent_runtime import LatentTrace
    from anima_server.services.agent.forgetting import purge_latent_traces_matching_topic

    se = _soul_engine()
    sf = _factory(se)
    with sf() as db_:
        db_.add(LatentTrace(user_id=1, topic_key="commute_stress", kind="minor_observation", weight=0.6))
        db_.add(DreamJournal(
            user_id=1, dreamt_at=NIGHT, narrative="a dream about the commute",
            source_refs={"memory_item_ids": [], "latent_topic_keys": ["commute_stress"]},
            affect_delta={}, share_worthy=True,
        ))
        db_.commit()

    with sf() as db_:
        purged = purge_latent_traces_matching_topic(db_, user_id=1, topic="commute stress")
        db_.commit()
        assert purged == 1
    with sf() as db_:
        assert db_.scalars(select(DreamJournal)).all() == []  # dream scrubbed too
        assert db_.scalars(select(LatentTrace)).all() == []
    se.dispose()


def test_topic_purge_scrubs_dream_whose_trace_already_gone() -> None:
    """Regression (PR review, P1): a dream can outlive its source LatentTrace
    (pruned/capped/deleted). The topic purge must still scrub such a dream by
    token-matching the dream's own latent_topic_keys, not only live traces."""
    from anima_server.services.agent.forgetting import purge_latent_traces_matching_topic

    se = _soul_engine()
    sf = _factory(se)
    with sf() as db_:  # dream references the topic, but NO LatentTrace row exists
        db_.add(DreamJournal(
            user_id=1, dreamt_at=NIGHT, narrative="a dream about the commute",
            source_refs={"memory_item_ids": [], "latent_topic_keys": ["commute_stress"]},
            affect_delta={}, share_worthy=True,
        ))
        db_.commit()

    with sf() as db_:
        purge_latent_traces_matching_topic(db_, user_id=1, topic="commute stress")
        db_.commit()
    with sf() as db_:
        assert db_.scalars(select(DreamJournal)).all() == []  # scrubbed despite no trace
    se.dispose()


def test_failed_generation_marks_attempt_and_blocks_retry_same_night(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression (PR review, P2): a failed/empty extraction call writes no
    dream_journal row, so without an attempt marker the 60s tick would re-call
    the model all night. The attempt is recorded (last_dream_attempt_at) so a
    second eligible tick the same night does NOT call the model again."""
    from anima_server.models.runtime_consciousness import DriveStateRow

    se, re = _soul_engine(), _runtime_engine()
    sf, rf = _factory(se), _factory(re)
    _seed_user(sf, rf)

    calls = {"n": 0}

    async def failing_generate(soul_db, *, user_id, material, latent_topics, affect_line, client=None):
        calls["n"] += 1
        return None  # model outage / empty output

    monkeypatch.setattr(dream_edge, "generate_dream_narrative", failing_generate)

    assert dream_edge.run_dream_for_user(sf, rf, user_id=1, local_now=NIGHT) is False
    assert calls["n"] == 1  # attempted once
    with rf() as db_:
        row = db_.scalars(select(DriveStateRow).where(DriveStateRow.user_id == 1)).one()
        assert row.last_dream_attempt_at is not None  # attempt recorded
    with sf() as db_:
        assert db_.scalars(select(DreamJournal)).all() == []  # no row written

    # A second eligible tick the same night must NOT re-call the model.
    assert dream_edge.run_dream_for_user(
        sf, rf, user_id=1, local_now=NIGHT + timedelta(minutes=1)
    ) is False
    assert calls["n"] == 1  # still 1 — attempt marker blocked the retry
    se.dispose()
    re.dispose()


@pytest.mark.parametrize(
    "value,expected",
    [
        (0.4, 0.4), (1, 1.0), ("1.5", 1.5), ("warm", 0.0), (None, 0.0), ({}, 0.0),
        # Non-finite must be rejected: float('nan') 'succeeds' but NaN survives
        # the clamp as the positive cap (a max positive nudge), so -> 0.0.
        ("nan", 0.0), ("inf", 0.0), ("-inf", 0.0), (float("nan"), 0.0), (float("inf"), 0.0),
    ],
)
def test_coerce_delta(value: object, expected: float) -> None:
    assert dream_edge._coerce_delta(value) == expected


def test_reconsolidation_uses_configured_dream_eta(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression (PR review, P2): the dream reconsolidation pass must use the
    configured dream_eta (operator-tunable via ANIMA_RECONSOLIDATION_DREAM_ETA),
    not a hard-coded 0.02 — so tuning/disabling it actually takes effect."""
    from anima_server.services.agent.inner_life.dream import DreamConfig

    se, re = _soul_engine(), _runtime_engine()
    sf, rf = _factory(se), _factory(re)
    _seed_user(sf, rf)

    seen: list[float] = []

    def spy_reconsolidate(soul_db, item, *, current_affect_magnitude, eta, **kw):
        seen.append(eta)
        return None

    monkeypatch.setattr(dream_edge, "apply_reconsolidation", spy_reconsolidate)

    assert dream_edge.run_dream_for_user(
        sf, rf, user_id=1, local_now=NIGHT, config=DreamConfig(dream_eta=0.005)
    ) is True
    assert seen and all(e == 0.005 for e in seen)  # the configured eta, not 0.02
    se.dispose()
    re.dispose()


def test_zero_dream_eta_fully_skips_reconsolidation() -> None:
    """Regression (PR review, P2): eta=0 must FULLY disable dream
    reconsolidation. apply_reconsolidation no-ops the affect nudge at eta 0 but
    still upgrades stability classes and writes ReconsolidationLog rows, so the
    call is skipped entirely — no ReconsolidationLog is written."""
    from anima_server.services.agent.inner_life.dream import DreamConfig

    se, re = _soul_engine(), _runtime_engine()
    sf, rf = _factory(se), _factory(re)
    _seed_user(sf, rf)

    assert dream_edge.run_dream_for_user(
        sf, rf, user_id=1, local_now=NIGHT, config=DreamConfig(dream_eta=0.0)
    ) is True
    with sf() as db_:
        assert db_.scalars(select(DreamJournal)).all()  # dream still recorded
        assert db_.scalars(select(ReconsolidationLog)).all() == []  # but no reconsolidation
    se.dispose()
    re.dispose()


def test_nonnumeric_delta_does_not_raise_and_yields_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression (PR review, P2): parseable JSON with a non-numeric delta
    ("valence_delta": "warm") must not raise out of generate_dream_narrative
    (that would escape run_dream_for_user's outer handler and roll back the
    attempt marker). It is coerced to a 0 nudge and the valid narrative kept."""
    import asyncio

    # Drop the autouse stubs (esp. the generate_dream_narrative stub) so we
    # exercise the REAL generate_dream_narrative + its delta coercion.
    monkeypatch.undo()

    async def fake_json(system, prompt, *, expect="object", client=None):
        return {"narrative": "a warm blurred hallway", "valence_delta": "warm", "arousal_delta": None}

    monkeypatch.setattr("anima_server.services.agent.llm_json.call_llm_for_json", fake_json)

    se = _soul_engine()
    sf = _factory(se)
    with sf() as soul_db:
        out = asyncio.run(
            dream_edge.generate_dream_narrative(
                soul_db, user_id=1, material=["m"], latent_topics=[],
                affect_line="steady", client=object(),
            )
        )
    assert out is not None
    assert out["narrative"] == "a warm blurred hallway"
    assert out["valence_delta"] == 0.0  # "warm" -> 0.0, no raise
    assert out["arousal_delta"] == 0.0  # None -> 0.0
    se.dispose()


# --------------------------------------------------------------------------
# Vault round-trip + eval-reset
# --------------------------------------------------------------------------


def test_dream_journal_serialize_roundtrip() -> None:
    from anima_server.services.vault import serialize_dream_journal_record

    se = _soul_engine()
    sf = _factory(se)
    with sf() as db_:
        db_.add(DreamJournal(
            id=7, user_id=1, dreamt_at=NIGHT, narrative="ENC(a dream)",
            source_refs={"memory_item_ids": [1, 2], "latent_topic_keys": ["ENC(topic:x)"]},
            affect_delta={"valence": 0.03}, share_worthy=True, surfaced=False,
        ))
        db_.commit()
        row = db_.scalars(select(DreamJournal)).one()
        rec = serialize_dream_journal_record(row, deks=None)
    assert rec["id"] == 7
    assert rec["narrative"] == "ENC(a dream)"  # deks=None -> value passthrough
    # source_refs is decrypted for export (deks=None -> passthrough here); the
    # encrypted latent_topic_keys are carried through the decrypt path, not
    # exported as opaque ciphertext that a re-import couldn't re-key.
    assert rec["source_refs"]["memory_item_ids"] == [1, 2]
    assert rec["source_refs"]["latent_topic_keys"] == ["ENC(topic:x)"]
    assert rec["share_worthy"] is True
    se.dispose()


def test_eval_reset_clears_dream_journal() -> None:
    from anima_server.services.eval_reset import _reset_soul_state

    se = _soul_engine()
    sf = _factory(se)
    with sf() as db_:
        db_.add(DreamJournal(
            user_id=1, dreamt_at=NIGHT, narrative="x",
            source_refs={}, affect_delta={}, share_worthy=False,
        ))
        db_.commit()
    with sf() as db_:
        _reset_soul_state(db_, user_id=1, deleted={})
        db_.commit()
    with sf() as db_:
        assert db_.scalars(select(DreamJournal)).all() == []
    se.dispose()
