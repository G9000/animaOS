"""Tests for F7 — Intentional Forgetting."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from anima_server.config import settings
from anima_server.db.base import Base
from anima_server.models import (
    ForesightSignal,
    ForgetAuditLog,
    LatentTrace,
    MemoryClaim,
    MemoryClaimEvidence,
    MemoryEpisode,
    MemoryItem,
    MemoryItemEvidence,
    SoulOwner,
)
from anima_server.models.consciousness import SelfModelBlock
from anima_server.services import anima_core_retrieval as retrieval_module
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
from anima_server.services.agent.provenance import add_memory_item_evidence
from anima_server.services.data_crypto import df
from anima_server.services.regeneration_work import regeneration_work_ids
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def test_regeneration_queue_writes_without_posix_fchmod(monkeypatch):
    from anima_server.services import regeneration_work as work

    monkeypatch.delattr(work.os, "fchmod", raising=False)
    work.mark_regeneration_work({(1, "memory_episode", 7), (2, "self_model_block", 9)})
    assert work.regeneration_work_ids(user_id=1, kind="memory_episode") == frozenset({7})
    assert work.regeneration_work_ids(user_id=1, kind="self_model_block") == frozenset()
    assert not tuple(work._path().parent.glob("*.tmp"))


@pytest.mark.parametrize("failure_boundary", ["fchmod", "chmod", "fdopen", "replace"])
def test_regeneration_permission_failure_closes_temp_and_preserves_queue(
    monkeypatch, failure_boundary
):
    import errno

    from anima_server.services import regeneration_work as work

    path = work._path()
    path.parent.mkdir(parents=True)
    original = b'{"version":1,"items":[{"userId":1,"kind":"memory_episode","recordId":3}]}\n'
    path.write_bytes(original)
    created = []
    original_mkstemp = work.tempfile.mkstemp

    def capture_temp(*args, **kwargs):
        result = original_mkstemp(*args, **kwargs)
        created.append(result)
        return result

    def reject_permissions(*_args, **_kwargs):
        raise PermissionError("injected private-file permission failure")

    monkeypatch.setattr(work.tempfile, "mkstemp", capture_temp)
    if failure_boundary == "chmod":
        monkeypatch.delattr(work.os, "fchmod", raising=False)
        monkeypatch.setattr(work.os, "chmod", reject_permissions)
    else:
        monkeypatch.setattr(work.os, failure_boundary, reject_permissions, raising=False)
    try:
        with pytest.raises(PermissionError, match="injected private-file permission failure"):
            work.mark_regeneration_work({(1, "memory_episode", 7)})
        assert path.read_bytes() == original
        assert created
        with pytest.raises(OSError) as closed:
            work.os.fstat(created[0][0])
        assert closed.value.errno == errno.EBADF
        assert not tuple(path.parent.glob("*.tmp"))
    finally:
        for descriptor, name in created:
            with suppress(OSError):
                work.os.close(descriptor)
            Path(name).unlink(missing_ok=True)


_FAKE_DEK = b"0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def _runtime_work_root(managed_tmp_path) -> Generator[None, None, None]:  # type: ignore[no-untyped-def]
    previous = settings.runtime_instance_data_dir
    settings.runtime_instance_data_dir = str(managed_tmp_path / "runtime-instance")
    try:
        yield
    finally:
        settings.runtime_instance_data_dir = previous


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
        assert ep.needs_regeneration is False
        assert block.needs_regeneration is False
        assert ep.id in regeneration_work_ids(user_id=1, kind="memory_episode")
        assert block.id in regeneration_work_ids(user_id=1, kind="self_model_block")

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

    def test_forget_deletes_foresight_signals_from_source_turn(self, db: Session):
        item = _make_item(db, content="I have a dentist appointment tomorrow")
        db.add(
            MemoryItemEvidence(
                user_id=1,
                memory_item_id=item.id,
                source_kind="conversation",
                evidence_text="I have a dentist appointment tomorrow",
                source_message_uris=[URI_A, URI_B],
            )
        )
        overlapping = ForesightSignal(
            user_id=1,
            content="User has a dentist appointment tomorrow",
            evidence="I have a dentist appointment tomorrow",
            source_message_uris=[URI_B],
        )
        unrelated = ForesightSignal(
            user_id=1,
            content="User has a product review next week",
            evidence="I have a product review next week",
            source_message_uris=[URI_A],
        )
        other_user = ForesightSignal(
            user_id=2,
            content="Other user has a dentist appointment tomorrow",
            evidence="I have a dentist appointment tomorrow",
            source_message_uris=[URI_B],
        )
        db.add_all([overlapping, unrelated, other_user])
        db.flush()

        forget_memory(db, memory_id=item.id, user_id=1)

        assert db.get(ForesightSignal, overlapping.id) is None
        assert db.get(ForesightSignal, unrelated.id) is not None
        assert db.get(ForesightSignal, other_user.id) is not None

    def test_forget_preserves_unrelated_foresight_from_same_source_turn(self, db: Session):
        item = _make_item(db, content="User likes sushi")
        db.add(
            MemoryItemEvidence(
                user_id=1,
                memory_item_id=item.id,
                source_kind="conversation",
                evidence_text=("I like sushi and I have a dentist appointment tomorrow"),
                source_message_uris=[URI_B],
            )
        )
        unrelated_foresight = ForesightSignal(
            user_id=1,
            content="User has a dentist appointment tomorrow",
            evidence="I have a dentist appointment tomorrow",
            source_message_uris=[URI_B],
        )
        db.add(unrelated_foresight)
        db.flush()

        forget_memory(db, memory_id=item.id, user_id=1)

        assert db.get(ForesightSignal, unrelated_foresight.id) is not None

    def test_forget_defers_retrieval_index_cleanup_until_after_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        db: Session,
    ):
        deletes: list[dict[str, object]] = []
        monkeypatch.setattr(
            retrieval_module,
            "memory_index_delete",
            lambda **kwargs: deletes.append(kwargs) or True,
        )
        item = _make_item(db, content="secret fact")
        item_id = item.id

        forget_memory(db, memory_id=item_id, user_id=1)

        assert deletes == []
        db.commit()
        assert deletes == [
            {
                "root": retrieval_module.get_retrieval_root(),
                "record_id": item_id,
                "user_id": 1,
            }
        ]

    def test_forget_drops_deferred_retrieval_cleanup_on_rollback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        db: Session,
    ):
        deletes: list[dict[str, object]] = []
        monkeypatch.setattr(
            retrieval_module,
            "memory_index_delete",
            lambda **kwargs: deletes.append(kwargs) or True,
        )
        item = _make_item(db, content="secret fact")

        forget_memory(db, memory_id=item.id, user_id=1)

        assert deletes == []
        db.rollback()
        db.commit()
        assert deletes == []

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

    def test_forget_removes_pattern_memory_citing_stale_episode(
        self,
        monkeypatch: pytest.MonkeyPatch,
        db: Session,
    ):
        deletes: list[dict[str, object]] = []
        monkeypatch.setattr(
            retrieval_module,
            "memory_index_delete",
            lambda **kwargs: deletes.append(kwargs) or True,
        )
        item = _make_item(db, content="secret codename aurora")
        episode = _make_episode(
            db,
            summary="User discussed secret codename aurora during launch planning.",
        )
        pattern = MemoryItem(
            user_id=1,
            category="pattern",
            source="pattern_synthesis",
            content="Aurora launch planning repeatedly creates pressure.",
            importance=4,
            evidence_strength=0.86,
        )
        db.add(pattern)
        db.flush()
        db.add(
            MemoryItemEvidence(
                user_id=1,
                memory_item_id=pattern.id,
                source_kind="pattern_synthesis",
                evidence_text="launch pressure appeared in the source episode",
                metadata_json={
                    "memory_source": "pattern_synthesis",
                    "source_episode_ids": [episode.id],
                },
            )
        )
        db.flush()
        pattern_id = pattern.id

        # IL7 (PR #116 P1): a dream that sampled the DERIVED pattern item must
        # be scrubbed on forget — dreams can sample pattern rows, and the
        # narrative is derived from the now-forgotten content.
        from anima_server.models import DreamJournal

        db.add(
            DreamJournal(
                user_id=1,
                dreamt_at=datetime(2026, 1, 2, tzinfo=UTC),
                narrative="a dream about the aurora launch pressure",
                source_refs={"memory_item_ids": [pattern_id], "latent_topic_keys": []},
                affect_delta={},
                share_worthy=True,
            )
        )
        db.flush()

        result = forget_memory(db, memory_id=item.id, user_id=1)

        assert deletes == []
        assert result.derived_refs_affected == 2
        db.refresh(episode)
        assert episode.needs_regeneration is False
        assert episode.id in regeneration_work_ids(user_id=1, kind="memory_episode")
        assert db.get(MemoryItem, pattern_id) is None
        # The dream built on the derived pattern item is scrubbed.
        assert result.dream_journal_scrubbed == 1
        assert db.scalars(select(DreamJournal)).all() == []
        db.commit()
        deleted_record_ids = {int(call["record_id"]) for call in deletes}
        assert deleted_record_ids == {item.id, pattern_id}

    def test_forget_matches_encrypted_pattern_evidence_text(
        self,
        monkeypatch: pytest.MonkeyPatch,
        db: Session,
    ):
        monkeypatch.setattr(
            "anima_server.services.data_crypto.get_active_dek",
            lambda user_id, domain="memories": _FAKE_DEK,
        )
        item = _make_item(db, content="secret codename aurora")
        pattern = MemoryItem(
            user_id=1,
            category="pattern",
            source="pattern_synthesis",
            content="Launch planning repeatedly creates pressure.",
            importance=4,
            evidence_strength=0.86,
        )
        db.add(pattern)
        db.flush()
        evidence = add_memory_item_evidence(
            db,
            user_id=1,
            memory_item_id=pattern.id,
            source_kind="pattern_synthesis",
            evidence_text="Source evidence mentioned secret codename aurora once.",
            metadata={"memory_source": "pattern_synthesis"},
        )
        assert evidence is not None
        assert evidence.evidence_text != "Source evidence mentioned secret codename aurora once."
        assert (
            df(
                1,
                evidence.evidence_text,
                table="memory_item_evidence",
                field="evidence_text",
            )
            == "Source evidence mentioned secret codename aurora once."
        )
        pattern_id = pattern.id
        evidence_id = evidence.id

        result = forget_memory(db, memory_id=item.id, user_id=1)

        assert result.derived_refs_affected == 1
        assert db.get(MemoryItem, pattern_id) is None
        remaining_evidence = db.scalar(
            select(func.count())
            .select_from(MemoryItemEvidence)
            .where(MemoryItemEvidence.id == evidence_id)
        )
        assert remaining_evidence == 0

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
        assert ep.needs_regeneration is False
        assert block.needs_regeneration is False
        assert ep.id in regeneration_work_ids(user_id=1, kind="memory_episode")
        assert block.id in regeneration_work_ids(user_id=1, kind="self_model_block")

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


URI_A = "corefs://object/00000000000000000000000000#message=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
URI_B = "corefs://object/00000000000000000000000000#message=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


@pytest.mark.parametrize("cleanup_kind", ["foresight", "latent"])
@pytest.mark.parametrize(
    "memory_uri,target_uri,memory_historical_id,target_historical_id,should_remove",
    [
        (URI_A, URI_A, 501, 601, True),
        (URI_A, URI_B, 501, 501, False),
        (None, URI_A, 501, 501, False),
        (URI_A, None, 501, 501, False),
    ],
    ids=[
        "same-native-source-new-runtime-id",
        "different-native-source-reused-runtime-id",
        "unknown-memory-provenance",
        "unknown-target-provenance",
    ],
)
def test_forget_source_cleanup_uses_core_uri_and_preserves_unknown_history(
    monkeypatch,
    managed_tmp_path,
    cleanup_kind,
    memory_uri,
    target_uri,
    memory_historical_id,
    target_historical_id,
    should_remove,
):
    from anima_server.config import settings

    monkeypatch.setattr(settings, "core_require_encryption", False)
    monkeypatch.setattr(settings, "runtime_instance_data_dir", str(managed_tmp_path / "runtime"))
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as db:
            db.add(SoulOwner(id=1, core_owner_id=str(uuid4())))
            item = MemoryItem(
                user_id=1,
                content="Complete the private launch checklist Friday",
                category="fact",
                source="test",
            )
            db.add(item)
            db.flush()
            db.add(
                MemoryItemEvidence(
                    user_id=1,
                    memory_item_id=item.id,
                    source_kind="llm",
                    evidence_text="Launch checklist deadline",
                    source_message_uri=memory_uri,
                    provenance_metadata={
                        "historicalSourceIds": {"runtime_message_id": memory_historical_id},
                        "provenanceState": "authenticated" if memory_uri else "source_unavailable",
                    },
                )
            )
            signal = ForesightSignal(
                user_id=1,
                content="Complete the private launch checklist Friday",
                evidence="Launch checklist deadline",
                source_message_uris=[target_uri] if target_uri else None,
                provenance_metadata={
                    "historicalSourceIds": {"source_message_ids_json": [target_historical_id]},
                    "provenanceState": "authenticated" if target_uri else "source_unavailable",
                },
            )
            original_ref = {
                "candidate_id": 91,
                "content_hash": "historical-candidate-digest",
                "source_message_ids": [target_historical_id],
                "source_message_uris": [target_uri] if target_uri else [],
                "provenanceState": "authenticated" if target_uri else "source_unavailable",
                "opaqueHistoricalMetadata": {"preserve": "original evidence"},
            }
            trace = LatentTrace(
                user_id=1,
                topic_key="unrelated-native-source-topic",
                weight=0.5,
                evidence_refs=[original_ref],
            )
            selected = signal if cleanup_kind == "foresight" else trace
            db.add(selected)
            db.flush()
            item_id, selected_id = item.id, selected.id
            result = forget_memory(db, memory_id=item_id, user_id=1)
            db.commit()
            assert result.items_forgotten == 1
            assert (db.get(type(selected), selected_id) is None) is should_remove
            if not should_remove and cleanup_kind == "latent":
                assert db.get(LatentTrace, selected_id).evidence_refs == [original_ref]
    finally:
        engine.dispose()


@pytest.mark.parametrize("commit_root", [False, True])
def test_profile_candidate_cleanup_waits_for_root_commit_and_discards_root_rollback(
    monkeypatch, commit_root
):
    from anima_server.services.agent import forgetting

    root_engine = create_engine("sqlite://")
    runtime_engine = create_engine("sqlite://")
    called = []
    monkeypatch.setattr(
        forgetting,
        "_reject_profile_candidates_for_forget_contexts",
        lambda *_args, **_kwargs: called.append("candidate_rejected") or 0,
    )
    try:
        with Session(root_engine) as db:
            outer = db.begin()
            savepoint = db.begin_nested()
            forgetting._schedule_profile_candidate_rejection_after_commit(
                db,
                user_id=1,
                source_contexts=[
                    forgetting.SourceProfileForgetContext((URI_A,), "fact", "Private fact")
                ],
                runtime_db_factory=sessionmaker(runtime_engine),
            )
            savepoint.commit()
            assert called == []
            if commit_root:
                outer.commit()
                assert called == ["candidate_rejected"]
            else:
                outer.rollback()
                assert called == []
    finally:
        root_engine.dispose()
        runtime_engine.dispose()


def test_latent_writer_records_owned_current_uri_and_preserves_unavailable_history():
    from types import SimpleNamespace

    from anima_server.services.agent.candidate_ops import create_memory_candidate
    from anima_server.services.agent.latent_traces import evidence_ref_for_candidate
    from test_user_profile import _bind_current_profile_source, _current_profile_source_core

    with (
        _current_profile_source_core() as (
            owner_id,
            _soul_factory,
            runtime_factory,
            view,
            first,
            _second,
        ),
        runtime_factory() as runtime_db,
    ):
        row = _bind_current_profile_source(
            runtime_db, user_id=owner_id, view=view, message=first, runtime_id=501
        )
        candidate = create_memory_candidate(
            runtime_db,
            user_id=owner_id,
            content="Current source evidence for trace",
            category="minor_observation",
            source_message_ids=[row.id],
        )
        assert candidate is not None
        evidence = evidence_ref_for_candidate(candidate, user_id=owner_id)
        assert evidence["source_message_uris"] == [
            f"corefs://object/{view.document.thread_id}#message={first.message_id}"
        ]
        assert evidence["provenanceState"] == "authenticated"
        assert evidence["source_message_ids"] == [501]
        absent = create_memory_candidate(
            runtime_db,
            user_id=owner_id,
            content="Unavailable source trace",
            category="minor_observation",
            source_message_ids=[999],
        )
        missing = evidence_ref_for_candidate(absent, user_id=owner_id)
        assert missing["source_message_uris"] == []
        assert missing["source_message_ids"] == [999]
        assert missing["provenanceState"] == "source_unavailable"
        foreign = SimpleNamespace(
            user_id=owner_id + 1, source_message_ids=[501], content_hash="foreign-candidate"
        )
        mismatched = evidence_ref_for_candidate(foreign, user_id=owner_id)
        assert mismatched["source_message_uris"] == []
        assert mismatched["source_message_ids"] == [501]
        assert mismatched["provenanceState"] == "source_unavailable"
        runtime_db.rollback()


def test_profile_candidate_cleanup_discards_savepoint_rollback_before_root_commit(monkeypatch):
    from anima_server.services.agent import forgetting

    root_engine = create_engine("sqlite://")
    runtime_engine = create_engine("sqlite://")
    called = []
    monkeypatch.setattr(
        forgetting,
        "_reject_profile_candidates_for_forget_contexts",
        lambda *_args, **_kwargs: called.append("candidate_rejected") or 0,
    )
    try:
        with Session(root_engine) as db:
            outer = db.begin()
            savepoint = db.begin_nested()
            forgetting._schedule_profile_candidate_rejection_after_commit(
                db,
                user_id=1,
                source_contexts=[
                    forgetting.SourceProfileForgetContext((URI_A,), "fact", "Private fact")
                ],
                runtime_db_factory=sessionmaker(runtime_engine),
            )
            savepoint.rollback()
            assert called == []
            outer.commit()
            assert called == []
    finally:
        root_engine.dispose()
        runtime_engine.dispose()
