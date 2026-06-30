from __future__ import annotations

import importlib
import importlib.util
import json
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime

import anima_server.models as models
import pytest
from anima_server.db.base import Base
from anima_server.db.runtime_base import RuntimeBase
from anima_server.models import MemoryClaimEvidence, User
from anima_server.services.agent.claims import upsert_claim
from anima_server.services.data_crypto import df, ef
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@contextmanager
def _db_session() -> Generator[Session, None, None]:
    engine: Engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=Session,
    )
    Base.metadata.create_all(bind=engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _make_user(db: Session) -> User:
    user = User(username="profile-user", display_name="Profile User", password_hash="x")
    db.add(user)
    db.flush()
    return user


@contextmanager
def _soul_session_factory() -> Generator[sessionmaker[Session], None, None]:
    engine: Engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=Session,
    )
    Base.metadata.create_all(bind=engine)
    try:
        yield factory
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@contextmanager
def _runtime_session_factory() -> Generator[sessionmaker[Session], None, None]:
    engine: Engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=Session,
    )
    RuntimeBase.metadata.create_all(bind=engine)
    try:
        yield factory
    finally:
        RuntimeBase.metadata.drop_all(bind=engine)
        engine.dispose()


def _profile_service():
    spec = importlib.util.find_spec("anima_server.services.agent.user_profile")
    assert spec is not None
    return importlib.import_module("anima_server.services.agent.user_profile")


def test_user_profile_models_are_registered() -> None:
    assert hasattr(models, "UserProfileField")
    assert hasattr(models, "UserProfileFieldEvidence")


def test_upsert_profile_field_supersedes_and_preserves_evidence() -> None:
    service = _profile_service()
    with _db_session() as db:
        user = _make_user(db)
        observed = datetime(2026, 6, 30, 10, 0, tzinfo=UTC)

        created = service.upsert_profile_field(
            db,
            user_id=user.id,
            category="work",
            key="role",
            value="Solo founder building AnimaOS",
            confidence=0.74,
            evidence_text="I am a solo founder building AnimaOS",
            source_kind="llm_extraction",
            observed_at=observed,
        )
        repeated = service.upsert_profile_field(
            db,
            user_id=user.id,
            category="work",
            key="role",
            value="Solo founder building AnimaOS",
            confidence=0.82,
            evidence_text="Still solo founder on AnimaOS",
            source_kind="llm_extraction",
            observed_at=observed,
        )
        corrected = service.upsert_profile_field(
            db,
            user_id=user.id,
            category="work",
            key="role",
            value="Founder and engineer building AnimaOS",
            confidence=1.0,
            evidence_text="User corrected the role wording",
            source_kind="user_correction",
            observed_at=observed,
        )
        db.flush()
        db.refresh(created)

        active = service.list_profile_fields(db, user_id=user.id)
        history = service.list_profile_fields(db, user_id=user.id, include_history=True)
        created_status = created.status
        created_superseded_by_id = created.superseded_by_id
        corrected_value = df(
            user.id,
            corrected.value_text,
            table="user_profile_fields",
            field="value_text",
        )
        created_evidence_count = len(created.evidence)
        corrected_evidence_count = len(corrected.evidence)
        corrected_confidence = corrected.confidence

    assert repeated.id == created.id
    assert corrected.id != created.id
    assert created_status == "superseded"
    assert created_superseded_by_id == corrected.id
    assert len(active) == 1
    assert len(history) == 2
    assert corrected_value == "Founder and engineer building AnimaOS"
    assert created_evidence_count == 2
    assert corrected_evidence_count == 1
    assert corrected_confidence == 1.0


def test_upsert_profile_field_preserves_user_correction_from_auto_update() -> None:
    service = _profile_service()
    with _db_session() as db:
        user = _make_user(db)
        extracted = service.upsert_profile_field(
            db,
            user_id=user.id,
            category="work",
            key="role",
            value="Designer",
            confidence=0.7,
            evidence_text="I work as a designer",
            source_kind="profile_llm",
        )
        corrected = service.upsert_profile_field(
            db,
            user_id=user.id,
            category="work",
            key="role",
            value="Engineer",
            confidence=1.0,
            evidence_text="Actually, I work as an engineer",
            source_kind="user_correction",
        )

        stale = service.upsert_profile_field(
            db,
            user_id=user.id,
            category="work",
            key="role",
            value="Designer",
            confidence=0.9,
            evidence_text="older extraction said designer",
            source_kind="profile_llm",
        )
        active = service.list_profile_fields(db, user_id=user.id)
        history = service.list_profile_fields(db, user_id=user.id, include_history=True)
        active_value = df(
            user.id,
            active[0].value_text,
            table="user_profile_fields",
            field="value_text",
        )
        corrected_evidence_count = len(corrected.evidence)

    assert stale.id == corrected.id
    assert extracted.status == "superseded"
    assert active_value == "Engineer"
    assert [(field.id, field.status) for field in history] == [
        (corrected.id, "active"),
        (extracted.id, "superseded"),
    ]
    assert corrected_evidence_count == 1


def test_upsert_profile_field_canonicalizes_key_case() -> None:
    service = _profile_service()
    with _db_session() as db:
        user = _make_user(db)
        extracted = service.upsert_profile_field(
            db,
            user_id=user.id,
            category="work",
            key="Role",
            value="Designer",
            confidence=0.7,
            evidence_text="I work as a designer",
            source_kind="profile_llm",
        )
        updated = service.upsert_profile_field(
            db,
            user_id=user.id,
            category="work",
            key="role",
            value="Engineer",
            confidence=0.9,
            evidence_text="I work as an engineer",
            source_kind="profile_llm",
        )
        active = service.list_profile_fields(db, user_id=user.id)
        history = service.list_profile_fields(db, user_id=user.id, include_history=True)
        active_value = df(
            user.id,
            active[0].value_text,
            table="user_profile_fields",
            field="value_text",
        )

    assert updated.id != extracted.id
    assert extracted.status == "superseded"
    assert len(active) == 1
    assert [(field.key, field.status) for field in history] == [
        ("role", "active"),
        ("role", "superseded"),
    ]
    assert active_value == "Engineer"


