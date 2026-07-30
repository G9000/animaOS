from __future__ import annotations

import re
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from anima_server.models import (
    MemoryClaim,
    MemoryClaimEvidence,
    UserProfileField,
    UserProfileFieldEvidence,
)
from anima_server.models.runtime_memory import ProfileUpdateCandidate
from anima_server.services.data_crypto import df, ef

PROFILE_CATEGORIES: tuple[str, ...] = (
    "identity",
    "relationships",
    "work",
    "preferences",
    "goals",
    "values",
    "constraints",
    "emotional_patterns",
    "active_projects",
)

_CATEGORY_ALIASES: dict[str, str] = {
    "relationship": "relationships",
    "relationships": "relationships",
    "preference": "preferences",
    "preferences": "preferences",
    "goal": "goals",
    "goals": "goals",
    "value": "values",
    "values": "values",
    "constraint": "constraints",
    "constraints": "constraints",
    "emotional_pattern": "emotional_patterns",
    "emotional_patterns": "emotional_patterns",
    "active_project": "active_projects",
    "active_projects": "active_projects",
    "project": "active_projects",
    "projects": "active_projects",
    "work_context": "work",
    "work": "work",
    "identity": "identity",
    "life_context": "identity",
}

_CATEGORY_TITLES: dict[str, str] = {
    "identity": "Identity",
    "relationships": "Relationships",
    "work": "Work",
    "preferences": "Preferences",
    "goals": "Goals",
    "values": "Values",
    "constraints": "Constraints",
    "emotional_patterns": "Emotional Patterns",
    "active_projects": "Active Projects",
}


