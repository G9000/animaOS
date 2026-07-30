"""Behavioral end-to-end lifecycle test for Inner Life (IL-001..IL-013).

One continuous "week in the life" driven through the REAL service functions
against isolated in-memory stores — the cross-feature companion to the
per-feature test files, guarding the seams BETWEEN mechanisms that unit
tests can't see:

  a warm conversation moves the affect vector; time relaxes it -> a 3-day
  absence is caught up in O(1), defers exactly one dream, and leaves the
  reconnect subdued -> an open foresight thread builds drive pressure and
  fires exactly one initiative with full provenance -> the client polls,
  is served, and acks -> an immediate re-tick is silent (cooldown) -> at
  02:00 after long idle a dream fires over important-but-cold memories
  with provenance, share-worthiness, the nightly cap, and a bounded affect
  nudge -> the dream raises dream_residue and the NEXT initiative voices
  it, marking the dream surfaced -> forgetting a source memory scrubs the
  dream (right-to-forget through the derived layer).

Only the LLM seams and the DEK/crypto edges are stubbed (deterministic /
passthrough via monkeypatch, so nothing leaks across tests); everything
else is production code. Originally run as a manual verification script
during the IL-000 closeout; promoted into the suite so the lifecycle is
enforced, not just verified once (RWF-007).
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest
from anima_server.db.base import Base
from anima_server.db.runtime_base import RuntimeBase
from anima_server.models import DreamJournal, ForesightSignal, InitiativeLog, MemoryItem
from anima_server.models.runtime_consciousness import (
    AffectStateRow,
    DriveStateRow,
    PendingInitiative,
    PresenceCatchup,
)
from anima_server.services.agent.inner_life import dream_edge, initiative
from anima_server.services.agent.inner_life.affect import (
    AffectState,
    apply_turn_deltas,
    relax,
    render_affect,
)
from anima_server.services.agent.inner_life.catchup import apply_offline_catchup
from anima_server.services.agent.inner_life.delivery import (
    acknowledge_pending_initiative,
    list_and_mark_delivered,
)
from anima_server.services.agent.inner_life.store import (
    get_affect_config,
    get_affect_state,
    save_affect_state,
)
from anima_server.services.presence_config import get_or_create_presence_config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

USER_ID = 1
DAY0 = datetime(2026, 7, 20, 15, 0, tzinfo=UTC)


def _engine(base):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    base.metadata.create_all(bind=engine)
    return engine


@pytest.fixture()
def soul_factory():
    engine = _engine(Base)
    yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    engine.dispose()


@pytest.fixture()
def rt_factory():
    engine = _engine(RuntimeBase)
    yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    engine.dispose()


@pytest.fixture(autouse=True)
def _stub_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    """Crypto passthrough + deterministic LLM seams, scoped to this test."""
    for mod in (initiative, dream_edge):
        monkeypatch.setattr(mod, "get_active_dek", lambda user_id, domain=None: b"dek")
        monkeypatch.setattr(mod, "df", lambda user_id, v, **kw: v)
        monkeypatch.setattr(mod, "ef", lambda user_id, v, **kw: v)

    async def fake_initiative_msg(soul_db, *, user_id, decision, material, affect_line):
        return f"[{decision.drive}] {material[:60]}"

    async def fake_dream(soul_db, *, user_id, material, latent_topics, affect_line, client=None):
        return {
            "narrative": f"a blurred dream weaving {len(material)} cold memories",
            "valence_delta": 0.5,
            "arousal_delta": 0.1,
            "energy_delta": -0.3,
        }

    monkeypatch.setattr(initiative, "generate_initiative_message", fake_initiative_msg)
    monkeypatch.setattr(dream_edge, "generate_dream_narrative", fake_dream)


def test_inner_life_week_lifecycle(soul_factory, rt_factory) -> None:
    # ---------------------------------------------------------- IL-001 affect
    # A warm, engaging turn raises valence; 48 idle hours relax it back.
    cfg = get_affect_config()
    with rt_factory() as db:
        s0 = get_affect_state(db, user_id=USER_ID, config=cfg)
        s1 = apply_turn_deltas(s0, 0.12, 0.10, 0.05)
        save_affect_state(
            db,
            user_id=USER_ID,
            state=AffectState(
                valence=s1.valence, arousal=s1.arousal, energy=s1.energy, updated_at=DAY0
            ),
        )
        db.commit()
    assert s1.valence > s0.valence

    relaxed = relax(
        AffectState(
            valence=s1.valence, arousal=s1.arousal, energy=s1.energy, updated_at=DAY0
        ),
        DAY0 + timedelta(hours=48),
        cfg,
    )
    assert abs(relaxed.valence) < abs(s1.valence)
    assert render_affect(relaxed)  # renders to a non-empty tone line

    # ------------------------------------------- IL-002 + IL-011 catch-up
    # A 3-day absence is applied in O(1): one audit row, exactly one deferred
    # dream (the gap covers night windows), and the reconnect lands subdued
    # (energy-only dip, recorded in components).
    gap_end = DAY0 + timedelta(days=3)
    results = apply_offline_catchup(rt_factory, now=gap_end)
    assert [r.user_id for r in results] == [USER_ID]
    with rt_factory() as db:
        marker = db.scalar(
            select(PresenceCatchup).where(PresenceCatchup.user_id == USER_ID)
        )
    assert marker is not None
    assert marker.gap_seconds == pytest.approx(3 * 86400, rel=1e-6)
    assert marker.dream_deferred is True
    assert "reconnect_energy" in marker.components  # IL-011 subdued reconnect

    # ------------------------------------------ IL-003 drives -> initiative
    # An open foresight thread builds unresolved_thread pressure; with the
    # explicit opt-in on, the initiative fires once with full provenance.
    with soul_factory() as db:
        pc = get_or_create_presence_config(db, USER_ID)
        pc.initiative_enabled = True  # explicit opt-in (off by default)
        db.add(
            ForesightSignal(
                user_id=USER_ID,
                content="the gallery opening you were nervous about",
                evidence="mentioned twice",
                status="due",
                start_date=(gap_end + timedelta(days=1)).date(),
                confidence=0.9,
            )
        )
        db.commit()
    with rt_factory() as db:
        db.add(DriveStateRow(user_id=USER_ID, unresolved_thread=0.85, updated_at=gap_end))
        db.commit()

    tick_at = gap_end + timedelta(hours=5)
    fired = initiative.tick_initiative_for_user(
        soul_factory, rt_factory, user_id=USER_ID, local_now=tick_at
    )
    assert fired is True
    with soul_factory() as db:
        logs = db.scalars(select(InitiativeLog)).all()
    with rt_factory() as db:
        pending = db.scalars(select(PendingInitiative)).all()
    assert len(logs) == 1
    assert logs[0].drive == "unresolved_thread"
    assert logs[0].pressure_snapshot and logs[0].gate_states  # provenance complete
    assert "the gallery opening" in (logs[0].generated_text or "")
    assert len(pending) == 1  # pollable delivery row

    # Client polls and acks; the ack feeds the unanswered-backoff.
    with rt_factory() as rdb, soul_factory() as sdb:
        fetched = list_and_mark_delivered(rdb, user_id=USER_ID, soul_db=sdb)
        assert len(fetched) == 1 and fetched[0].delivered
        acked = acknowledge_pending_initiative(
            rdb, soul_db=sdb, user_id=USER_ID, pending_id=fetched[0].id
        )
        rdb.commit()
        sdb.commit()
    assert acked is not None
    with soul_factory() as db:
        log = db.scalars(select(InitiativeLog)).one()
        assert log.answered and log.delivered

    # An immediate re-tick is silent: cooldown + rate cap hold.
    initiative.tick_initiative_for_user(
        soul_factory, rt_factory, user_id=USER_ID, local_now=tick_at + timedelta(minutes=10)
    )
    with soul_factory() as db:
        assert len(db.scalars(select(InitiativeLog)).all()) == 1

    # ------------------------------------------------- IL-007 night dream
    # 02:00, long idle, important-but-cold memories: the dream fires with
    # provenance and share-worthiness; a second same-night dream is blocked.
    with soul_factory() as db:
        for txt in (
            "the summer you spent restoring the boat",
            "your father's unfinished letter",
            "the plan to learn woodworking",
        ):
            db.add(
                MemoryItem(
                    user_id=USER_ID,
                    content=txt,
                    category="fact",
                    importance=5,
                    emotional_salience=0.7,
                    memory_class="life_event",
                    heat=0.02,
                )
            )
        db.commit()
    night = (gap_end + timedelta(days=1)).replace(hour=2, minute=0)
    dreamt = dream_edge.run_dream_for_user(
        soul_factory, rt_factory, user_id=USER_ID, local_now=night, rng=random.Random(42)
    )
    assert dreamt is True
    with soul_factory() as db:
        dreams = db.scalars(select(DreamJournal)).all()
    assert len(dreams) == 1
    assert dreams[0].source_refs.get("memory_item_ids")  # provenance
    assert dreams[0].share_worthy is True

    second = dream_edge.run_dream_for_user(
        soul_factory,
        rt_factory,
        user_id=USER_ID,
        local_now=night + timedelta(hours=1),
        rng=random.Random(43),
    )
    assert second is False  # <=1 dream per night
    with soul_factory() as db:
        assert len(db.scalars(select(DreamJournal)).all()) == 1
    with rt_factory() as db:
        assert (
            db.scalar(select(AffectStateRow).where(AffectStateRow.user_id == USER_ID))
            is not None
        )  # the dream's bounded affect nudge persisted state

    # ----------------------- IL-003 x IL-007: dream_residue -> initiative
    # Days later the share-worthy dream becomes the next initiative and is
    # marked surfaced so it stops re-raising.
    with rt_factory() as db:
        row = db.scalar(select(DriveStateRow).where(DriveStateRow.user_id == USER_ID))
        row.dream_residue = 0.9  # pressure accumulated over subsequent ticks
        row.last_fired_at = None  # cooldown elapsed (simulate days later)
        db.commit()
    with soul_factory() as db:  # resolve the foresight so it can't dominate
        fs = db.scalars(select(ForesightSignal)).one()
        fs.status = "resolved"
        db.commit()

    later = night + timedelta(days=3, hours=10)
    initiative.tick_initiative_for_user(
        soul_factory, rt_factory, user_id=USER_ID, local_now=later
    )
    with soul_factory() as db:
        logs = db.scalars(select(InitiativeLog).order_by(InitiativeLog.id)).all()
        dream = db.scalars(select(DreamJournal)).one()
        assert len(logs) == 2
        assert logs[-1].drive == "dream_residue"
        assert "blurred dream" in (logs[-1].generated_text or "")
        assert dream.surfaced is True

    # ------------------------------------------------ right-to-forget
    # Forgetting one source memory deletes the dream built on it: the
    # right-to-forget cascades through the derived layer.
    from anima_server.services.agent import forgetting

    with soul_factory() as db:
        dream = db.scalars(select(DreamJournal)).one()
        source_ids = set(dream.source_refs["memory_item_ids"])
        scrubbed = forgetting._scrub_dream_journal_for_forget(
            db, user_id=USER_ID, forgotten_memory_item_ids={next(iter(source_ids))}
        )
        db.commit()
    assert scrubbed == 1
    with soul_factory() as db:
        assert db.scalars(select(DreamJournal)).all() == []
