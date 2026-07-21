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
from anima_server.services.presence_config import get_or_create_presence_config
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

    async def fake_generate(soul_db, *, user_id, material, latent_topics, transcript_fragment, affect_line, client=None):
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
    se.dispose(); re.dispose()


def test_outside_night_window_does_not_dream() -> None:
    se, re = _soul_engine(), _runtime_engine()
    sf, rf = _factory(se), _factory(re)
    _seed_user(sf, rf)  # no thread -> maximally idle, but it is noon
    assert dream_edge.run_dream_for_user(sf, rf, user_id=1, local_now=DAY) is False
    with sf() as db_:
        assert db_.scalars(select(DreamJournal)).all() == []
    se.dispose(); re.dispose()


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
    se.dispose(); re.dispose()


def test_no_active_dek_skips_dream(monkeypatch: pytest.MonkeyPatch) -> None:
    se, re = _soul_engine(), _runtime_engine()
    sf, rf = _factory(se), _factory(re)
    _seed_user(sf, rf)
    monkeypatch.setattr(dream_edge, "get_active_dek", lambda user_id, domain=None: None)
    assert dream_edge.run_dream_for_user(sf, rf, user_id=1, local_now=NIGHT) is False
    with sf() as db_:
        assert db_.scalars(select(DreamJournal)).all() == []
    se.dispose(); re.dispose()


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
    se.dispose(); re.dispose()


def test_low_significance_material_is_not_share_worthy() -> None:
    se, re = _soul_engine(), _runtime_engine()
    sf, rf = _factory(se), _factory(re)
    _seed_user(sf, rf, importance=1)  # trivial material
    assert dream_edge.run_dream_for_user(sf, rf, user_id=1, local_now=NIGHT) is True
    with sf() as db_:
        d = db_.scalars(select(DreamJournal)).one()
        assert d.share_worthy is False
    se.dispose(); re.dispose()


def test_identity_only_memories_yield_no_dream() -> None:
    se, re = _soul_engine(), _runtime_engine()
    sf, rf = _factory(se), _factory(re)
    _seed_user(sf, rf, memory_class="identity")  # excluded from material
    assert dream_edge.run_dream_for_user(sf, rf, user_id=1, local_now=NIGHT) is False
    with sf() as db_:
        assert db_.scalars(select(DreamJournal)).all() == []
    se.dispose(); re.dispose()


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
    se.dispose(); re.dispose()


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
    se.dispose(); re.dispose()


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
    se.dispose(); re.dispose()


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
    se.dispose(); re.dispose()


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
            source_refs={"memory_item_ids": [1, 2]}, affect_delta={"valence": 0.03},
            share_worthy=True, surfaced=False,
        ))
        db_.commit()
        row = db_.scalars(select(DreamJournal)).one()
        rec = serialize_dream_journal_record(row, deks=None)
    assert rec["id"] == 7
    assert rec["narrative"] == "ENC(a dream)"  # deks=None -> value passthrough
    assert rec["source_refs"] == {"memory_item_ids": [1, 2]}
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
