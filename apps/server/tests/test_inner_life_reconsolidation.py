"""Tests for IL6 — Recall Reconsolidation (F2 extension).

Covers: the pure nudge math (toward-affect, clamping, stability
upgrade-only, identity exemption, no-affect-signal skip), fires-only-on-
context-inclusion wiring through ``sync_retrieval_feedback``, the exact
lifetime drift cap, exact reversibility from the provenance log, the
superseded/distilled-tombstone "active item" skip guard, the
reduced-strength (IL7 dream) eta path, vault export/import round-tripping,
eval-reset clearing, and the zero-LLM / no-N+1 assertions.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import pytest
from anima_server.db.base import Base
from anima_server.db.runtime_base import RuntimeBase
from anima_server.models import MemoryItem, ReconsolidationLog, User
from anima_server.models.runtime_consciousness import AffectStateRow
from anima_server.models.runtime_memory import MemoryRetrievalFeedback
from anima_server.services.agent import reconsolidation
from anima_server.services.agent.reconsolidation import (
    ReconsolidationState,
    apply_reconsolidation,
    original_salience_from_log,
    reconsolidate_salience,
    resolve_current_affect_magnitude,
)
from anima_server.services.agent.retrieval_feedback import sync_retrieval_feedback
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
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _make_item(
    db: Session,
    *,
    user_id: int = 1,
    content: str = "had a great hike this weekend",
    category: str = "fact",
    memory_class: str = "casual",
    emotional_salience: float = 0.2,
    stability_class: str = "temporary",
    superseded_by: int | None = None,
    distilled_at: datetime | None = None,
    reconsolidation_drift: float = 0.0,
) -> MemoryItem:
    item = MemoryItem(
        user_id=user_id,
        content=content,
        category=category,
        importance=3,
        source="extraction",
        memory_class=memory_class,
        emotional_salience=emotional_salience,
        stability_class=stability_class,
        superseded_by=superseded_by,
        distilled_at=distilled_at,
        reconsolidation_drift=reconsolidation_drift,
    )
    db.add(item)
    db.flush()
    return item


def _create_soul_engine() -> Engine:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return engine


def _create_runtime_engine() -> Engine:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    RuntimeBase.metadata.create_all(bind=engine)
    return engine


def _make_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


# ---------------------------------------------------------------------------
# 1. Pure math
# ---------------------------------------------------------------------------


def test_nudge_moves_emotional_salience_toward_affect_by_eta() -> None:
    state = ReconsolidationState(emotional_salience=0.2, stability_class="stable")
    result = reconsolidate_salience(state, 0.9, 0.05, 0.0, is_identity=False)

    expected_delta = 0.05 * (0.9 - 0.2)
    assert result.emotional_salience_delta == pytest.approx(expected_delta)
    assert result.emotional_salience == pytest.approx(0.2 + expected_delta)


def test_nudge_moves_down_when_affect_is_below_current_salience() -> None:
    state = ReconsolidationState(emotional_salience=0.8, stability_class="stable")
    result = reconsolidate_salience(state, 0.1, 0.05, 0.0, is_identity=False)

    expected_delta = 0.05 * (0.1 - 0.8)
    assert result.emotional_salience_delta == pytest.approx(expected_delta)
    assert result.emotional_salience < 0.8


def test_nudge_clamps_to_unit_interval_even_with_out_of_range_input() -> None:
    state = ReconsolidationState(emotional_salience=0.5, stability_class="stable")
    high = reconsolidate_salience(state, 5.0, 1.0, 0.0, is_identity=False, drift_cap=10.0)
    assert high.emotional_salience == 1.0

    low = reconsolidate_salience(state, -5.0, 1.0, 0.0, is_identity=False, drift_cap=10.0)
    assert low.emotional_salience == 0.0


def test_stability_upgrades_one_rung_and_never_downgrades() -> None:
    temporary = ReconsolidationState(emotional_salience=0.2, stability_class="temporary")
    result = reconsolidate_salience(temporary, 0.5, 0.05, 0.0, is_identity=False)
    assert result.stability_class == "evolving"
    assert result.stability_upgraded is True

    evolving = ReconsolidationState(emotional_salience=0.2, stability_class="evolving")
    result = reconsolidate_salience(evolving, 0.5, 0.05, 0.0, is_identity=False)
    assert result.stability_class == "stable"
    assert result.stability_upgraded is True

    stable = ReconsolidationState(emotional_salience=0.2, stability_class="stable")
    result = reconsolidate_salience(stable, 0.5, 0.05, 0.0, is_identity=False)
    assert result.stability_class == "stable"
    assert result.stability_upgraded is False


def test_identity_items_get_recency_refresh_only() -> None:
    """Identity exemption (PRD IL6): no affect nudge, no drift, and NO
    stability change either — even for an identity item that arrives with a
    non-stable stability_class (possible via LLM-set salience)."""
    state = ReconsolidationState(emotional_salience=0.3, stability_class="temporary")
    result = reconsolidate_salience(state, 0.9, 0.05, 0.0, is_identity=True)

    assert result.emotional_salience == 0.3
    assert result.emotional_salience_delta == 0.0
    assert result.lifetime_drift_total == 0.0
    # Stability is NOT upgraded for identity items — refresh only.
    assert result.stability_class == "temporary"
    assert result.stability_upgraded is False


def test_no_affect_signal_skips_emotional_nudge_but_stability_still_applies() -> None:
    state = ReconsolidationState(emotional_salience=0.4, stability_class="temporary")
    result = reconsolidate_salience(state, None, 0.05, 0.0, is_identity=False)

    assert result.emotional_salience == 0.4
    assert result.emotional_salience_delta == 0.0
    assert result.lifetime_drift_total == 0.0
    assert result.stability_class == "evolving"
    assert result.stability_upgraded is True


# ---------------------------------------------------------------------------
# 2. Drift cap (exact)
# ---------------------------------------------------------------------------


def test_drift_cap_accumulates_exactly_then_becomes_noop() -> None:
    cap = 0.3
    emotional = 0.0
    drift_so_far = 0.0

    for _ in range(500):
        result = reconsolidate_salience(
            ReconsolidationState(emotional_salience=emotional, stability_class="stable"),
            1.0,
            0.05,
            drift_so_far,
            is_identity=False,
            drift_cap=cap,
        )
        emotional = result.emotional_salience
        drift_so_far = result.lifetime_drift_total
        if drift_so_far >= cap:
            break

    assert drift_so_far == pytest.approx(cap, abs=1e-9)

    # Further calls at the cap are exact no-ops.
    result_after_cap = reconsolidate_salience(
        ReconsolidationState(emotional_salience=emotional, stability_class="stable"),
        1.0,
        0.05,
        drift_so_far,
        is_identity=False,
        drift_cap=cap,
    )
    assert result_after_cap.emotional_salience_delta == 0.0
    assert result_after_cap.emotional_salience == emotional
    assert result_after_cap.lifetime_drift_total == pytest.approx(cap, abs=1e-9)


def test_drift_cap_is_never_exceeded_regardless_of_eta_or_target() -> None:
    cap = 0.3
    emotional = 0.0
    drift_so_far = 0.0

    for _ in range(50):
        result = reconsolidate_salience(
            ReconsolidationState(emotional_salience=emotional, stability_class="stable"),
            1.0,
            0.3,  # deliberately large eta to overshoot the cap in one step
            drift_so_far,
            is_identity=False,
            drift_cap=cap,
        )
        emotional = result.emotional_salience
        drift_so_far = result.lifetime_drift_total
        assert drift_so_far <= cap + 1e-9

    assert drift_so_far == pytest.approx(cap, abs=1e-9)


# ---------------------------------------------------------------------------
# 3. Reduced-strength (IL7 dream) eta
# ---------------------------------------------------------------------------


def test_reduced_strength_eta_applies_proportionally_smaller_nudge() -> None:
    state = ReconsolidationState(emotional_salience=0.2, stability_class="stable")
    normal = reconsolidate_salience(state, 0.9, 0.05, 0.0, is_identity=False)
    reduced = reconsolidate_salience(state, 0.9, 0.02, 0.0, is_identity=False)

    assert reduced.emotional_salience_delta == pytest.approx(0.02 * (0.9 - 0.2))
    assert abs(reduced.emotional_salience_delta) < abs(normal.emotional_salience_delta)
    assert reduced.emotional_salience_delta == pytest.approx(
        normal.emotional_salience_delta * (0.02 / 0.05)
    )


# ---------------------------------------------------------------------------
# 4. Edge function: apply_reconsolidation + skip guards
# ---------------------------------------------------------------------------


def test_apply_reconsolidation_writes_item_and_provenance(db: Session) -> None:
    item = _make_item(db, emotional_salience=0.2, stability_class="temporary")

    result = apply_reconsolidation(
        db,
        item,
        current_affect_magnitude=0.9,
        eta=0.05,
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )
    db.flush()

    assert result is not None
    assert item.emotional_salience == pytest.approx(0.2 + 0.05 * (0.9 - 0.2))
    assert item.stability_class == "evolving"
    assert item.reconsolidation_drift > 0.0

    logs = db.scalars(
        select(ReconsolidationLog).where(ReconsolidationLog.memory_item_id == item.id)
    ).all()
    fields = {row.field for row in logs}
    assert fields == {"emotional_salience", "stability_class"}
    emotional_log = next(row for row in logs if row.field == "emotional_salience")
    assert emotional_log.old_value == 0.2
    assert emotional_log.new_value == item.emotional_salience
    assert emotional_log.eta == 0.05


def test_apply_reconsolidation_noop_writes_no_provenance(db: Session) -> None:
    """Already-stable, cap-exhausted item: nothing changes, nothing logged."""
    item = _make_item(
        db,
        emotional_salience=0.5,
        stability_class="stable",
        reconsolidation_drift=0.3,
    )

    result = apply_reconsolidation(db, item, current_affect_magnitude=0.9, eta=0.05, drift_cap=0.3)
    db.flush()

    assert result is not None
    assert result.emotional_salience_delta == 0.0
    assert result.stability_upgraded is False
    assert item.emotional_salience == 0.5
    logs = db.scalars(select(ReconsolidationLog)).all()
    assert logs == []


def test_superseded_items_are_never_reconsolidated(db: Session) -> None:
    superseding = _make_item(db, content="newer version")
    item = _make_item(db, superseded_by=superseding.id)

    result = apply_reconsolidation(db, item, current_affect_magnitude=0.9, eta=0.05)

    assert result is None
    assert db.scalars(select(ReconsolidationLog)).all() == []


def test_distilled_tombstones_are_never_reconsolidated(db: Session) -> None:
    item = _make_item(db, distilled_at=datetime.now(UTC))

    result = apply_reconsolidation(db, item, current_affect_magnitude=0.9, eta=0.05)

    assert result is None
    assert db.scalars(select(ReconsolidationLog)).all() == []


# ---------------------------------------------------------------------------
# 5. Exact reversibility (binding)
# ---------------------------------------------------------------------------


def test_reversibility_reconstructs_exact_original_values(db: Session) -> None:
    item = _make_item(db, emotional_salience=0.2, stability_class="temporary")
    original_emotional = item.emotional_salience
    original_stability_rank = float(reconsolidation._STABILITY_STRENGTH[item.stability_class])

    for i in range(5):
        apply_reconsolidation(
            db,
            item,
            current_affect_magnitude=0.9,
            eta=0.05,
            now=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=i),
        )
        db.flush()

    # Sanity: reconsolidation actually changed both fields across 5 calls.
    assert item.emotional_salience != original_emotional
    assert item.stability_class != "temporary"

    reconstructed = original_salience_from_log(db, item_id=item.id)
    assert reconstructed is not None
    assert reconstructed["emotional_salience"] == original_emotional
    assert reconstructed["stability_class"] == original_stability_rank


def test_original_salience_from_log_returns_none_when_never_reconsolidated(
    db: Session,
) -> None:
    item = _make_item(db)
    assert original_salience_from_log(db, item_id=item.id) is None


# ---------------------------------------------------------------------------
# 6. Fires only on context inclusion (wiring through sync_retrieval_feedback)
# ---------------------------------------------------------------------------


def test_sync_reconsolidates_rendered_items_used_and_ignored_but_not_corrected() -> None:
    """Reconsolidation fires for every memory RENDERED into context (recall
    makes the trace labile), whether the answer used it or ignored it — but
    NOT for corrected items (a correction must not strengthen the trace)."""
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_factory(soul_engine)
    runtime_factory = _make_factory(runtime_engine)

    with soul_factory() as soul_db:
        user = User(username="il6-user", password_hash="x", display_name="IL6 User")
        soul_db.add(user)
        soul_db.flush()
        user_id = user.id

        def _item(content: str) -> MemoryItem:
            it = MemoryItem(
                user_id=user_id, content=content, category="fact", importance=3,
                source="extraction", emotional_salience=0.2, stability_class="temporary",
            )
            soul_db.add(it)
            return it

        used_item = _item("Ran a marathon")            # answer cited it
        ignored_item = _item("Mentioned the weather")  # rendered, answer ignored
        corrected_item = _item("Lives in Berlin")      # answer corrected it
        soul_db.commit()
        used_id, ignored_id, corrected_id = used_item.id, ignored_item.id, corrected_item.id

    with runtime_factory() as runtime_db:
        runtime_db.add(AffectStateRow(user_id=user_id, valence=0.6, arousal=0.8, updated_at=datetime.now(UTC)))
        runtime_db.add(MemoryRetrievalFeedback(
            user_id=user_id, run_id=1, memory_item_id=used_id,
            was_used=True, evidence_score=1.0, synced=False,
        ))
        # Run 2: rendered but the answer ignored it → zero_reference (rendered).
        runtime_db.add(MemoryRetrievalFeedback(
            user_id=user_id, run_id=2, memory_item_id=ignored_id,
            was_used=False, evidence_score=0.0, synced=False,
        ))
        # Run 3: the answer corrected it.
        runtime_db.add(MemoryRetrievalFeedback(
            user_id=user_id, run_id=3, memory_item_id=corrected_id,
            was_used=False, was_corrected=True, evidence_score=0.9, synced=False,
        ))
        runtime_db.commit()

    with runtime_factory() as runtime_db, soul_factory() as soul_db:
        result = sync_retrieval_feedback(
            user_id=user_id, runtime_db=runtime_db, soul_db=soul_db, dry_run=False,
        )

        # Both rendered items reconsolidate; the corrected one does not.
        assert used_id in result["reconsolidated_items"]
        assert ignored_id in result["reconsolidated_items"]
        assert corrected_id not in result["reconsolidated_items"]

        assert soul_db.get(MemoryItem, used_id).emotional_salience > 0.2
        assert soul_db.get(MemoryItem, ignored_id).emotional_salience > 0.2
        assert soul_db.get(MemoryItem, corrected_id).emotional_salience == 0.2
        assert soul_db.get(MemoryItem, corrected_id).stability_class == "temporary"

        for mid in (used_id, ignored_id):
            assert soul_db.scalars(
                select(ReconsolidationLog).where(ReconsolidationLog.memory_item_id == mid)
            ).all() != []
        assert soul_db.scalars(
            select(ReconsolidationLog).where(ReconsolidationLog.memory_item_id == corrected_id)
        ).all() == []

    soul_engine.dispose()
    runtime_engine.dispose()


def test_sync_retrieval_feedback_is_idempotent_within_one_sync_cycle() -> None:
    """Several used feedback rows for the same item in one cycle must
    reconsolidate it AT MOST once, not once per row."""
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_factory(soul_engine)
    runtime_factory = _make_factory(runtime_engine)

    with soul_factory() as soul_db:
        user = User(username="il6-idempotent", password_hash="x", display_name="Idempotent")
        soul_db.add(user)
        soul_db.flush()
        user_id = user.id

        item = MemoryItem(
            user_id=user_id,
            content="Loves hiking",
            category="fact",
            importance=3,
            source="extraction",
            emotional_salience=0.2,
            stability_class="temporary",
        )
        soul_db.add(item)
        soul_db.commit()
        item_id = item.id

    with runtime_factory() as runtime_db:
        runtime_db.add(
            AffectStateRow(user_id=user_id, valence=0.6, arousal=0.8, updated_at=datetime.now(UTC))
        )
        for run_id in (1, 2, 3):
            runtime_db.add(
                MemoryRetrievalFeedback(
                    user_id=user_id,
                    run_id=run_id,
                    memory_item_id=item_id,
                    was_used=True,
                    evidence_score=1.0,
                    synced=False,
                )
            )
        runtime_db.commit()

    with runtime_factory() as runtime_db, soul_factory() as soul_db:
        sync_retrieval_feedback(
            user_id=user_id, runtime_db=runtime_db, soul_db=soul_db, dry_run=False
        )

        logs = soul_db.scalars(
            select(ReconsolidationLog).where(ReconsolidationLog.memory_item_id == item_id)
        ).all()
        # Exactly one emotional_salience row and one stability_class row —
        # NOT three of each, despite three used feedback rows this cycle.
        assert len([row for row in logs if row.field == "emotional_salience"]) == 1
        assert len([row for row in logs if row.field == "stability_class"]) == 1

    soul_engine.dispose()
    runtime_engine.dispose()


def test_sync_retrieval_feedback_skips_superseded_and_tombstoned_used_items() -> None:
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_factory(soul_engine)
    runtime_factory = _make_factory(runtime_engine)

    with soul_factory() as soul_db:
        user = User(username="il6-skip", password_hash="x", display_name="Skip")
        soul_db.add(user)
        soul_db.flush()
        user_id = user.id

        newer = MemoryItem(
            user_id=user_id, content="newer", category="fact", importance=3, source="extraction"
        )
        soul_db.add(newer)
        soul_db.flush()

        superseded_item = MemoryItem(
            user_id=user_id,
            content="superseded",
            category="fact",
            importance=3,
            source="extraction",
            superseded_by=newer.id,
        )
        tombstone_item = MemoryItem(
            user_id=user_id,
            content="",
            category="fact",
            importance=3,
            source="extraction",
            memory_class="casual",
            distilled_at=datetime.now(UTC),
        )
        soul_db.add_all([superseded_item, tombstone_item])
        soul_db.commit()
        superseded_id = superseded_item.id
        tombstone_id = tombstone_item.id

    with runtime_factory() as runtime_db:
        runtime_db.add(
            AffectStateRow(user_id=user_id, valence=0.6, arousal=0.8, updated_at=datetime.now(UTC))
        )
        for run_id, item_id in ((1, superseded_id), (2, tombstone_id)):
            runtime_db.add(
                MemoryRetrievalFeedback(
                    user_id=user_id,
                    run_id=run_id,
                    memory_item_id=item_id,
                    was_used=True,
                    evidence_score=1.0,
                    synced=False,
                )
            )
        runtime_db.commit()

    with runtime_factory() as runtime_db, soul_factory() as soul_db:
        result = sync_retrieval_feedback(
            user_id=user_id, runtime_db=runtime_db, soul_db=soul_db, dry_run=False
        )

        assert result["reconsolidated_items"] == {}
        logs = soul_db.scalars(select(ReconsolidationLog)).all()
        assert logs == []

    soul_engine.dispose()
    runtime_engine.dispose()


def test_resolve_current_affect_magnitude_none_without_persisted_row() -> None:
    runtime_engine = _create_runtime_engine()
    runtime_factory = _make_factory(runtime_engine)
    with runtime_factory() as runtime_db:
        assert resolve_current_affect_magnitude(runtime_db, user_id=999) is None
    runtime_engine.dispose()


# ---------------------------------------------------------------------------
# 7. Vault export/import round-trip
# ---------------------------------------------------------------------------


def test_vault_round_trip_preserves_reconsolidation_log_full_scope(db: Session) -> None:
    item = _make_item(db, emotional_salience=0.2, stability_class="temporary")
    apply_reconsolidation(db, item, current_affect_magnitude=0.9, eta=0.05)
    db.flush()
    db.commit()

    snapshot = export_database_snapshot(db, user_id=1)
    assert len(snapshot["reconsolidationLog"]) == 2  # emotional_salience + stability_class

    restore_database_snapshot(db, snapshot, scope="full")
    db.commit()

    restored_logs = db.scalars(select(ReconsolidationLog)).all()
    assert len(restored_logs) == 2

    restored_item = db.get(MemoryItem, item.id)
    assert restored_item.reconsolidation_drift == item.reconsolidation_drift

    reconstructed = original_salience_from_log(db, item_id=item.id)
    assert reconstructed is not None
    assert reconstructed["emotional_salience"] == 0.2


def test_vault_memories_scope_export_includes_reconsolidation_log(db: Session) -> None:
    """The memories-SCOPE export path filters by _MEMORY_TABLES; the log
    must survive it (not just a full export + memories restore), or exact
    reversibility is lost through a memories-scoped vault."""
    from anima_server.services.vault import _build_vault_payload
    from sqlalchemy import delete

    item = _make_item(db, emotional_salience=0.2, stability_class="temporary")
    apply_reconsolidation(db, item, current_affect_magnitude=0.9, eta=0.05)
    db.flush()
    db.commit()

    # Go through the real memories-scope export (not a full export), which
    # is where the _MEMORY_TABLES filter would have dropped the log. The
    # payload nests the filtered snapshot under "database".
    scoped = _build_vault_payload(db, scope="memories")
    assert "reconsolidationLog" in scoped["database"]
    assert len(scoped["database"]["reconsolidationLog"]) == 2

    db.execute(delete(ReconsolidationLog))
    db.commit()

    restore_database_snapshot(db, scoped["database"], scope="memories")
    db.commit()

    assert len(db.scalars(select(ReconsolidationLog)).all()) == 2


# ---------------------------------------------------------------------------
# 8. Eval reset clears the ledger before memory_items
# ---------------------------------------------------------------------------


def test_eval_reset_clears_reconsolidation_log(db: Session) -> None:
    from anima_server.services.eval_reset import _reset_soul_state

    item = _make_item(db, emotional_salience=0.2, stability_class="temporary")
    apply_reconsolidation(db, item, current_affect_magnitude=0.9, eta=0.05)
    db.flush()
    assert db.scalars(select(ReconsolidationLog)).all()  # precondition

    deleted: dict[str, int] = {}
    _reset_soul_state(db, user_id=1, deleted=deleted)
    db.commit()

    assert deleted.get("reconsolidation_log", 0) >= 1
    assert db.scalars(select(ReconsolidationLog)).all() == []


# ---------------------------------------------------------------------------
# 9. Zero LLM / latency (no N+1)
# ---------------------------------------------------------------------------


def test_reconsolidation_module_has_no_llm_seam() -> None:
    source = inspect.getsource(reconsolidation)
    assert "call_llm_for_json" not in source
    assert "llm_json" not in source
    assert "async def" not in source


def test_apply_reconsolidation_issues_no_extra_selects(db: Session) -> None:
    """Per-item reconsolidation is pure arithmetic plus provenance
    insert(s) on the already-loaded item — no additional per-item SELECT,
    so no N+1 as a sync cycle scales to many items."""
    item = _make_item(db, emotional_salience=0.2, stability_class="temporary")
    db.flush()

    statements: list[str] = []
    from sqlalchemy import event

    def _capture(conn, cursor, statement, parameters, context, executemany) -> None:
        statements.append(statement)

    event.listen(db.get_bind(), "before_cursor_execute", _capture)
    try:
        apply_reconsolidation(db, item, current_affect_magnitude=0.9, eta=0.05)
        db.flush()
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", _capture)

    selects = [s for s in statements if s.strip().upper().startswith("SELECT")]
    assert selects == []


def test_sync_reconsolidation_failure_is_isolated_per_item(monkeypatch) -> None:
    """One item's apply_reconsolidation raising must NOT abort the sync:
    the other used item still reconsolidates and the pass commits. Guards
    the per-item SAVEPOINT isolation."""
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_factory(soul_engine)
    runtime_factory = _make_factory(runtime_engine)

    with soul_factory() as soul_db:
        user = User(username="il6-iso", password_hash="x", display_name="Iso")
        soul_db.add(user)
        soul_db.flush()
        user_id = user.id
        boom = MemoryItem(
            user_id=user_id, content="Will fail", category="fact", importance=3,
            source="extraction", emotional_salience=0.2, stability_class="temporary",
        )
        ok = MemoryItem(
            user_id=user_id, content="Will succeed", category="fact", importance=3,
            source="extraction", emotional_salience=0.2, stability_class="temporary",
        )
        soul_db.add_all([boom, ok])
        soul_db.commit()
        boom_id, ok_id = boom.id, ok.id

    with runtime_factory() as runtime_db:
        runtime_db.add(AffectStateRow(user_id=user_id, valence=0.6, arousal=0.8, updated_at=datetime.now(UTC)))
        for mid in (boom_id, ok_id):
            runtime_db.add(MemoryRetrievalFeedback(
                user_id=user_id, run_id=1, memory_item_id=mid,
                was_used=True, evidence_score=1.0, synced=False,
            ))
        runtime_db.commit()

    real_apply = reconsolidation.apply_reconsolidation

    def _selective_apply(db, item, **kwargs):
        if item.id == boom_id:
            # Inject a REAL flush-time failure that poisons the session
            # (NOT NULL violation), not just a Python raise — this is the
            # scenario the savepoint guards. Under the old bare try/except
            # (no rollback) this would leave the session needing rollback
            # and break the next item's flush + the final commit.
            item.emotional_salience = None
            db.flush()
            return None
        return real_apply(db, item, **kwargs)

    monkeypatch.setattr(reconsolidation, "apply_reconsolidation", _selective_apply)

    with runtime_factory() as runtime_db, soul_factory() as soul_db:
        result = sync_retrieval_feedback(
            user_id=user_id, runtime_db=runtime_db, soul_db=soul_db, dry_run=False,
        )
        # The healthy item still reconsolidated; the failed one did not abort
        # it, and the session survived the poisoning flush to commit.
        assert ok_id in result["reconsolidated_items"]
        assert boom_id not in result["reconsolidated_items"]
        assert soul_db.get(MemoryItem, ok_id).emotional_salience > 0.2
        # The savepoint rolled back the NOT NULL violation — boom is intact.
        assert soul_db.get(MemoryItem, boom_id).emotional_salience == 0.2

    soul_engine.dispose()
    runtime_engine.dispose()


def test_forget_memory_clears_reconsolidation_log(db: Session) -> None:
    """Forgetting a reconsolidated memory must delete its reconsolidation_log
    rows — SQLite FK cascades aren't enforced, so orphaned log rows would be
    exported and could reattach to a reused item id (F7 right-to-forget)."""
    from anima_server.models import User
    from anima_server.services.agent.forgetting import forget_memory

    user = User(username="il6-forget", password_hash="x", display_name="F")
    db.add(user)
    db.flush()
    item = _make_item(db, user_id=user.id, emotional_salience=0.2, stability_class="temporary")
    apply_reconsolidation(db, item, current_affect_magnitude=0.9, eta=0.05)
    db.flush()
    item_id = item.id
    assert db.scalars(
        select(ReconsolidationLog).where(ReconsolidationLog.memory_item_id == item_id)
    ).all()  # precondition

    forget_memory(db, memory_id=item_id, user_id=user.id)

    assert db.scalars(
        select(ReconsolidationLog).where(ReconsolidationLog.memory_item_id == item_id)
    ).all() == []
    assert db.get(MemoryItem, item_id) is None


def test_ignored_memory_in_mixed_run_still_reconsolidates() -> None:
    """A run that renders two memories and the answer uses one but ignores
    the other: the ignored one is rendered-in-context and must reconsolidate.
    It lands in unused_counts (per-row) but NOT zero_reference_counts
    (per-run, only when the whole run was ignored) — the regression this
    guards."""
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_factory(soul_engine)
    runtime_factory = _make_factory(runtime_engine)

    with soul_factory() as soul_db:
        user = User(username="il6-mixed", password_hash="x", display_name="Mixed")
        soul_db.add(user)
        soul_db.flush()
        user_id = user.id

        def _item(content: str) -> MemoryItem:
            it = MemoryItem(
                user_id=user_id, content=content, category="fact", importance=3,
                source="extraction", emotional_salience=0.2, stability_class="temporary",
            )
            soul_db.add(it)
            return it

        used_item = _item("Cited in the answer")
        ignored_item = _item("Rendered but ignored")
        soul_db.commit()
        used_id, ignored_id = used_item.id, ignored_item.id

    with runtime_factory() as runtime_db:
        runtime_db.add(AffectStateRow(user_id=user_id, valence=0.6, arousal=0.8, updated_at=datetime.now(UTC)))
        # SAME run_id — a mixed run: one used, one ignored.
        runtime_db.add(MemoryRetrievalFeedback(
            user_id=user_id, run_id=1, memory_item_id=used_id,
            was_used=True, evidence_score=1.0, synced=False,
        ))
        runtime_db.add(MemoryRetrievalFeedback(
            user_id=user_id, run_id=1, memory_item_id=ignored_id,
            was_used=False, evidence_score=0.0, synced=False,
        ))
        runtime_db.commit()

    with runtime_factory() as runtime_db, soul_factory() as soul_db:
        result = sync_retrieval_feedback(
            user_id=user_id, runtime_db=runtime_db, soul_db=soul_db, dry_run=False,
        )
        # Both the used AND the ignored-but-rendered memory reconsolidate.
        assert used_id in result["reconsolidated_items"]
        assert ignored_id in result["reconsolidated_items"]
        assert soul_db.get(MemoryItem, ignored_id).emotional_salience > 0.2

    soul_engine.dispose()
    runtime_engine.dispose()