def normalize_profile_category(category: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", category.strip().casefold()).strip("_")
    resolved = _CATEGORY_ALIASES.get(normalized)
    if resolved is None:
        raise ValueError(f"Invalid profile category: {category}")
    return resolved


def normalize_profile_key(key: str) -> str:
    normalized = re.sub(r"\s+", " ", key.strip()).casefold()
    if not normalized:
        raise ValueError("Profile key cannot be empty")
    return normalized[:128]


def upsert_profile_field(
    db: Session,
    *,
    user_id: int,
    category: str,
    key: str,
    value: str,
    confidence: float = 0.8,
    evidence_text: str | None = None,
    source_kind: str = "extraction",
    source_memory_id: int | None = None,
    source_evidence_id: int | None = None,
    source_claim_evidence_id: int | None = None,
    runtime_thread_id: int | None = None,
    runtime_message_id: int | None = None,
    observed_at: datetime | None = None,
) -> UserProfileField:
    """Create or update a structured user profile field.

    Corrections preserve audit history by superseding the old row instead of
    mutating its value in place.
    """
    normalized_category = normalize_profile_category(category)
    normalized_key = normalize_profile_key(key)
    clean_value = re.sub(r"\s+", " ", value.strip())
    if not clean_value:
        raise ValueError("Profile value cannot be empty")

    now = datetime.now(UTC)
    observed = observed_at or now
    confidence = max(0.0, min(1.0, float(confidence)))
    existing = _get_active_profile_field(
        db,
        user_id=user_id,
        category=normalized_category,
        key=normalized_key,
    )
    if existing is None and _should_preserve_retracted_profile_field(source_kind):
        retracted = _get_latest_retracted_profile_field(
            db,
            user_id=user_id,
            category=normalized_category,
            key=normalized_key,
        )
        if retracted is not None:
            return retracted

    if existing is not None:
        existing_value = df(
            user_id,
            existing.value_text,
            table="user_profile_fields",
            field="value_text",
        )
        if existing_value.strip().casefold() == clean_value.casefold():
            existing.confidence = max(float(existing.confidence), confidence)
            if source_kind == "user_correction":
                existing.source_kind = "user_correction"
            if _profile_datetime(observed) > _profile_datetime(existing.last_observed_at):
                existing.last_observed_at = observed
            existing.updated_at = now
            _add_profile_evidence(
                db,
                user_id=user_id,
                field=existing,
                evidence_text=evidence_text or clean_value,
                source_kind=source_kind,
                source_memory_id=source_memory_id,
                source_evidence_id=source_evidence_id,
                source_claim_evidence_id=source_claim_evidence_id,
                runtime_thread_id=runtime_thread_id,
                runtime_message_id=runtime_message_id,
                observed_at=observed,
            )
            db.flush()
            return existing

        if _preserve_newer_profile_field(
            existing=existing,
            incoming_source_kind=source_kind,
            observed_at=observed,
        ):
            return existing

        if _preserve_user_corrected_profile_field(
            existing=existing,
            incoming_source_kind=source_kind,
        ):
            return existing

    field = UserProfileField(
        user_id=user_id,
        category=normalized_category,
        key=normalized_key,
        value_text=ef(
            user_id,
            clean_value,
            table="user_profile_fields",
            field="value_text",
        ),
        confidence=confidence,
        status="active",
        source_kind=source_kind,
        source_memory_id=source_memory_id,
        source_evidence_id=source_evidence_id,
        source_claim_evidence_id=source_claim_evidence_id,
        first_observed_at=observed,
        last_observed_at=observed,
        updated_at=now,
    )
    db.add(field)
    db.flush()

    if existing is not None:
        existing.status = "superseded"
        existing.superseded_by_id = field.id
        existing.updated_at = now
        db.add(existing)

    _add_profile_evidence(
        db,
        user_id=user_id,
        field=field,
        evidence_text=evidence_text or clean_value,
        source_kind=source_kind,
        source_memory_id=source_memory_id,
        source_evidence_id=source_evidence_id,
        source_claim_evidence_id=source_claim_evidence_id,
        runtime_thread_id=runtime_thread_id,
        runtime_message_id=runtime_message_id,
        observed_at=observed,
    )
    db.flush()
    return field


def _preserve_user_corrected_profile_field(
    *,
    existing: UserProfileField,
    incoming_source_kind: str,
) -> bool:
    if existing.source_kind != "user_correction":
        return False
    return incoming_source_kind not in {"user_correction"}


def _preserve_newer_profile_field(
    *,
    existing: UserProfileField,
    incoming_source_kind: str,
    observed_at: datetime,
) -> bool:
    if incoming_source_kind == "user_correction":
        return False
    existing_observed = existing.last_observed_at or existing.updated_at
    return _profile_datetime(existing_observed) > _profile_datetime(observed_at)


def _should_preserve_retracted_profile_field(source_kind: str) -> bool:
    return source_kind in {
        "claim_reconciliation",
        "extraction",
        "llm",
        "reflection",
    } or source_kind.startswith("profile_")


def correct_profile_field(
    db: Session,
    *,
    user_id: int,
    field_id: int,
    value: str,
    confidence: float = 1.0,
    evidence_text: str = "user correction",
) -> UserProfileField:
    existing = db.get(UserProfileField, field_id)
    if existing is None or existing.user_id != user_id or existing.status != "active":
        raise ValueError("Active profile field not found")
    return upsert_profile_field(
        db,
        user_id=user_id,
        category=existing.category,
        key=existing.key,
        value=value,
        confidence=confidence,
        evidence_text=evidence_text,
        source_kind="user_correction",
    )


def retract_profile_field(
    db: Session,
    *,
    user_id: int,
    field_id: int,
) -> UserProfileField:
    field = db.get(UserProfileField, field_id)
    if field is None or field.user_id != user_id or field.status != "active":
        raise ValueError("Active profile field not found")
    field.status = "retracted"
    field.updated_at = datetime.now(UTC)
    db.add(field)
    db.flush()
    return field


def list_profile_fields(
    db: Session,
    *,
    user_id: int,
    include_history: bool = False,
    category: str | None = None,
) -> list[UserProfileField]:
    q = select(UserProfileField).where(UserProfileField.user_id == user_id)
    if not include_history:
        q = q.where(UserProfileField.status == "active")
    if category:
        q = q.where(UserProfileField.category == normalize_profile_category(category))
    q = q.order_by(
        UserProfileField.category.asc(),
        UserProfileField.key.asc(),
        UserProfileField.updated_at.desc(),
        UserProfileField.id.desc(),
    )
    return list(db.scalars(q).all())


def render_profile_prompt_block(
    db: Session,
    *,
    user_id: int,
    max_chars: int = 1800,
) -> str:
    fields = list_profile_fields(db, user_id=user_id)
    if not fields:
        return ""

    lines: list[str] = []
    by_category: dict[str, list[UserProfileField]] = {}
    for field in fields:
        by_category.setdefault(field.category, []).append(field)

    for category in PROFILE_CATEGORIES:
        category_fields = by_category.get(category)
        if not category_fields:
            continue
        if lines:
            candidate_header = _CATEGORY_TITLES[category] + ":"
        else:
            candidate_header = _CATEGORY_TITLES[category] + ":"
        lines.append(candidate_header)
        for field in sorted(category_fields, key=lambda item: item.key.casefold()):
            value = df(
                user_id,
                field.value_text,
                table="user_profile_fields",
                field="value_text",
            )
            lines.append(f"- {field.key}: {value}")

    rendered = "\n".join(lines)
    return _truncate_profile_prompt_block(rendered, max_chars)


def _truncate_profile_prompt_block(rendered: str, max_chars: int) -> str:
    if len(rendered) <= max_chars:
        return rendered.rstrip()
    truncated = rendered[:max_chars]
    line_boundary = truncated.rfind("\n")
    if line_boundary > 0:
        return truncated[:line_boundary].rstrip()
    return truncated.rstrip()


def reconcile_profile_from_claims(
    db: Session,
    *,
    user_id: int,
    limit: int = 200,
) -> int:
    claims = list(
        db.scalars(
            select(MemoryClaim)
            .where(
                MemoryClaim.user_id == user_id,
                MemoryClaim.status == "active",
            )
            .order_by(MemoryClaim.updated_at.desc(), MemoryClaim.id.desc())
            .limit(limit)
        ).all()
    )
    reconciled = 0
    for claim in claims:
        mapped = _profile_mapping_for_claim(claim)
        if mapped is None:
            continue
        category, key = mapped
        value = df(
            user_id,
            claim.value_text,
            table="memory_items",
            field="content",
        )
        claim_evidence = _latest_claim_evidence(db, claim_id=claim.id)
        source_claim_evidence_id = claim_evidence.id if claim_evidence is not None else None
        if _profile_claim_already_reconciled(
            db,
            user_id=user_id,
            category=category,
            key=key,
            value=value,
            source_claim_evidence_id=source_claim_evidence_id,
            source_memory_id=claim.memory_item_id,
            claim_observed_at=(
                claim_evidence.created_at
                if claim_evidence is not None
                else claim.updated_at
            ),
        ):
            continue

        evidence = (
            _claim_evidence_text(user_id=user_id, evidence=claim_evidence)
            if claim_evidence is not None
            else value
        )
        upsert_profile_field(
            db,
            user_id=user_id,
            category=category,
            key=key,
            value=value,
            confidence=claim.confidence,
            evidence_text=evidence,
            source_kind="claim_reconciliation",
            source_memory_id=claim.memory_item_id,
            source_claim_evidence_id=source_claim_evidence_id,
        )
        reconciled += 1
    db.flush()
    return reconciled


def compute_profile_update_hash(
    user_id: int,
    category: str,
    key: str,
    value: str,
) -> str:
    normalized = "|".join(
        (
            str(user_id),
            normalize_profile_category(category),
            normalize_profile_key(key).casefold(),
            re.sub(r"\s+", " ", value.strip()).casefold(),
        )
    )
    return sha256(normalized.encode("utf-8")).hexdigest()


def create_profile_update_candidate(
    runtime_db: Session,
    *,
    user_id: int,
    category: str,
    key: str,
    value: str,
    confidence: float = 0.8,
    evidence_text: str | None = None,
    source: str = "llm",
    source_message_ids: list[int] | None = None,
    extraction_model: str | None = None,
) -> ProfileUpdateCandidate | None:
    normalized_category = normalize_profile_category(category)
    normalized_key = normalize_profile_key(key)
    clean_value = re.sub(r"\s+", " ", value.strip())
    if not clean_value:
        return None
    confidence = max(0.0, min(1.0, float(confidence)))
    content_hash = compute_profile_update_hash(
        user_id,
        normalized_category,
        normalized_key,
        clean_value,
    )
    existing = runtime_db.scalar(
        select(ProfileUpdateCandidate.id).where(
            ProfileUpdateCandidate.content_hash == content_hash,
            ProfileUpdateCandidate.status.not_in(["rejected", "failed", "promoted"]),
        )
    )
    if existing is not None:
        return None

    from sqlalchemy.orm.attributes import set_committed_value

    from anima_server.services.corefs.sealed_runtime import (
        runtime_index_for_sensitive_write,
        seal_runtime_record,
    )

    clean_evidence = (evidence_text or clean_value).strip()
    runtime_index = runtime_index_for_sensitive_write(
        runtime_db,
        user_id=user_id,
    )
    candidate = ProfileUpdateCandidate(
        user_id=user_id,
        category=normalized_category,
        key="" if runtime_index is not None else normalized_key,
        value="" if runtime_index is not None else clean_value,
        confidence=confidence,
        evidence_text=None if runtime_index is not None else clean_evidence,
        source=source,
        source_message_ids=[int(message_id) for message_id in source_message_ids or []],
        extraction_model=extraction_model,
        content_hash=content_hash,
        status="extracted",
    )
    runtime_db.add(candidate)
    runtime_db.flush()
    if runtime_index is not None:
        seal_runtime_record(
            runtime_db,
            index=runtime_index,
            row_type="profile_update_candidate",
            row_id=int(candidate.id),
            owner_id=user_id,
            payload={
                "key": normalized_key,
                "value": clean_value,
                "evidence_text": clean_evidence,
                "last_error": None,
            },
        )
        set_committed_value(candidate, "key", normalized_key)
        set_committed_value(candidate, "value", clean_value)
        set_committed_value(candidate, "evidence_text", clean_evidence)
    return candidate


def create_profile_update_candidates_from_payload(
    runtime_db: Session,
    *,
    user_id: int,
    profile_updates: list[dict[str, Any]],
    source_message_ids: list[int] | None = None,
    extraction_model: str | None = None,
    source: str = "llm",
) -> int:
    created = 0
    for update in profile_updates:
        category = update.get("category")
        key = update.get("key")
        value = update.get("value")
        if not (
            isinstance(category, str)
            and isinstance(key, str)
            and isinstance(value, str)
        ):
            continue

        raw_confidence = update.get("confidence", 0.8)
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError):
            confidence = 0.8

        evidence = (
            update.get("evidence_quote")
            or update.get("evidence_text")
            or update.get("evidence")
        )
        try:
            candidate = create_profile_update_candidate(
                runtime_db,
                user_id=user_id,
                category=category,
                key=key,
                value=value,
                confidence=confidence,
                evidence_text=evidence if isinstance(evidence, str) else None,
                source=source,
                source_message_ids=source_message_ids,
                extraction_model=extraction_model,
            )
        except ValueError:
            continue
        if candidate is not None:
            created += 1
    return created