def test_upsert_profile_field_marks_same_value_user_correction() -> None:
    service = _profile_service()
    with _db_session() as db:
        user = _make_user(db)
        extracted = service.upsert_profile_field(
            db,
            user_id=user.id,
            category="work",
            key="role",
            value="Engineer",
            confidence=0.7,
            evidence_text="I work as an engineer",
            source_kind="profile_llm",
        )
        corrected = service.upsert_profile_field(
            db,
            user_id=user.id,
            category="work",
            key="role",
            value="Engineer",
            confidence=1.0,
            evidence_text="Yes, engineer is right",
            source_kind="user_correction",
        )
        stale = service.upsert_profile_field(
            db,
            user_id=user.id,
            category="work",
            key="role",
            value="Designer",
            confidence=0.9,
            evidence_text="older extraction said designer",
            source_kind="profile_llm",
        )
        active = service.list_profile_fields(db, user_id=user.id)
        active_value = df(
            user.id,
            active[0].value_text,
            table="user_profile_fields",
            field="value_text",
        )
        evidence_count = len(extracted.evidence)

    assert corrected.id == extracted.id
    assert stale.id == extracted.id
    assert extracted.source_kind == "user_correction"
    assert active_value == "Engineer"
    assert evidence_count == 2


def test_retracted_profile_field_blocks_automatic_recreation() -> None:
    service = _profile_service()
    with _db_session() as db:
        user = _make_user(db)
        field = service.upsert_profile_field(
            db,
            user_id=user.id,
            category="work",
            key="role",
            value="Designer",
            evidence_text="I work as a designer",
            source_kind="profile_llm",
        )

        retracted = service.retract_profile_field(
            db,
            user_id=user.id,
            field_id=field.id,
        )
        recreated = service.upsert_profile_field(
            db,
            user_id=user.id,
            category="work",
            key="role",
            value="Designer",
            evidence_text="older automatic extraction repeated designer",
            source_kind="profile_llm",
        )
        active = service.list_profile_fields(db, user_id=user.id)
        history = service.list_profile_fields(db, user_id=user.id, include_history=True)

    assert retracted.status == "retracted"
    assert recreated.id == field.id
    assert recreated.status == "retracted"
    assert active == []
    assert [profile_field.id for profile_field in history] == [field.id]


def test_render_profile_prompt_block_is_grouped_and_deterministic() -> None:
    service = _profile_service()
    with _db_session() as db:
        user = _make_user(db)
        service.upsert_profile_field(
            db,
            user_id=user.id,
            category="preferences",
            key="communication",
            value="Prefers direct, concise answers",
            evidence_text="keep it direct",
        )
        service.upsert_profile_field(
            db,
            user_id=user.id,
            category="identity",
            key="name",
            value="Leo",
            evidence_text="my name is Leo",
        )
        service.upsert_profile_field(
            db,
            user_id=user.id,
            category="work",
            key="project",
            value="Building AnimaOS",
            evidence_text="I am building AnimaOS",
        )

        rendered = service.render_profile_prompt_block(db, user_id=user.id)

    assert rendered.splitlines() == [
        "Identity:",
        "- name: Leo",
        "Work:",
        "- project: Building AnimaOS",
        "Preferences:",
        "- communication: Prefers direct, concise answers",
    ]
    assert "evidence" not in rendered.lower()


def test_reconcile_profile_from_claims_maps_active_claims_to_profile_fields() -> None:
    service = _profile_service()
    with _db_session() as db:
        user = _make_user(db)
        work_claim = upsert_claim(
            db,
            user_id=user.id,
            content="works as a product manager",
            category="fact",
            evidence_text="I work as a product manager",
        )
        upsert_claim(
            db,
            user_id=user.id,
            content="likes concise answers",
            category="preference",
            evidence_text="I like concise answers",
        )

        reconciled = service.reconcile_profile_from_claims(db, user_id=user.id)
        active = service.list_profile_fields(db, user_id=user.id)
        profile = {(field.category, field.key): field for field in active}
        work_evidence_count = len(profile[("work", "occupation")].evidence)
        has_claim_evidence = (
            db.scalar(select(MemoryClaimEvidence).where(MemoryClaimEvidence.claim_id == work_claim.id))
            is not None
        )

    assert reconciled == 2
    assert ("work", "occupation") in profile
    assert ("preferences", "likes") in profile
    assert work_evidence_count == 1
    assert has_claim_evidence


def test_reconcile_profile_from_claims_skips_unmapped_fact_claims() -> None:
    service = _profile_service()
    with _db_session() as db:
        user = _make_user(db)
        upsert_claim(
            db,
            user_id=user.id,
            content="visited Kyoto in 2024",
            category="fact",
            evidence_text="I visited Kyoto in 2024",
        )

        reconciled = service.reconcile_profile_from_claims(db, user_id=user.id)
        active = service.list_profile_fields(db, user_id=user.id)

    assert reconciled == 0
    assert active == []


def test_reconcile_profile_from_claims_is_idempotent_for_same_claim() -> None:
    service = _profile_service()
    with _db_session() as db:
        user = _make_user(db)
        upsert_claim(
            db,
            user_id=user.id,
            content="works as a product manager",
            category="fact",
            evidence_text="I work as a product manager",
        )

        first_count = service.reconcile_profile_from_claims(db, user_id=user.id)
        second_count = service.reconcile_profile_from_claims(db, user_id=user.id)
        active = service.list_profile_fields(db, user_id=user.id)
        evidence_count = len(active[0].evidence)

    assert first_count == 1
    assert second_count == 0
    assert evidence_count == 1


