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


def test_identity_items_get_no_emotional_nudge_and_no_drift_accrual() -> None:
    state = ReconsolidationState(emotional_salience=0.3, stability_class="temporary")
    result = reconsolidate_salience(state, 0.9, 0.05, 0.0, is_identity=True)

    assert result.emotional_salience == 0.3
    assert result.emotional_salience_delta == 0.0
    assert result.lifetime_drift_total == 0.0
    # Not named exempt by the PRD — stability re-evaluation still applies.
    assert result.stability_class == "evolving"
    assert result.stability_upgraded is True


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


def test_sync_retrieval_feedback_reconsolidates_used_items_only() -> None:
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_factory(soul_engine)
    runtime_factory = _make_factory(runtime_engine)

    with soul_factory() as soul_db:
        user = User(username="il6-user", password_hash="x", display_name="IL6 User")
        soul_db.add(user)
        soul_db.flush()
        user_id = user.id

        used_item = MemoryItem(
            user_id=user_id,
            content="Ran a marathon",
            category="fact",
            importance=3,
            source="extraction",
            emotional_salience=0.2,
            stability_class="temporary",
        )
        scored_only_item = MemoryItem(
            user_id=user_id,
            content="Mentioned the weather once",
            category="fact",
            importance=3,
            source="extraction",
            emotional_salience=0.2,
            stability_class="temporary",
        )
        soul_db.add_all([used_item, scored_only_item])
        soul_db.commit()
        used_item_id = used_item.id
        scored_only_item_id = scored_only_item.id

    with runtime_factory() as runtime_db:
        # A real (non-default-seeded) affect row so the emotional nudge has
        # a genuine signal to move toward.
        runtime_db.add(
            AffectStateRow(
                user_id=user_id,
                valence=0.6,
                arousal=0.8,
                updated_at=datetime.now(UTC),
            )
        )
        runtime_db.add(
            MemoryRetrievalFeedback(
                user_id=user_id,
                run_id=1,
                memory_item_id=used_item_id,
                was_used=True,
                evidence_score=1.0,
                synced=False,
            )
        )
        # Separate run: the only feedback row for this item is NOT used, so
        # it lands in zero_reference_counts (still processed by the sync
        # loop for heat decay) but must not be reconsolidated.
        runtime_db.add(
            MemoryRetrievalFeedback(
                user_id=user_id,
                run_id=2,
                memory_item_id=scored_only_item_id,
                was_used=False,
                evidence_score=0.0,
                synced=False,
            )
        )
        runtime_db.commit()

    with runtime_factory() as runtime_db, soul_factory() as soul_db:
        result = sync_retrieval_feedback(
            user_id=user_id,
            runtime_db=runtime_db,
            soul_db=soul_db,
            dry_run=False,
        )

        assert used_item_id in result["reconsolidated_items"]
        assert scored_only_item_id not in result["reconsolidated_items"]

        used_refreshed = soul_db.get(MemoryItem, used_item_id)
        scored_only_refreshed = soul_db.get(MemoryItem, scored_only_item_id)

        assert used_refreshed.emotional_salience > 0.2
        assert used_refreshed.stability_class == "evolving"
        assert scored_only_refreshed.emotional_salience == 0.2
        assert scored_only_refreshed.stability_class == "temporary"

        used_logs = soul_db.scalars(
            select(ReconsolidationLog).where(
                ReconsolidationLog.memory_item_id == used_item_id
            )
        ).all()
        scored_only_logs = soul_db.scalars(
            select(ReconsolidationLog).where(
                ReconsolidationLog.memory_item_id == scored_only_item_id
            )
        ).all()
        assert used_logs != []
        assert scored_only_logs == []

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


def test_vault_memories_scope_restores_reconsolidation_log(db: Session) -> None:
    from sqlalchemy import delete

    item = _make_item(db, emotional_salience=0.2, stability_class="temporary")
    apply_reconsolidation(db, item, current_affect_magnitude=0.9, eta=0.05)
    db.flush()
    db.commit()

    snapshot = export_database_snapshot(db, user_id=1)

    db.execute(delete(ReconsolidationLog))
    db.commit()

    restore_database_snapshot(db, snapshot, scope="memories")
    db.commit()

    restored_logs = db.scalars(select(ReconsolidationLog)).all()
    assert len(restored_logs) == 2


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