def get_profile_update_candidate(
    runtime_db: Session,
    *,
    candidate_id: int,
) -> ProfileUpdateCandidate | None:
    return runtime_db.get(ProfileUpdateCandidate, candidate_id)


def count_eligible_profile_update_candidates(
    runtime_db: Session,
    *,
    user_id: int,
    max_retry: int = 3,
) -> int:
    return runtime_db.scalar(
        select(func.count(ProfileUpdateCandidate.id)).where(
            ProfileUpdateCandidate.user_id == user_id,
            or_(
                ProfileUpdateCandidate.status.in_(["extracted", "queued"]),
                and_(
                    ProfileUpdateCandidate.status == "failed",
                    ProfileUpdateCandidate.retry_count < max_retry,
                ),
            ),
        )
    ) or 0


def get_profile_update_candidates_for_promotion(
    runtime_db: Session,
    *,
    user_id: int,
    limit: int,
    max_retry: int = 3,
) -> list[ProfileUpdateCandidate]:
    return list(
        runtime_db.scalars(
            select(ProfileUpdateCandidate)
            .where(
                ProfileUpdateCandidate.user_id == user_id,
                or_(
                    ProfileUpdateCandidate.status.in_(["extracted", "queued"]),
                    and_(
                        ProfileUpdateCandidate.status == "failed",
                        ProfileUpdateCandidate.retry_count < max_retry,
                    ),
                ),
            )
            .order_by(ProfileUpdateCandidate.created_at.asc(), ProfileUpdateCandidate.id.asc())
            .limit(limit)
        ).all()
    )