def test_reconcile_profile_from_claims_does_not_count_user_correction_skip() -> None:
    service = _profile_service()
    with _db_session() as db:
        user = _make_user(db)
        service.upsert_profile_field(
            db,
            user_id=user.id,
            category="work",
            key="occupation",
            value="Designer",
            evidence_text="I work as a designer",
            source_kind="profile_llm",
        )
        corrected = service.upsert_profile_field(
            db,
            user_id=user.id,
            category="work",
            key="occupation",
            value="Engineer",
            evidence_text="Actually, I work as an engineer",
            source_kind="user_correction",
        )
        upsert_claim(
            db,
            user_id=user.id,
            content="works as a designer",
            category="fact",
            evidence_text="older claim said designer",
        )

        first_count = service.reconcile_profile_from_claims(db, user_id=user.id)
        second_count = service.reconcile_profile_from_claims(db, user_id=user.id)
        active = service.list_profile_fields(db, user_id=user.id)
        evidence_count = len(active[0].evidence)

    assert first_count == 0
    assert second_count == 0
    assert [profile_field.id for profile_field in active] == [corrected.id]
    assert evidence_count == 1


def test_reconcile_profile_from_claims_preserves_newer_profile_llm_update() -> None:
    service = _profile_service()
    with _db_session() as db:
        user = _make_user(db)
        older_claim = upsert_claim(
            db,
            user_id=user.id,
            content="works as a product manager",
            category="fact",
            evidence_text="Earlier memory said product manager",
        )
        assert older_claim is not None
        older_observed = datetime(2026, 1, 1, tzinfo=UTC)
        newer_observed = datetime(2026, 2, 1, tzinfo=UTC)
        older_claim.updated_at = older_observed
        for evidence in older_claim.evidence:
            evidence.created_at = older_observed
        promoted = service.upsert_profile_field(
            db,
            user_id=user.id,
            category="work",
            key="occupation",
            value="Systems designer",
            evidence_text="Latest profile extraction said systems designer",
            source_kind="profile_llm",
            observed_at=newer_observed,
        )

        reconciled = service.reconcile_profile_from_claims(db, user_id=user.id)
        active = service.list_profile_fields(db, user_id=user.id)
        evidence_count = len(active[0].evidence)

    assert reconciled == 0
    assert [profile_field.id for profile_field in active] == [promoted.id]
    assert active[0].source_kind == "profile_llm"
    assert evidence_count == 1


def test_reconcile_profile_from_claims_is_idempotent_for_sourceless_claim() -> None:
    service = _profile_service()
    with _db_session() as db:
        user = _make_user(db)
        upsert_claim(
            db,
            user_id=user.id,
            content="works as a product manager",
            category="fact",
            evidence_text=None,
        )

        first_count = service.reconcile_profile_from_claims(db, user_id=user.id)
        second_count = service.reconcile_profile_from_claims(db, user_id=user.id)
        active = service.list_profile_fields(db, user_id=user.id)
        evidence_count = len(active[0].evidence)

    assert first_count == 1
    assert second_count == 0
    assert evidence_count == 1


def test_reconcile_profile_from_claims_scopes_sourceless_memory_to_field() -> None:
    service = _profile_service()
    from anima_server.models import MemoryItem

    with _db_session() as db:
        user = _make_user(db)
        item = MemoryItem(
            user_id=user.id,
            content=ef(
                user.id,
                "works as a product manager and likes concise answers",
                table="memory_items",
                field="content",
            ),
            category="fact",
            importance=3,
            source="test",
        )
        db.add(item)
        db.flush()
        upsert_claim(
            db,
            user_id=user.id,
            content="works as a product manager",
            category="fact",
            memory_item_id=item.id,
            evidence_text=None,
        )
        upsert_claim(
            db,
            user_id=user.id,
            content="likes concise answers",
            category="preference",
            memory_item_id=item.id,
            evidence_text=None,
        )

        reconciled = service.reconcile_profile_from_claims(db, user_id=user.id)
        second_count = service.reconcile_profile_from_claims(db, user_id=user.id)
        active = service.list_profile_fields(db, user_id=user.id)
        profile = {(field.category, field.key): field for field in active}
        evidence_counts = {
            profile_key: len(field.evidence) for profile_key, field in profile.items()
        }

    assert reconciled == 2
    assert second_count == 0
    assert set(profile) == {("work", "occupation"), ("preferences", "likes")}
    assert evidence_counts == {
        ("work", "occupation"): 1,
        ("preferences", "likes"): 1,
    }


def test_reconcile_profile_from_claims_tracks_claim_evidence_separately() -> None:
    service = _profile_service()
    with _db_session() as db:
        user = _make_user(db)
        claim = upsert_claim(
            db,
            user_id=user.id,
            content="works as a product manager",
            category="fact",
            evidence_text="I work as a product manager",
        )
        assert claim is not None
        claim_evidence = db.scalar(
            select(MemoryClaimEvidence).where(MemoryClaimEvidence.claim_id == claim.id)
        )
        assert claim_evidence is not None

        reconciled = service.reconcile_profile_from_claims(db, user_id=user.id)
        field = service.list_profile_fields(db, user_id=user.id)[0]
        evidence = field.evidence[0]

    assert reconciled == 1
    assert field.source_evidence_id is None
    assert field.source_claim_evidence_id == claim_evidence.id
    assert evidence.source_evidence_id is None
    assert evidence.source_claim_evidence_id == claim_evidence.id


