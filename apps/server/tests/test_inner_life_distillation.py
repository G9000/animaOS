"""Tests for IL5 — Forgetting as Distillation (F7 extension).

Covers: the distill flow (tendency claim + ledger + tombstone + audit),
class exemptions, multi-contributor merging, the right-to-forget property
test (distill -> forget == never distilled), retrieval-surface exclusion,
the per-run cap, the zero-LLM assertion, and vault export/import
round-tripping of the ledger + tombstone linkage.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

import pytest
from anima_server.db.base import Base
from anima_server.models import (
    ForgetAuditLog,
    MemoryClaim,
    MemoryItem,
    MemoryItemEvidence,
    TendencyContribution,
)
from anima_server.services.agent import distillation
from anima_server.services.agent.distillation import (
    DISTILL_MEMORY_CLASSES,
    distill_due_items,
    recompute_tendency_from_ledger,
)
from anima_server.services.agent.forgetting import HEAT_VISIBILITY_FLOOR, forget_memory
from anima_server.services.agent.memory_store import get_memory_items, get_memory_items_scored
from anima_server.services.agent.provenance import add_memory_item_evidence
from anima_server.services.data_crypto import df
from anima_server.services.vault import export_database_snapshot, restore_database_snapshot
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

_BELOW_FLOOR = HEAT_VISIBILITY_FLOOR / 2


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
    content: str = "annoyed about the commute again",
    category: str = "fact",
    memory_class: str = "casual",
    importance: int = 3,
    emotional_salience: float = 0.4,
    heat: float = _BELOW_FLOOR,
    superseded_by: int | None = None,
    distilled_at: datetime | None = None,
) -> MemoryItem:
    item = MemoryItem(
        user_id=user_id,
        content=content,
        category=category,
        importance=importance,
        source="extraction",
        heat=heat,
        memory_class=memory_class,
        emotional_salience=emotional_salience,
        embedding_json=[0.1, 0.2, 0.3],
        embedding_checksum="deadbeef",
        superseded_by=superseded_by,
        distilled_at=distilled_at,
    )
    db.add(item)
    db.flush()
    return item


# ---------------------------------------------------------------------------
# 0. Reachability: eligibility must be attainable through REAL heat decay,
#    not just direct heat assignment (the trigger predicate is
#    floor-equality, and the visibility floor is unreachable for
#    schema-valid items — see the module eligibility comment).
# ---------------------------------------------------------------------------


def test_real_decay_drives_casual_item_to_its_floor_and_it_distills(db: Session) -> None:
    """End-to-end reachability: a casual item created through the normal
    fields, never referenced and far in the past, is decayed by the real
    decay_all_heat pass to its own floor, then distilled — no direct heat
    assignment anywhere."""
    from anima_server.services.agent.heat_scoring import decay_all_heat

    ancient = datetime(2020, 1, 1, tzinfo=UTC)
    item = MemoryItem(
        user_id=1,
        content="offhand remark about the weather",
        category="fact",
        importance=1,
        source="extraction",
        memory_class="casual",
        emotional_salience=0.0,
        reference_count=0,
        last_referenced_at=None,
        created_at=ancient,
        embedding_json=[0.1, 0.2, 0.3],
        embedding_checksum="deadbeef",
    )
    db.add(item)
    db.flush()
    item_id = item.id

    # Real decay — this is what the sleep task runs immediately before the
    # sweep. With no recency/access signal the heat lands on the floor.
    decay_all_heat(db, user_id=1, now=datetime(2026, 1, 1, tzinfo=UTC))
    db.flush()
    decayed = db.get(MemoryItem, item_id)
    assert decayed.heat > 0.0  # scored, not "never scored"
    assert decayed.heat <= distillation.item_heat_floor(decayed) + 1e-9

    result = distill_due_items(db, user_id=1, max_per_run=20)
    assert result.distilled == 1
    assert db.get(MemoryItem, item_id).distilled_at is not None


def test_recently_referenced_casual_item_does_not_distill(db: Session) -> None:
    """Negative reachability: a casual item still warm from recent access
    sits above its floor and must NOT distill, even after a decay pass."""
    from anima_server.services.agent.heat_scoring import decay_all_heat

    now = datetime(2026, 1, 1, tzinfo=UTC)
    item = MemoryItem(
        user_id=1,
        content="just mentioned the commute five minutes ago",
        category="fact",
        importance=1,
        source="extraction",
        memory_class="casual",
        emotional_salience=0.0,
        reference_count=5,
        last_referenced_at=now,
        created_at=now,
        embedding_json=[0.1, 0.2, 0.3],
        embedding_checksum="deadbeef",
    )
    db.add(item)
    db.flush()
    item_id = item.id

    decay_all_heat(db, user_id=1, now=now)
    db.flush()
    warm = db.get(MemoryItem, item_id)
    assert warm.heat > distillation.item_heat_floor(warm) + 1e-9

    result = distill_due_items(db, user_id=1, max_per_run=20)
    assert result.distilled == 0
    assert db.get(MemoryItem, item_id).distilled_at is None


# ---------------------------------------------------------------------------
# 1. Basic distill flow
# ---------------------------------------------------------------------------


def test_sub_floor_casual_item_distills_into_tendency_claim(db: Session) -> None:
    item = _make_item(db, memory_class="casual", content="stressed about the commute")
    add_memory_item_evidence(
        db,
        user_id=1,
        memory_item_id=item.id,
        evidence_text="ugh the commute again",
        source_kind="user_message",
    )
    db.flush()
    item_id = item.id

    result = distill_due_items(db, user_id=1, max_per_run=20)

    assert result.distilled == 1
    assert result.failed == 0

    refreshed = db.get(MemoryItem, item_id)
    assert refreshed is not None
    assert df(1, refreshed.content, table="memory_items", field="content") == ""
    assert refreshed.embedding_json is None
    assert refreshed.embedding_checksum is None
    assert refreshed.distilled_at is not None
    # Retained tombstone shell:
    assert refreshed.memory_class == "casual"
    assert refreshed.category == "fact"
    assert refreshed.created_at is not None

    evidence = db.scalars(
        select(MemoryItemEvidence).where(MemoryItemEvidence.memory_item_id == item_id)
    ).all()
    assert list(evidence) == []

    claim = db.scalar(select(MemoryClaim).where(MemoryClaim.namespace == "tendency"))
    assert claim is not None
    assert claim.status == "active"
    assert isinstance(claim.value_json, dict)
    assert "strength" in claim.value_json

    contribution = db.scalar(
        select(TendencyContribution).where(TendencyContribution.tombstone_item_id == item_id)
    )
    assert contribution is not None
    assert contribution.tendency_claim_id == claim.id
    assert set(contribution.contribution_vector.keys()) == {"strength", "valence_hint"}
    for value in contribution.contribution_vector.values():
        assert isinstance(value, (int, float))

    audit = db.scalar(
        select(ForgetAuditLog).where(ForgetAuditLog.trigger == "passive_decay")
    )
    assert audit is not None
    assert audit.scope == "distilled"
    assert audit.items_forgotten == 1


def test_distillation_is_idempotent(db: Session) -> None:
    """Re-running the sweep over an already-distilled item is a no-op."""
    _make_item(db)
    distill_due_items(db, user_id=1, max_per_run=20)

    result = distill_due_items(db, user_id=1, max_per_run=20)
    assert result.distilled == 0
    assert result.failed == 0

    contributions = db.scalars(select(TendencyContribution)).all()
    assert len(contributions) == 1


# ---------------------------------------------------------------------------
# 2. Class exemptions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("memory_class", ["identity", "life_event", "relationship", "active_project"])
def test_exempt_classes_never_distill(db: Session, memory_class: str) -> None:
    item = _make_item(db, memory_class=memory_class, content="my name is Alex")
    item_id = item.id

    result = distill_due_items(db, user_id=1, max_per_run=20)

    assert result.distilled == 0
    refreshed = db.get(MemoryItem, item_id)
    assert refreshed is not None
    assert refreshed.distilled_at is None
    assert df(1, refreshed.content, table="memory_items", field="content") != ""
    assert db.scalars(select(TendencyContribution)).all() == []
    assert memory_class not in DISTILL_MEMORY_CLASSES


def test_superseded_and_already_distilled_items_are_skipped(db: Session) -> None:
    superseded = _make_item(db, content="superseded casual note")
    target = _make_item(db, content="target casual note")
    superseded.superseded_by = target.id
    db.flush()

    already_distilled = _make_item(
        db, content="already distilled note", distilled_at=datetime.now(UTC)
    )

    result = distill_due_items(db, user_id=1, max_per_run=20)

    # Only `target` is eligible: `superseded` is superseded, and
    # `already_distilled` is (as the name says) already distilled.
    assert result.distilled == 1
    assert db.get(MemoryItem, superseded.id).distilled_at is None
    assert db.get(MemoryItem, already_distilled.id).distilled_at is not None


# ---------------------------------------------------------------------------
# 3. Multi-contributor merge
# ---------------------------------------------------------------------------


def test_two_same_signature_items_merge_into_one_tendency(db: Session) -> None:
    item_a = _make_item(db, content="stressed about work deadlines", importance=2, emotional_salience=0.2)
    item_b = _make_item(db, content="stressed about work deadlines", importance=4, emotional_salience=0.6)

    result = distill_due_items(db, user_id=1, max_per_run=20)
    assert result.distilled == 2

    claims = db.scalars(select(MemoryClaim).where(MemoryClaim.namespace == "tendency")).all()
    assert len(claims) == 1
    claim = claims[0]

    contributions = db.scalars(
        select(TendencyContribution).where(TendencyContribution.tendency_claim_id == claim.id)
    ).all()
    assert len(contributions) == 2
    assert {c.tombstone_item_id for c in contributions} == {item_a.id, item_b.id}

    # Strength is documented as the MEAN of surviving ledger rows.
    expected_strength = round((2 / 5 + 4 / 5) / 2, 4)
    expected_valence = round((0.2 + 0.6) / 2, 4)
    assert claim.value_json["strength"] == pytest.approx(expected_strength)
    assert claim.value_json["valence_hint"] == pytest.approx(expected_valence)
    assert claim.value_json["contributor_count"] == 2


def test_recompute_tendency_from_ledger_is_the_single_source_of_truth(db: Session) -> None:
    _make_item(db)
    distill_due_items(db, user_id=1, max_per_run=20)
    claim = db.scalar(select(MemoryClaim).where(MemoryClaim.namespace == "tendency"))
    assert claim is not None

    recomputed = recompute_tendency_from_ledger(db, tendency_claim_id=claim.id)
    assert recomputed == claim.value_json

    # No surviving rows -> None (caller deletes the claim).
    db.execute(
        delete(TendencyContribution).where(
            TendencyContribution.tendency_claim_id == claim.id
        )
    )
    db.flush()
    assert recompute_tendency_from_ledger(db, tendency_claim_id=claim.id) is None


# ---------------------------------------------------------------------------
# 4. Right-to-forget precedence (property test, non-negotiable per PRD)
# ---------------------------------------------------------------------------


def test_forget_after_distill_single_contributor_removes_tendency_entirely(db: Session) -> None:
    item = _make_item(db, content="single contributor tendency")
    distill_due_items(db, user_id=1, max_per_run=20)
    claim = db.scalar(select(MemoryClaim).where(MemoryClaim.namespace == "tendency"))
    assert claim is not None
    claim_id = claim.id

    forget_memory(db, memory_id=item.id, user_id=1)

    assert db.get(MemoryClaim, claim_id) is None
    assert db.scalars(select(TendencyContribution)).all() == []
    assert db.get(MemoryItem, item.id) is None


def test_forget_after_distill_multi_contributor_recomputes_exactly(db: Session) -> None:
    """Property test: distill(item) -> forget_memory(item) leaves tendency
    state EXACTLY as if `item` had never been distilled at all — same
    claim id, same recomputed strength as a fresh distillation of only the
    surviving item(s)."""
    forgotten = _make_item(
        db, content="shared tendency topic", importance=5, emotional_salience=0.9
    )
    survivor = _make_item(
        db, content="shared tendency topic", importance=1, emotional_salience=0.1
    )

    distill_due_items(db, user_id=1, max_per_run=20)
    claim = db.scalar(select(MemoryClaim).where(MemoryClaim.namespace == "tendency"))
    assert claim is not None
    claim_id = claim.id

    forget_memory(db, memory_id=forgotten.id, user_id=1)

    # Claim survives (survivor is still a contributor) with the SAME id.
    remaining_claim = db.get(MemoryClaim, claim_id)
    assert remaining_claim is not None
    remaining_contributions = db.scalars(
        select(TendencyContribution).where(TendencyContribution.tendency_claim_id == claim_id)
    ).all()
    assert len(remaining_contributions) == 1
    assert remaining_contributions[0].tombstone_item_id == survivor.id

    # Build the "never distilled" reference universe: a fresh claim/ledger
    # from distilling ONLY the survivor, in an independent session.
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    ReferenceSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with ReferenceSession() as ref_db:
        _make_item(
            ref_db, content="shared tendency topic", importance=1, emotional_salience=0.1
        )
        distill_due_items(ref_db, user_id=1, max_per_run=20)
        ref_claim = ref_db.scalar(select(MemoryClaim).where(MemoryClaim.namespace == "tendency"))
        assert ref_claim is not None

        assert remaining_claim.value_json == ref_claim.value_json
        assert remaining_claim.canonical_key == ref_claim.canonical_key


# ---------------------------------------------------------------------------
# 5. Retrieval-surface exclusion
# ---------------------------------------------------------------------------


def test_tombstones_excluded_from_scored_retrieval_and_listing(db: Session) -> None:
    item = _make_item(db, content="will be distilled")
    distill_due_items(db, user_id=1, max_per_run=20)

    listed = get_memory_items(db, user_id=1)
    assert item.id not in {i.id for i in listed}

    scored = get_memory_items_scored(db, user_id=1)
    assert item.id not in {i.id for i in scored}


# ---------------------------------------------------------------------------
# 6. Per-run cap
# ---------------------------------------------------------------------------


def test_distill_max_per_run_is_respected(db: Session) -> None:
    for i in range(5):
        _make_item(db, content=f"distinct casual topic number {i}")

    result = distill_due_items(db, user_id=1, max_per_run=2)
    assert result.distilled == 2

    remaining = db.scalars(
        select(MemoryItem).where(MemoryItem.distilled_at.is_(None))
    ).all()
    assert len(remaining) == 3


# ---------------------------------------------------------------------------
# 7. Zero-LLM assertion
# ---------------------------------------------------------------------------


def test_distillation_module_has_no_llm_seam() -> None:
    source = inspect.getsource(distillation)
    assert "call_llm_for_json" not in source
    assert "llm_json" not in source
    assert "async def" not in source


# ---------------------------------------------------------------------------
# 8. Vault export/import round-trip
# ---------------------------------------------------------------------------


def test_vault_round_trip_preserves_tendency_ledger_full_scope(db: Session) -> None:
    _make_item(db, content="vault round trip topic")
    distill_due_items(db, user_id=1, max_per_run=20)

    snapshot = export_database_snapshot(db, user_id=1)
    assert len(snapshot["memoryClaims"]) == 1
    assert len(snapshot["tendencyContributions"]) == 1

    restore_database_snapshot(db, snapshot, scope="full")
    db.commit()

    claims = db.scalars(select(MemoryClaim).where(MemoryClaim.namespace == "tendency")).all()
    assert len(claims) == 1
    contributions = db.scalars(select(TendencyContribution)).all()
    assert len(contributions) == 1
    assert contributions[0].tendency_claim_id == claims[0].id

    tombstones = db.scalars(
        select(MemoryItem).where(MemoryItem.distilled_at.is_not(None))
    ).all()
    assert len(tombstones) == 1
    assert tombstones[0].id == contributions[0].tombstone_item_id


def test_vault_memories_scope_restores_tendency_ledger(db: Session) -> None:
    _make_item(db, content="memories scope topic")
    distill_due_items(db, user_id=1, max_per_run=20)

    snapshot = export_database_snapshot(db, user_id=1)

    db.execute(delete(TendencyContribution))
    db.execute(delete(MemoryClaim))
    db.commit()

    restore_database_snapshot(db, snapshot, scope="memories")
    db.commit()

    claims = db.scalars(select(MemoryClaim).where(MemoryClaim.namespace == "tendency")).all()
    assert len(claims) == 1
    contributions = db.scalars(select(TendencyContribution)).all()
    assert len(contributions) == 1


# ---------------------------------------------------------------------------
# 9. Memory overview counts (real migrated per-user DB, real route)
# ---------------------------------------------------------------------------


def test_memory_overview_excludes_distilled_tombstones() -> None:
    """Integration check against a real per-user SQLite DB migrated with
    Alembic (validates the IL5 migration itself, not just the ORM model)
    and the real ``/api/memory/{user_id}`` overview route."""
    from anima_server.db.session import get_user_session_factory
    from conftest import managed_test_client

    with managed_test_client("anima-distillation-test-") as client:
        response = client.post(
            "/api/auth/register",
            json={"username": "distilltest", "password": "pw123456", "name": "Distill Test"},
        )
        assert response.status_code == 201
        reg = response.json()
        user_id = int(reg["id"])
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.post(
            f"/api/memory/{user_id}/items",
            headers=headers,
            json={"content": "Will be distilled", "category": "fact", "importance": 3},
        )
        assert resp.status_code == 201
        item_id = int(resp.json()["id"])

        resp = client.get(f"/api/memory/{user_id}", headers=headers)
        assert resp.json()["factCount"] == 1

        # Simulate the sleep-time distillation outcome directly against the
        # real migrated schema: gutted content + distilled_at set.
        with get_user_session_factory(user_id)() as db:
            item = db.get(MemoryItem, item_id)
            assert item is not None
            item.content = ""
            item.embedding_json = None
            item.embedding_checksum = None
            item.distilled_at = datetime.now(UTC)
            db.commit()

        resp = client.get(f"/api/memory/{user_id}", headers=headers)
        overview = resp.json()
        assert overview["factCount"] == 0
        assert overview["totalItems"] == 0


# ---------------------------------------------------------------------------
# 11. Maintenance sweeps must not treat tombstones as active work
# ---------------------------------------------------------------------------


def test_backfill_embeddings_skips_distilled_tombstones(db: Session) -> None:
    """A distilled tombstone (empty content, null embedding) must not be
    picked up by embedding backfill — it would scatter to None forever and
    starve real unembedded memories."""
    import asyncio

    from anima_server.services.agent.embeddings import backfill_embeddings

    item = _make_item(db, memory_class="casual", content="to be distilled")
    add_memory_item_evidence(
        db, user_id=1, memory_item_id=item.id,
        evidence_text="x", source_kind="user_message",
    )
    db.flush()
    distill_due_items(db, user_id=1, max_per_run=20)

    # The tombstone now has embedding_json=None; backfill must ignore it.
    embedded = asyncio.run(backfill_embeddings(db, user_id=1, batch_size=50))
    assert embedded == 0


def test_evidence_audit_excludes_distilled_tombstones(db: Session) -> None:
    """Distilled tombstones have their evidence hard-deleted by design and
    must not be reported as active memories missing evidence."""
    from anima_server.services.agent.provenance import audit_memory_item_evidence

    item = _make_item(db, memory_class="casual", content="to be distilled")
    add_memory_item_evidence(
        db, user_id=1, memory_item_id=item.id,
        evidence_text="x", source_kind="user_message",
    )
    db.flush()
    distill_due_items(db, user_id=1, max_per_run=20)

    report = audit_memory_item_evidence(db, user_id=1)
    assert report.missing_evidence == 0


def test_decay_all_heat_leaves_tombstones_untouched(db: Session) -> None:
    """decay_all_heat must skip distilled tombstones — their heat is frozen
    and rescoring them every sleep run is unbounded busywork."""
    from anima_server.services.agent.heat_scoring import decay_all_heat

    item = _make_item(db, memory_class="casual", content="to be distilled")
    add_memory_item_evidence(
        db, user_id=1, memory_item_id=item.id,
        evidence_text="x", source_kind="user_message",
    )
    db.flush()
    distill_due_items(db, user_id=1, max_per_run=20)
    item_id = item.id
    frozen_heat = db.get(MemoryItem, item_id).heat

    updated = decay_all_heat(db, user_id=1, now=datetime(2027, 1, 1, tzinfo=UTC))
    db.flush()
    # The tombstone was not in the working set...
    assert db.get(MemoryItem, item_id).heat == frozen_heat
    assert updated == 0