def promote_profile_update_candidate(
    soul_db: Session,
    *,
    candidate: ProfileUpdateCandidate,
) -> UserProfileField:
    source_message_ids = [int(message_id) for message_id in candidate.source_message_ids or []]
    return upsert_profile_field(
        soul_db,
        user_id=candidate.user_id,
        category=candidate.category,
        key=candidate.key,
        value=candidate.value,
        confidence=candidate.confidence,
        evidence_text=candidate.evidence_text or candidate.value,
        source_kind=f"profile_{candidate.source}",
        runtime_message_id=source_message_ids[-1] if source_message_ids else None,
        observed_at=candidate.created_at,
    )


def _get_active_profile_field(
    db: Session,
    *,
    user_id: int,
    category: str,
    key: str,
) -> UserProfileField | None:
    return db.scalar(
        select(UserProfileField)
        .where(
            UserProfileField.user_id == user_id,
            UserProfileField.category == category,
            UserProfileField.key == key,
            UserProfileField.status == "active",
        )
        .order_by(UserProfileField.updated_at.desc(), UserProfileField.id.desc())
    )


def _get_latest_retracted_profile_field(
    db: Session,
    *,
    user_id: int,
    category: str,
    key: str,
) -> UserProfileField | None:
    return db.scalar(
        select(UserProfileField)
        .where(
            UserProfileField.user_id == user_id,
            UserProfileField.category == category,
            UserProfileField.key == key,
            UserProfileField.status == "retracted",
        )
        .order_by(UserProfileField.updated_at.desc(), UserProfileField.id.desc())
    )