def test_forget_memory_deletes_profile_fields_sourced_from_claim_chain() -> None:
    service = _profile_service()
    from anima_server.models import MemoryItem, UserProfileField, UserProfileFieldEvidence
    from anima_server.services.agent.forgetting import forget_memory

    with _db_session() as db:
        user = _make_user(db)
        item = MemoryItem(
            user_id=user.id,
            content=ef(
                user.id,
                "works as a product manager",
                table="memory_items",
                field="content",
            ),
            category="fact",
            importance=3,
            source="test",
        )
        db.add(item)
        db.flush()
        upsert_claim(
            db,
            user_id=user.id,
            content="works as a product manager",
            category="fact",
            memory_item_id=item.id,
            evidence_text="I work as a product manager",
        )
        service.reconcile_profile_from_claims(db, user_id=user.id)
        field = service.list_profile_fields(db, user_id=user.id)[0]
        field_id = field.id
        evidence_id = field.evidence[0].id

        result = forget_memory(db, memory_id=item.id, user_id=user.id)
        active_after_forget = service.list_profile_fields(db, user_id=user.id)
        history_after_forget = service.list_profile_fields(
            db,
            user_id=user.id,
            include_history=True,
        )
        forgotten_field = db.get(UserProfileField, field_id)
        forgotten_evidence = db.get(UserProfileFieldEvidence, evidence_id)

    assert result.items_forgotten == 1
    assert active_after_forget == []
    assert history_after_forget == []
    assert forgotten_field is None
    assert forgotten_evidence is None


def test_forget_memory_deletes_profile_evidence_without_fk_cascade() -> None:
    service = _profile_service()
    from anima_server.models import (
        MemoryItem,
        MemoryItemEvidence,
        UserProfileField,
        UserProfileFieldEvidence,
    )
    from anima_server.services.agent.forgetting import forget_memory

    with _db_session() as db:
        user = _make_user(db)
        item = MemoryItem(
            user_id=user.id,
            content=ef(
                user.id,
                "works as a systems designer",
                table="memory_items",
                field="content",
            ),
            category="fact",
            importance=3,
            source="test",
        )
        db.add(item)
        db.flush()
        source_evidence = MemoryItemEvidence(
            user_id=user.id,
            memory_item_id=item.id,
            source_kind="explicit_save",
            evidence_text=ef(
                user.id,
                "works as a systems designer",
                table="memory_item_evidence",
                field="evidence_text",
            ),
        )
        db.add(source_evidence)
        db.flush()
        field = service.upsert_profile_field(
            db,
            user_id=user.id,
            category="work",
            key="occupation",
            value="systems designer",
            evidence_text="works as a systems designer",
            source_kind="explicit_save",
            source_memory_id=item.id,
            source_evidence_id=source_evidence.id,
        )
        field_id = field.id
        profile_evidence_id = db.scalar(
            select(UserProfileFieldEvidence.id).where(
                UserProfileFieldEvidence.profile_field_id == field_id,
            )
        )
        assert profile_evidence_id is not None
        db.expire(field, ["evidence"])

        result = forget_memory(db, memory_id=item.id, user_id=user.id)
        forgotten_field = db.get(UserProfileField, field_id)
        forgotten_profile_evidence = db.get(
            UserProfileFieldEvidence,
            profile_evidence_id,
        )

    assert result.items_forgotten == 1
    assert forgotten_field is None
    assert forgotten_profile_evidence is None


def test_forget_memory_preserves_profile_field_with_surviving_evidence() -> None:
    service = _profile_service()
    from anima_server.models import MemoryItem, MemoryItemEvidence, UserProfileFieldEvidence
    from anima_server.services.agent.forgetting import forget_memory

    with _db_session() as db:
        user = _make_user(db)
        first_observed = datetime(2026, 1, 1, tzinfo=UTC)
        second_observed = datetime(2026, 1, 5, tzinfo=UTC)
        first_item = MemoryItem(
            user_id=user.id,
            content=ef(
                user.id,
                "works as a designer",
                table="memory_items",
                field="content",
            ),
            category="fact",
            importance=3,
            source="test",
        )
        second_item = MemoryItem(
            user_id=user.id,
            content=ef(
                user.id,
                "still works as a designer",
                table="memory_items",
                field="content",
            ),
            category="fact",
            importance=3,
            source="test",
        )
        db.add_all([first_item, second_item])
        db.flush()
        first_evidence = MemoryItemEvidence(
            user_id=user.id,
            memory_item_id=first_item.id,
            source_kind="explicit_save",
            evidence_text=ef(
                user.id,
                "works as a designer",
                table="memory_item_evidence",
                field="evidence_text",
            ),
        )
        second_evidence = MemoryItemEvidence(
            user_id=user.id,
            memory_item_id=second_item.id,
            source_kind="explicit_save",
            evidence_text=ef(
                user.id,
                "still works as a designer",
                table="memory_item_evidence",
                field="evidence_text",
            ),
        )
        db.add_all([first_evidence, second_evidence])
        db.flush()
        field = service.upsert_profile_field(
            db,
            user_id=user.id,
            category="work",
            key="occupation",
            value="designer",
            evidence_text="works as a designer",
            source_kind="explicit_save",
            source_memory_id=first_item.id,
            source_evidence_id=first_evidence.id,
            observed_at=first_observed,
        )
        field = service.upsert_profile_field(
            db,
            user_id=user.id,
            category="work",
            key="occupation",
            value="designer",
            evidence_text="still works as a designer",
            source_kind="explicit_save",
            source_memory_id=second_item.id,
            source_evidence_id=second_evidence.id,
            observed_at=second_observed,
        )
        field_id = field.id
        first_profile_evidence_id = db.scalar(
            select(UserProfileFieldEvidence.id).where(
                UserProfileFieldEvidence.source_evidence_id == first_evidence.id,
            )
        )

        result = forget_memory(db, memory_id=first_item.id, user_id=user.id)
        fields = service.list_profile_fields(db, user_id=user.id, include_history=True)
        remaining_field = fields[0]
        remaining_evidence = list(remaining_field.evidence)
        removed_profile_evidence = db.get(
            UserProfileFieldEvidence,
            first_profile_evidence_id,
        )

    assert result.items_forgotten == 1
    assert [profile_field.id for profile_field in fields] == [field_id]
    assert remaining_field.status == "active"
    assert remaining_field.source_memory_id == second_item.id
    assert remaining_field.source_evidence_id == second_evidence.id
    assert remaining_field.first_observed_at is not None
    assert remaining_field.last_observed_at is not None
    assert remaining_field.first_observed_at.replace(tzinfo=UTC) == second_observed
    assert remaining_field.last_observed_at.replace(tzinfo=UTC) == second_observed
    assert len(remaining_evidence) == 1
    assert remaining_evidence[0].source_memory_id == second_item.id
    assert remaining_evidence[0].source_evidence_id == second_evidence.id
    assert removed_profile_evidence is None


