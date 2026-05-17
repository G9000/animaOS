"""Tests for F7 — Intentional Forgetting."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from anima_server.db.base import Base
from anima_server.models import (
    ForgetAuditLog,
    MemoryClaim,
    MemoryClaimEvidence,
    MemoryEpisode,
    MemoryItem,
    MemoryItemEvidence,
)
from anima_server.models.consciousness import SelfModelBlock
from anima_server.services.agent.forgetting import (
    HEAT_VISIBILITY_FLOOR,
    SUPERSEDED_DECAY_MULTIPLIER,
    DerivedReferences,
    find_derived_references,
    forget_by_topic,
    forget_memory,
    redact_derived_references,
    suppress_memory,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture()
def db() -> Session:  # type: ignore[misc]
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _make_item(
    db: Session,
    *,
    user_id: int = 1,
    content: str = "test memory",
    category: str = "fact",
    importance: int = 3,
    heat: float = 0.0,
    superseded_by: int | None = None,
) -> MemoryItem:
    item = MemoryItem(
        user_id=user_id,
        content=content,
        category=category,
        importance=importance,
        source="extraction",
        heat=heat,
        superseded_by=superseded_by,
    )
    db.add(item)
    db.flush()
    return item


def _make_episode(
    db: Session,
    *,
    user_id: int = 1,
    summary: str = "test episode",
) -> MemoryEpisode:
    ep = MemoryEpisode(
        user_id=user_id,
        date="2026-03-19",
        summary=summary,
        significance_score=3,
    )
    db.add(ep)
    db.flush()
    return ep


def _make_self_model_block(
    db: Session,
    *,
    user_id: int = 1,
    section: str = "growth_log",
    content: str = "test growth log",
) -> SelfModelBlock:
    block = SelfModelBlock(
        user_id=user_id,
        section=section,
        content=content,
    )
    db.add(block)
    db.flush()
    return block


# ── T1: Visibility floor filter ──────────────────────────────────────


class TestVisibilityFloor:
    def test_floor_constant_is_positive(self):
        assert HEAT_VISIBILITY_FLOOR > 0.0
        assert HEAT_VISIBILITY_FLOOR == 0.01

    def test_item_below_floor_excluded_from_retrieval(self, db: Session):
        """Items with heat below HEAT_VISIBILITY_FLOOR should be excluded
        from scored retrieval (verified in memory_store integration)."""
        low_heat = _make_item(db, heat=0.005, content="low heat")
        high_heat = _make_item(db, heat=0.5, content="high heat")

        # Verify the items exist
        assert low_heat.heat < HEAT_VISIBILITY_FLOOR
        assert high_heat.heat >= HEAT_VISIBILITY_FLOOR

    def test_zero_heat_not_excluded(self, db: Session):
        """Items with heat=0.0 (never scored) should NOT be excluded."""
        unscored = _make_item(db, heat=0.0, content="unscored item")
        assert unscored.heat == 0.0
        # Zero heat means "not yet scored", distinct from "decayed below floor"

    def test_get_memory_items_scored_excludes_below_floor(self, db: Session):
        """Integration: get_memory_items_scored filters out sub-floor items."""
        from anima_server.services.agent.memory_store import get_memory_items_scored

        _make_item(db, heat=0.005, content="below floor")
        _make_item(db, heat=0.5, content="above floor")
        _make_item(db, heat=0.0, content="unscored")

        results = get_memory_items_scored(db, user_id=1)

        contents = [r.content for r in results]
        assert "above floor" in contents
        assert "unscored" in contents
        assert "below floor" not in contents


# ── T2: Superseded decay multiplier ──────────────────────────────────


class TestSupersededDecay:
    def test_multiplier_value(self):
        assert SUPERSEDED_DECAY_MULTIPLIER == 3.0

    def test_superseded_decay_faster(self):
        """Superseded items should decay 3x faster (lower tau)."""
        from anima_server.services.agent.heat_scoring import (
            RECENCY_TAU_HOURS,
            compute_time_decay,
        )

        now = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)
        last_accessed = datetime(2026, 3, 18, 12, 0, 0, tzinfo=UTC)  # 24h ago

        normal_decay = compute_time_decay(
            last_accessed,
            now,
            tau_hours=RECENCY_TAU_HOURS,
        )
        superseded_decay = compute_time_decay(
            last_accessed,
            now,
            tau_hours=RECENCY_TAU_HOURS / SUPERSEDED_DECAY_MULTIPLIER,
        )

        # Superseded should decay faster (lower value)
        assert superseded_decay < normal_decay
        # With 3x faster decay, the ratio should be significant
        assert normal_decay / superseded_decay > 2.0


# ── T3: suppress_memory ──────────────────────────────────────────────


class TestSuppressMemory:
    def test_suppress_flags_derived_refs(self, db: Session):
        item = _make_item(db, content="Works as a teacher")
        new_item = _make_item(db, content="Works as an engineer")
        item.superseded_by = new_item.id

        ep = _make_episode(db, summary="Discussed that user Works as a teacher")
        block = _make_self_model_block(
            db,
            section="growth_log",
            content="User revealed: Works as a teacher",
        )

        result = suppress_memory(
            db,
            memory_id=item.id,
            superseded_by=new_item.id,
            user_id=1,
        )

        assert result.derived_refs_flagged == 2
        assert result.audit_log_id is not None

        # Verify flags set
        db.refresh(ep)
        db.refresh(block)
        assert ep.needs_regeneration is True
        assert block.needs_regeneration is True

    def test_suppress_creates_audit_log(self, db: Session):
        item = _make_item(db, content="some fact")
        new_item = _make_item(db, content="updated fact")
        item.superseded_by = new_item.id

        result = suppress_memory(
            db,
            memory_id=item.id,
            superseded_by=new_item.id,
            user_id=1,
        )

        log = db.get(ForgetAuditLog, result.audit_log_id)
        assert log is not None
        assert log.trigger == "suppression"
        assert log.scope == "single"
        assert log.items_forgotten == 0  # suppression does not delete

    def test_suppress_nonexistent_memory(self, db: Session):
        result = suppress_memory(
            db,
            memory_id=9999,
            superseded_by=1,
            user_id=1,
        )
        assert result.derived_refs_flagged == 0


# ── T4: forget_memory ─────────────────────────────────────────────────


class TestForgetMemory:
    def test_hard_delete_and_audit(self, db: Session):
        item = _make_item(db, content="secret fact")
        item_id = item.id

        result = forget_memory(db, memory_id=item_id, user_id=1)

        assert result.items_forgotten == 1
        assert result.audit_log_id is not None

        # Item should be gone
        assert db.get(MemoryItem, item_id) is None

    def test_audit_log_has_no_content(self, db: Session):
        item = _make_item(db, content="confidential info about someone")
        item_id = item.id

        result = forget_memory(db, memory_id=item_id, user_id=1)

        log = db.get(ForgetAuditLog, result.audit_log_id)
        assert log is not None
        assert log.trigger == "user_request"
        assert log.items_forgotten == 1
        # The audit log must NOT contain the forgotten content
        assert "confidential" not in str(log.scope)
        assert "confidential" not in str(log.trigger)

    def test_forget_deletes_claims(self, db: Session):
        item = _make_item(db, content="some claim")
        claim = MemoryClaim(
            user_id=1,
            subject_type="user",
            namespace="fact",
            slot="test",
            value_text="some claim",
            canonical_key="user:fact:test",
            memory_item_id=item.id,
        )
        db.add(claim)
        db.flush()
        evidence = MemoryClaimEvidence(
            claim_id=claim.id,
            source_text="user said something",
            source_kind="user_message",
        )
        db.add(evidence)
        db.flush()

        claim_id = claim.id
        evidence_id = evidence.id

        forget_memory(db, memory_id=item.id, user_id=1)

        assert db.get(MemoryClaim, claim_id) is None
        assert db.get(MemoryClaimEvidence, evidence_id) is None

    def test_forget_deletes_memory_item_evidence_for_supersession_chain(self, db: Session):
        old_item = _make_item(db, content="old secret")
        current_item = _make_item(db, content="current secret")
        old_item.superseded_by = current_item.id
        db.add_all(
            [
                MemoryItemEvidence(
                    user_id=1,
                    memory_item_id=old_item.id,
                    source_kind="explicit_save",
                    evidence_text="old secret evidence",
                ),
                MemoryItemEvidence(
                    user_id=1,
                    memory_item_id=current_item.id,
                    source_kind="explicit_save",
                    evidence_text="current secret evidence",
                ),
            ]
        )
        db.flush()

        forget_memory(db, memory_id=current_item.id, user_id=1)

        remaining = db.scalar(
            select(func.count())
            .select_from(MemoryItemEvidence)
            .where(MemoryItemEvidence.memory_item_id.in_([old_item.id, current_item.id]))
        )
        assert remaining == 0

    def test_forget_flags_derived_refs(self, db: Session):
        item = _make_item(db, content="Likes hiking")
        _make_episode(db, summary="User mentioned they Likes hiking in the park")
        _make_self_model_block(
            db,
            section="growth_log",
            content="Learned: Likes hiking",
        )

        result = forget_memory(db, memory_id=item.id, user_id=1)

        assert result.derived_refs_affected == 2

    def test_forget_nonexistent_memory(self, db: Session):
        result = forget_memory(db, memory_id=9999, user_id=1)
        assert result.items_forgotten == 0

    def test_forget_wrong_user(self, db: Session):
        item = _make_item(db, user_id=2, content="other user's memory")
        result = forget_memory(db, memory_id=item.id, user_id=1)
        assert result.items_forgotten == 0


# ── T5: forget_by_topic ───────────────────────────────────────────────


class TestForgetByTopic:
    def test_finds_matching_memories(self, db: Session):
        _make_item(db, content="Alex is my friend")
        _make_item(db, content="Alex works at Google")
        _make_item(db, content="Likes pizza")

        candidates = forget_by_topic(db, topic="Alex", user_id=1)

        assert len(candidates) == 2
        contents = [c.content for c in candidates]
        assert "Likes pizza" not in contents

    def test_case_insensitive(self, db: Session):
        _make_item(db, content="ALEX is great")

        candidates = forget_by_topic(db, topic="alex", user_id=1)
        assert len(candidates) == 1

    def test_no_matches(self, db: Session):
        _make_item(db, content="Likes pizza")

        candidates = forget_by_topic(db, topic="nonexistent", user_id=1)
        assert len(candidates) == 0

    def test_excludes_superseded(self, db: Session):
        item1 = _make_item(db, content="Alex is 25")
        item2 = _make_item(db, content="Alex is 26")
        item1.superseded_by = item2.id
        db.flush()

        candidates = forget_by_topic(db, topic="Alex", user_id=1)
        # Only active (non-superseded) should match
        assert len(candidates) == 1
        assert candidates[0].id == item2.id


# ── T6: ForgetAuditLog ───────────────────────────────────────────────


class TestForgetAuditLog:
    def test_audit_log_model(self, db: Session):
        log = ForgetAuditLog(
            user_id=1,
            forgotten_at=datetime.now(UTC),
            trigger="user_request",
            scope="single",
            items_forgotten=3,
            derived_refs_affected=2,
        )
        db.add(log)
        db.flush()

        loaded = db.get(ForgetAuditLog, log.id)
        assert loaded is not None
        assert loaded.trigger == "user_request"
        assert loaded.items_forgotten == 3
        assert loaded.derived_refs_affected == 2

    def test_audit_log_topic_scope(self, db: Session):
        log = ForgetAuditLog(
            user_id=1,
            forgotten_at=datetime.now(UTC),
            trigger="topic_forget",
            scope="topic:Alex",
            items_forgotten=5,
            derived_refs_affected=1,
        )
        db.add(log)
        db.flush()
        assert log.scope == "topic:Alex"


# ── T7: find_derived_references ───────────────────────────────────────


class TestFindDerivedReferences:
    def test_finds_in_episodes(self, db: Session):
        ep = _make_episode(db, summary="The user works as a teacher and enjoys it")

        refs = find_derived_references(
            db,
            memory_content="works as a teacher",
            user_id=1,
        )
        assert len(refs.episodes) == 1
        assert refs.episodes[0].record_id == ep.id

    def test_finds_in_self_model_blocks(self, db: Session):
        block = _make_self_model_block(
            db,
            section="growth_log",
            content="User revealed they enjoy painting",
        )

        refs = find_derived_references(
            db,
            memory_content="enjoy painting",
            user_id=1,
        )
        assert len(refs.self_model_blocks) == 1
        assert refs.self_model_blocks[0].record_id == block.id
        assert refs.self_model_blocks[0].section == "growth_log"

    def test_finds_in_intentions(self, db: Session):
        _make_self_model_block(
            db,
            section="intentions",
            content="Remind user about their goal to learn guitar",
        )

        refs = find_derived_references(
            db,
            memory_content="learn guitar",
            user_id=1,
        )
        assert len(refs.self_model_blocks) == 1
        assert refs.self_model_blocks[0].section == "intentions"

    def test_no_match_returns_empty(self, db: Session):
        _make_episode(db, summary="unrelated content")

        refs = find_derived_references(
            db,
            memory_content="something else",
            user_id=1,
        )
        assert refs.total == 0

    def test_short_content_skipped(self, db: Session):
        refs = find_derived_references(
            db,
            memory_content="ab",
            user_id=1,
        )
        assert refs.total == 0


# ── T8: redact_derived_references ─────────────────────────────────────


class TestRedactDerivedReferences:
    def test_flag_for_regeneration(self, db: Session):
        ep = _make_episode(db, summary="some content")
        block = _make_self_model_block(db, content="some content")

        refs = DerivedReferences(
            episodes=[DerivedReferences.__dataclass_fields__["episodes"].default_factory()[0:0]],
            self_model_blocks=[],
        )
        # Build proper refs
        refs = find_derived_references(
            db,
            memory_content="some content",
            user_id=1,
        )

        count = redact_derived_references(
            db,
            refs=refs,
            strategy="flag_for_regeneration",
        )

        assert count == 2
        db.refresh(ep)
        db.refresh(block)
        assert ep.needs_regeneration is True
        assert block.needs_regeneration is True

    def test_immediate_redact(self, db: Session):
        block = _make_self_model_block(
            db,
            content="sensitive data here",
        )

        refs = find_derived_references(
            db,
            memory_content="sensitive data here",
            user_id=1,
        )

        redact_derived_references(
            db,
            refs=refs,
            strategy="immediate_redact",
        )

        db.refresh(block)
        assert block.content == "[redacted]"