def _add_profile_evidence(
    db: Session,
    *,
    user_id: int,
    field: UserProfileField,
    evidence_text: str,
    source_kind: str,
    source_memory_id: int | None,
    source_evidence_id: int | None,
    source_claim_evidence_id: int | None,
    runtime_thread_id: int | None,
    runtime_message_id: int | None,
    observed_at: datetime | None,
) -> UserProfileFieldEvidence:
    if source_evidence_id is not None:
        existing = db.scalar(
            select(UserProfileFieldEvidence).where(
                UserProfileFieldEvidence.profile_field_id == field.id,
                UserProfileFieldEvidence.user_id == user_id,
                UserProfileFieldEvidence.source_evidence_id == source_evidence_id,
            )
        )
        if existing is not None:
            return existing
    if source_claim_evidence_id is not None:
        existing = db.scalar(
            select(UserProfileFieldEvidence).where(
                UserProfileFieldEvidence.profile_field_id == field.id,
                UserProfileFieldEvidence.user_id == user_id,
                UserProfileFieldEvidence.source_claim_evidence_id
                == source_claim_evidence_id,
            )
        )
        if existing is not None:
            return existing

    evidence = UserProfileFieldEvidence(
        profile_field_id=field.id,
        user_id=user_id,
        source_kind=source_kind,
        source_memory_id=source_memory_id,
        source_evidence_id=source_evidence_id,
        source_claim_evidence_id=source_claim_evidence_id,
        runtime_thread_id=runtime_thread_id,
        runtime_message_id=runtime_message_id,
        evidence_text=ef(
            user_id,
            evidence_text.strip(),
            table="user_profile_field_evidence",
            field="evidence_text",
        ),
        observed_at=observed_at,
    )
    db.add(evidence)
    db.flush()
    return evidence