def test_forget_memory_deletes_profile_field_sourced_by_runtime_message() -> None:
    service = _profile_service()
    from anima_server.models import (
        MemoryItem,
        MemoryItemEvidence,
        UserProfileField,
        UserProfileFieldEvidence,
    )
    from anima_server.services.agent.forgetting import forget_memory

    with _db_session() as db:
        user = _make_user(db)
        item = MemoryItem(
            user_id=user.id,
            content=ef(
                user.id,
                "works as a founder",
                table="memory_items",
                field="content",
            ),
            category="fact",
            importance=3,
            source="test",
        )
        db.add(item)
        db.flush()
        memory_evidence = MemoryItemEvidence(
            user_id=user.id,
            memory_item_id=item.id,
            source_kind="llm",
            runtime_message_id=202,
            evidence_text=ef(
                user.id,
                "I am the founder building AnimaOS",
                table="memory_item_evidence",
                field="evidence_text",
            ),
        )
        db.add(memory_evidence)
        db.flush()
        field = service.upsert_profile_field(
            db,
            user_id=user.id,
            category="work",
            key="role",
            value="Founder building AnimaOS",
            confidence=0.9,
            evidence_text="I am the founder building AnimaOS",
            source_kind="profile_llm",
            runtime_message_id=202,
        )
        field_id = field.id
        profile_evidence_id = field.evidence[0].id

        result = forget_memory(db, memory_id=item.id, user_id=user.id)
        active_after_forget = service.list_profile_fields(db, user_id=user.id)
        forgotten_field = db.get(UserProfileField, field_id)
        forgotten_evidence = db.get(UserProfileFieldEvidence, profile_evidence_id)

    assert result.items_forgotten == 1
    assert active_after_forget == []
    assert forgotten_field is None
    assert forgotten_evidence is None


def test_forget_memory_deletes_single_token_profile_field_from_runtime_message() -> None:
    service = _profile_service()
    from anima_server.models import (
        MemoryItem,
        MemoryItemEvidence,
        UserProfileField,
        UserProfileFieldEvidence,
    )
    from anima_server.services.agent.forgetting import forget_memory

    with _db_session() as db:
        user = _make_user(db)
        item = MemoryItem(
            user_id=user.id,
            content=ef(
                user.id,
                "lives in Paris",
                table="memory_items",
                field="content",
            ),
            category="fact",
            importance=3,
            source="test",
        )
        db.add(item)
        db.flush()
        memory_evidence = MemoryItemEvidence(
            user_id=user.id,
            memory_item_id=item.id,
            source_kind="llm",
            runtime_message_id=203,
            evidence_text=ef(
                user.id,
                "I live in Paris",
                table="memory_item_evidence",
                field="evidence_text",
            ),
        )
        db.add(memory_evidence)
        db.flush()
        field = service.upsert_profile_field(
            db,
            user_id=user.id,
            category="identity",
            key="location",
            value="Paris",
            confidence=0.9,
            evidence_text="Paris",
            source_kind="profile_llm",
            runtime_message_id=203,
        )
        field_id = field.id
        profile_evidence_id = field.evidence[0].id

        result = forget_memory(db, memory_id=item.id, user_id=user.id)
        active_after_forget = service.list_profile_fields(db, user_id=user.id)
        forgotten_field = db.get(UserProfileField, field_id)
        forgotten_evidence = db.get(UserProfileFieldEvidence, profile_evidence_id)

    assert result.items_forgotten == 1
    assert active_after_forget == []
    assert forgotten_field is None
    assert forgotten_evidence is None


def test_forget_memory_preserves_unrelated_profile_field_from_same_turn() -> None:
    service = _profile_service()
    from anima_server.models import MemoryItem, MemoryItemEvidence
    from anima_server.services.agent.forgetting import forget_memory

    with _db_session() as db:
        user = _make_user(db)
        item = MemoryItem(
            user_id=user.id,
            content=ef(
                user.id,
                "likes cats",
                table="memory_items",
                field="content",
            ),
            category="preference",
            importance=3,
            source="test",
        )
        db.add(item)
        db.flush()
        memory_evidence = MemoryItemEvidence(
            user_id=user.id,
            memory_item_id=item.id,
            source_kind="llm",
            runtime_message_ids_json=[101, 102],
            evidence_text=ef(
                user.id,
                "I like cats",
                table="memory_item_evidence",
                field="evidence_text",
            ),
        )
        db.add(memory_evidence)
        db.flush()
        field = service.upsert_profile_field(
            db,
            user_id=user.id,
            category="work",
            key="role",
            value="Engineer",
            confidence=0.9,
            evidence_text="I work as an engineer",
            source_kind="profile_llm",
            runtime_message_id=102,
        )
        field_id = field.id

        result = forget_memory(db, memory_id=item.id, user_id=user.id)
        fields = service.list_profile_fields(db, user_id=user.id)
        evidence_count = len(fields[0].evidence)
        runtime_message_id = fields[0].evidence[0].runtime_message_id

    assert result.items_forgotten == 1
    assert [profile_field.id for profile_field in fields] == [field_id]
    assert evidence_count == 1
    assert runtime_message_id == 102