def _profile_mapping_for_claim(claim: MemoryClaim) -> tuple[str, str] | None:
    namespace = claim.namespace
    slot = claim.slot
    if namespace == "fact":
        if slot in {"name", "display_name", "age", "birthday", "gender", "location"}:
            return "identity", slot
        if slot in {"occupation", "employer"}:
            return "work", slot
        return None
    if namespace == "preference":
        return "preferences", slot
    if namespace == "goal":
        return "goals", slot
    if namespace == "relationship":
        return "relationships", slot
    return None


def _profile_claim_already_reconciled(
    db: Session,
    *,
    user_id: int,
    category: str,
    key: str,
    value: str,
    source_claim_evidence_id: int | None,
    source_memory_id: int | None,
    claim_observed_at: datetime | None,
) -> bool:
    active = _get_active_profile_field(
        db,
        user_id=user_id,
        category=category,
        key=key,
    )
    if active is not None and active.source_kind == "user_correction":
        active_value = df(
            user_id,
            active.value_text,
            table="user_profile_fields",
            field="value_text",
        )
        if active_value.strip().casefold() != value.strip().casefold():
            return True

    if (
        active is not None
        and active.source_kind != "claim_reconciliation"
        and _profile_datetime(active.last_observed_at or active.updated_at)
        >= _profile_datetime(claim_observed_at)
    ):
        active_value = df(
            user_id,
            active.value_text,
            table="user_profile_fields",
            field="value_text",
        )
        if active_value.strip().casefold() != value.strip().casefold():
            return True

    if source_claim_evidence_id is not None:
        existing = db.scalar(
            select(UserProfileFieldEvidence.id).where(
                UserProfileFieldEvidence.user_id == user_id,
                UserProfileFieldEvidence.source_claim_evidence_id
                == source_claim_evidence_id,
            )
        )
        return existing is not None

    if source_memory_id is not None:
        existing = db.scalar(
            select(UserProfileFieldEvidence.id)
            .join(
                UserProfileField,
                UserProfileField.id == UserProfileFieldEvidence.profile_field_id,
            )
            .where(
                UserProfileFieldEvidence.user_id == user_id,
                UserProfileFieldEvidence.source_kind == "claim_reconciliation",
                UserProfileFieldEvidence.source_memory_id == source_memory_id,
                UserProfileField.user_id == user_id,
                UserProfileField.category == category,
                UserProfileField.key == key,
            )
        )
        return existing is not None

    if active is not None:
        active_value = df(
            user_id,
            active.value_text,
            table="user_profile_fields",
            field="value_text",
        )
        if active_value.strip().casefold() == value.strip().casefold():
            existing = db.scalar(
                select(UserProfileFieldEvidence.id).where(
                    UserProfileFieldEvidence.user_id == user_id,
                    UserProfileFieldEvidence.profile_field_id == active.id,
                    UserProfileFieldEvidence.source_kind == "claim_reconciliation",
                    UserProfileFieldEvidence.source_claim_evidence_id.is_(None),
                    UserProfileFieldEvidence.source_memory_id.is_(None),
                )
            )
            return existing is not None

    return False


def _profile_datetime(value: datetime | None) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _latest_claim_evidence(
    db: Session,
    *,
    claim_id: int,
) -> MemoryClaimEvidence | None:
    return db.scalar(
        select(MemoryClaimEvidence)
        .where(MemoryClaimEvidence.claim_id == claim_id)
        .order_by(MemoryClaimEvidence.created_at.desc(), MemoryClaimEvidence.id.desc())
    )


def _claim_evidence_text(
    *,
    user_id: int,
    evidence: MemoryClaimEvidence,
) -> str:
    if evidence is None:
        return ""
    return df(
        user_id,
        evidence.source_text,
        table="memory_claim_evidence",
        field="source_text",
    )