def test_forget_memory_preserves_same_turn_profile_field_with_one_shared_token() -> None:
    service = _profile_service()
    from anima_server.models import MemoryItem, MemoryItemEvidence
    from anima_server.services.agent.forgetting import forget_memory

    with _db_session() as db:
        user = _make_user(db)
        item = MemoryItem(
            user_id=user.id,
            content=ef(
                user.id,
                "works as founder of AnimaOS",
                table="memory_items",
                field="content",
            ),
            category="fact",
            importance=3,
            source="test",
        )
        db.add(item)
        db.flush()
        memory_evidence = MemoryItemEvidence(
            user_id=user.id,
            memory_item_id=item.id,
            source_kind="llm",
            runtime_message_id=303,
            evidence_text=ef(
                user.id,
                "I work as founder of AnimaOS",
                table="memory_item_evidence",
                field="evidence_text",
            ),
        )
        db.add(memory_evidence)
        db.flush()
        field = service.upsert_profile_field(
            db,
            user_id=user.id,
            category="active_projects",
            key="active_project",
            value="AnimaOS",
            confidence=0.9,
            evidence_text="AnimaOS is an active project",
            source_kind="profile_llm",
            runtime_message_id=303,
        )
        field_id = field.id

        result = forget_memory(db, memory_id=item.id, user_id=user.id)
        fields = service.list_profile_fields(db, user_id=user.id)
        evidence_count = len(fields[0].evidence) if fields else 0

    assert result.items_forgotten == 1
    assert [profile_field.id for profile_field in fields] == [field_id]
    assert evidence_count == 1


def test_forget_memory_rejects_pending_profile_candidates_from_source_turn() -> None:
    service = _profile_service()
    from anima_server.models import MemoryItem, MemoryItemEvidence
    from anima_server.services.agent.forgetting import forget_memory

    with (
        _soul_session_factory() as soul_factory,
        _runtime_session_factory() as runtime_session_factory,
    ):
        with soul_factory() as db:
            user = _make_user(db)
            user_id = user.id
            item = MemoryItem(
                user_id=user_id,
                content=ef(
                    user_id,
                    "works as founder of AnimaOS",
                    table="memory_items",
                    field="content",
                ),
                category="fact",
                importance=3,
                source="test",
            )
            db.add(item)
            db.flush()
            db.add(
                MemoryItemEvidence(
                    user_id=user_id,
                    memory_item_id=item.id,
                    source_kind="llm",
                    runtime_message_id=501,
                    evidence_text=ef(
                        user_id,
                        "I work as founder of AnimaOS",
                        table="memory_item_evidence",
                        field="evidence_text",
                    ),
                )
            )
            db.commit()
            item_id = item.id

        with runtime_session_factory() as runtime_db:
            pending = service.create_profile_update_candidate(
                runtime_db,
                user_id=user_id,
                category="work",
                key="role",
                value="Founder",
                evidence_text="I work as founder of AnimaOS",
                source_message_ids=[501],
            )
            shared_token = service.create_profile_update_candidate(
                runtime_db,
                user_id=user_id,
                category="active_projects",
                key="active_project",
                value="AnimaOS",
                evidence_text="AnimaOS is an active project",
                source_message_ids=[501],
            )
            unrelated = service.create_profile_update_candidate(
                runtime_db,
                user_id=user_id,
                category="preferences",
                key="likes",
                value="Cats",
                evidence_text="I like cats",
                source_message_ids=[501],
            )
            runtime_db.commit()
            pending_id = pending.id
            shared_token_id = shared_token.id
            unrelated_id = unrelated.id

        with soul_factory() as db:
            result = forget_memory(
                db,
                memory_id=item_id,
                user_id=user_id,
                runtime_db_factory=runtime_session_factory,
            )
            db.commit()

        with runtime_session_factory() as runtime_db:
            rejected_candidate = service.get_profile_update_candidate(
                runtime_db,
                candidate_id=pending_id,
            )
            untouched_candidate = service.get_profile_update_candidate(
                runtime_db,
                candidate_id=unrelated_id,
            )
            shared_token_candidate = service.get_profile_update_candidate(
                runtime_db,
                candidate_id=shared_token_id,
            )

    assert result.items_forgotten == 1
    assert rejected_candidate.status == "rejected"
    assert rejected_candidate.processed_at is not None
    assert shared_token_candidate.status == "extracted"
    assert untouched_candidate.status == "extracted"


@pytest.mark.asyncio
async def test_sleep_tasks_reconciles_claims_to_profile_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _profile_service()
    from anima_server.config import settings
    from anima_server.services.agent import sleep_tasks

    async def no_scan(**kwargs: object) -> tuple[int, int]:
        del kwargs
        return (0, 0)

    async def no_merge(**kwargs: object) -> int:
        del kwargs
        return 0

    original_provider = settings.agent_provider
    try:
        settings.agent_provider = "scaffold"
        monkeypatch.setattr(sleep_tasks, "scan_contradictions", no_scan)
        monkeypatch.setattr(sleep_tasks, "synthesize_profile", no_merge)
        monkeypatch.setattr(
            sleep_tasks,
            "_should_run_deep_monologue",
            lambda *args, **kwargs: False,
        )

        with _soul_session_factory() as factory:
            with factory() as db:
                user = _make_user(db)
                user_id = user.id
                upsert_claim(
                    db,
                    user_id=user_id,
                    content="works as a product manager",
                    category="fact",
                    evidence_text="I work as a product manager",
                )
                db.commit()

            result = await sleep_tasks.run_sleep_tasks(
                user_id=user_id,
                db_factory=factory,
            )

            with factory() as db:
                fields = service.list_profile_fields(db, user_id=user_id)

        assert result.profile_fields_reconciled == 1
        assert [(field.category, field.key) for field in fields] == [
            ("work", "occupation")
        ]
    finally:
        settings.agent_provider = original_provider


@pytest.mark.asyncio
async def test_sleep_tasks_invalidates_companion_memory_after_profile_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.config import settings
    from anima_server.services.agent import sleep_tasks

    class FakeCompanion:
        def __init__(self) -> None:
            self.invalidations = 0

        def invalidate_memory(self) -> None:
            self.invalidations += 1

    async def no_scan(**kwargs: object) -> tuple[int, int]:
        del kwargs
        return (0, 0)

    async def no_merge(**kwargs: object) -> int:
        del kwargs
        return 0

    companion = FakeCompanion()
    original_provider = settings.agent_provider
    try:
        settings.agent_provider = "scaffold"
        monkeypatch.setattr(sleep_tasks, "scan_contradictions", no_scan)
        monkeypatch.setattr(sleep_tasks, "synthesize_profile", no_merge)
        monkeypatch.setattr(
            sleep_tasks,
            "_should_run_deep_monologue",
            lambda *args, **kwargs: False,
        )
        monkeypatch.setattr(
            "anima_server.services.agent.companion.get_companion",
            lambda user_id: companion,
        )

        with _soul_session_factory() as factory:
            with factory() as db:
                user = _make_user(db)
                user_id = user.id
                upsert_claim(
                    db,
                    user_id=user_id,
                    content="works as a product manager",
                    category="fact",
                    evidence_text="I work as a product manager",
                )
                db.commit()

            result = await sleep_tasks.run_sleep_tasks(
                user_id=user_id,
                db_factory=factory,
            )

    finally:
        settings.agent_provider = original_provider

    assert result.profile_fields_reconciled == 1
    assert companion.invalidations == 1


def test_static_memory_blocks_include_structured_user_profile_block() -> None:
    service = _profile_service()
    from anima_server.models import AgentThread
    from anima_server.services.agent.memory_blocks import build_runtime_memory_blocks

    with _db_session() as db:
        user = _make_user(db)
        thread = AgentThread(user_id=user.id, status="active")
        db.add(thread)
        db.flush()
        service.upsert_profile_field(
            db,
            user_id=user.id,
            category="identity",
            key="name",
            value="Leo",
            evidence_text="my name is Leo",
        )
        service.upsert_profile_field(
            db,
            user_id=user.id,
            category="active_projects",
            key="animaos",
            value="Building the AnimaOS memory system",
            evidence_text="we are building memory",
        )

        blocks = build_runtime_memory_blocks(db, user_id=user.id, thread_id=thread.id)

    labels = [block.label for block in blocks]
    assert "user_profile" in labels
    block = next(block for block in blocks if block.label == "user_profile")
    assert block.read_only is True
    assert block.value.splitlines() == [
        "Identity:",
        "- name: Leo",
        "Active Projects:",
        "- animaos: Building the AnimaOS memory system",
    ]


@pytest.mark.asyncio
async def test_user_profile_api_lists_corrects_and_retracts_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _profile_service()
    from anima_server.api.routes import consciousness
    from anima_server.models import UserProfileField

    monkeypatch.setattr(
        consciousness,
        "require_unlocked_user",
        lambda request, user_id: None,
    )
    monkeypatch.setattr(
        consciousness,
        "_invalidate_companion_memory",
        lambda user_id: None,
    )

    with _db_session() as db:
        user = _make_user(db)
        field = service.upsert_profile_field(
            db,
            user_id=user.id,
            category="work",
            key="role",
            value="Product manager",
            evidence_text="I work as a product manager",
        )
        field_id = field.id
        db.commit()

        listed = await consciousness.get_user_profile(
            user_id=user.id,
            request=object(),
            include_history=False,
            category=None,
            db=db,
        )
        corrected = await consciousness.correct_user_profile_field(
            user_id=user.id,
            field_id=field_id,
            payload=consciousness.UserProfileCorrectionRequest(
                value="Founder building AnimaOS",
                confidence=0.95,
                evidenceText="Actually, I am the founder building AnimaOS",
            ),
            request=object(),
            db=db,
        )
        retracted = await consciousness.retract_user_profile_field(
            user_id=user.id,
            field_id=corrected.id,
            request=object(),
            db=db,
        )
        history = await consciousness.get_user_profile(
            user_id=user.id,
            request=object(),
            include_history=True,
            category=None,
            db=db,
        )
        old_field = db.get(UserProfileField, field_id)

    assert [field.value for field in listed.fields] == ["Product manager"]
    assert corrected.value == "Founder building AnimaOS"
    assert corrected.evidence[0].evidenceText == (
        "Actually, I am the founder building AnimaOS"
    )
    assert old_field.status == "superseded"
    assert old_field.superseded_by_id == corrected.id
    assert retracted.status == "retracted"
    assert sorted(field.status for field in history.fields) == [
        "retracted",
        "superseded",
    ]


@pytest.mark.asyncio
async def test_user_profile_api_returns_400_for_blank_correction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _profile_service()
    from anima_server.api.routes import consciousness
    from fastapi import HTTPException

    monkeypatch.setattr(
        consciousness,
        "require_unlocked_user",
        lambda request, user_id: None,
    )

    with _db_session() as db:
        user = _make_user(db)
        field = service.upsert_profile_field(
            db,
            user_id=user.id,
            category="work",
            key="role",
            value="Product manager",
            evidence_text="I work as a product manager",
        )
        field_id = field.id
        db.commit()

        with pytest.raises(HTTPException) as exc_info:
            await consciousness.correct_user_profile_field(
                user_id=user.id,
                field_id=field_id,
                payload=consciousness.UserProfileCorrectionRequest(
                    value="   ",
                    confidence=0.95,
                    evidenceText="blank correction",
                ),
                request=object(),
                db=db,
            )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Profile value cannot be empty"


@pytest.mark.asyncio
async def test_llm_extraction_parses_profile_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    from anima_server.config import settings
    from anima_server.services.agent.consolidation import extract_memories_via_llm

    async def _fake_call_llm_for_text(*_args, **_kwargs) -> str:
        return json.dumps(
            {
                "memories": [
                    {
                        "content": "Works as a founder",
                        "category": "fact",
                        "importance": 4,
                    }
                ],
                "profile_updates": [
                    {
                        "category": "work",
                        "key": "role",
                        "value": "Founder building AnimaOS",
                        "confidence": 0.86,
                        "evidence_quote": "I am the founder building AnimaOS",
                    }
                ],
                "emotion": None,
            }
        )

    monkeypatch.setattr(settings, "agent_provider", "openrouter")
    monkeypatch.setattr(
        "anima_server.services.agent.llm_json.call_llm_for_text",
        _fake_call_llm_for_text,
    )

    result = await extract_memories_via_llm(
        user_message="I am the founder building AnimaOS",
        assistant_response="Got it.",
    )

    assert result.memories[0]["content"] == "Works as a founder"
    assert result.profile_updates == [
        {
            "category": "work",
            "key": "role",
            "value": "Founder building AnimaOS",
            "confidence": 0.86,
            "evidence_quote": "I am the founder building AnimaOS",
        }
    ]


@pytest.mark.asyncio
async def test_llm_extraction_parses_profile_updates_without_memories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.config import settings
    from anima_server.services.agent.consolidation import extract_memories_via_llm

    async def _fake_call_llm_for_text(*_args, **_kwargs) -> str:
        return json.dumps(
            {
                "memories": None,
                "profile_updates": [
                    {
                        "category": "work",
                        "key": "role",
                        "value": "Founder building AnimaOS",
                        "confidence": 0.86,
                        "evidence_quote": "I am the founder building AnimaOS",
                    }
                ],
                "emotion": None,
            }
        )

    monkeypatch.setattr(settings, "agent_provider", "openrouter")
    monkeypatch.setattr(
        "anima_server.services.agent.llm_json.call_llm_for_text",
        _fake_call_llm_for_text,
    )

    result = await extract_memories_via_llm(
        user_message="I am the founder building AnimaOS",
        assistant_response="Got it.",
    )

    assert result.memories == []
    assert result.profile_updates == [
        {
            "category": "work",
            "key": "role",
            "value": "Founder building AnimaOS",
            "confidence": 0.86,
            "evidence_quote": "I am the founder building AnimaOS",
        }
    ]


@pytest.mark.asyncio
async def test_soul_writer_promotes_profile_update_candidates() -> None:
    service = _profile_service()
    from anima_server.services.agent.soul_writer import run_soul_writer

    with _soul_session_factory() as soul_factory, _runtime_session_factory() as runtime_session_factory:
        with soul_factory() as db:
            user = _make_user(db)
            user_id = user.id
            db.commit()

        with runtime_session_factory() as runtime_db:
            candidate = service.create_profile_update_candidate(
                runtime_db,
                user_id=user_id,
                category="work",
                key="role",
                value="Founder building AnimaOS",
                confidence=0.9,
                evidence_text="I am the founder building AnimaOS",
                source_message_ids=[101, 102],
            )
            runtime_db.commit()
            candidate_id = candidate.id

        result = await run_soul_writer(
            user_id,
            soul_db_factory=soul_factory,
            runtime_db_factory=runtime_session_factory,
        )

        with runtime_session_factory() as runtime_db:
            promoted_candidate = service.get_profile_update_candidate(
                runtime_db,
                candidate_id=candidate_id,
            )
        with soul_factory() as db:
            fields = service.list_profile_fields(db, user_id=user_id)
            evidence_count = len(fields[0].evidence)

    assert result.profile_updates_promoted == 1
    assert promoted_candidate.status == "promoted"
    assert [(field.category, field.key) for field in fields] == [("work", "role")]
    assert evidence_count == 1


@pytest.mark.asyncio
async def test_soul_writer_profile_retry_preserves_newer_profile_field() -> None:
    service = _profile_service()
    from anima_server.services.agent.soul_writer import run_soul_writer

    older_observed = datetime(2026, 1, 1, tzinfo=UTC)
    newer_observed = datetime(2026, 2, 1, tzinfo=UTC)
    with _soul_session_factory() as soul_factory, _runtime_session_factory() as runtime_session_factory:
        with soul_factory() as db:
            user = _make_user(db)
            user_id = user.id
            service.upsert_profile_field(
                db,
                user_id=user_id,
                category="work",
                key="role",
                value="Systems designer",
                evidence_text="Later profile extraction said systems designer",
                source_kind="profile_llm",
                observed_at=newer_observed,
            )
            db.commit()

        with runtime_session_factory() as runtime_db:
            candidate = service.create_profile_update_candidate(
                runtime_db,
                user_id=user_id,
                category="work",
                key="role",
                value="Product manager",
                confidence=0.9,
                evidence_text="Earlier profile extraction said product manager",
                source_message_ids=[101],
            )
            candidate.status = "failed"
            candidate.retry_count = 1
            candidate.created_at = older_observed
            runtime_db.commit()
            candidate_id = candidate.id

        result = await run_soul_writer(
            user_id,
            soul_db_factory=soul_factory,
            runtime_db_factory=runtime_session_factory,
        )

        with runtime_session_factory() as runtime_db:
            promoted_candidate = service.get_profile_update_candidate(
                runtime_db,
                candidate_id=candidate_id,
            )
        with soul_factory() as db:
            fields = service.list_profile_fields(db, user_id=user_id)
            active_value = df(
                user_id,
                fields[0].value_text,
                table="user_profile_fields",
                field="value_text",
            )
            evidence_count = len(fields[0].evidence)

    assert result.profile_updates_promoted == 1
    assert promoted_candidate.status == "promoted"
    assert [(field.category, field.key) for field in fields] == [("work", "role")]
    assert active_value == "Systems designer"
    assert evidence_count == 1


def test_profile_update_candidate_can_be_reextracted_after_promotion() -> None:
    service = _profile_service()
    with _runtime_session_factory() as runtime_session_factory, runtime_session_factory() as runtime_db:
        first = service.create_profile_update_candidate(
            runtime_db,
            user_id=1,
            category="work",
            key="role",
            value="Founder building AnimaOS",
            evidence_text="I am the founder building AnimaOS",
        )
        assert first is not None
        first.status = "promoted"
        runtime_db.commit()

        second = service.create_profile_update_candidate(
            runtime_db,
            user_id=1,
            category="work",
            key="role",
            value="Founder building AnimaOS",
            evidence_text="I said again that I am building AnimaOS",
        )
        runtime_db.commit()

    assert second is not None
    assert second.id != first.id
    assert second.status == "extracted"
