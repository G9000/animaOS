"""MemoryCandidate creation and query helpers."""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from anima_server.models.runtime_memory import MemoryCandidate
from anima_server.services.agent.memory_salience import (
    merge_salience,
    serialize_memory_salience,
)

logger = logging.getLogger(__name__)

_VALID_CATEGORIES = frozenset(
    {"fact", "preference", "goal", "relationship", "minor_observation"}
)
_VALID_SOURCES = frozenset({"regex", "llm", "predict_calibrate", "tool", "feedback"})
_VALID_IMPORTANCE_SOURCES = frozenset({
    "regex", "llm", "predict_calibrate", "user_explicit", "correction",
})
# "folded" (IL4: candidate scored below the promotion threshold and was
# accumulated into a latent trace instead) is terminal like rejected/
# reinforced/superseded/failed: each repeat mention of the same content
# must create its OWN candidate row so it folds into the trace again —
# that repeated accumulation is the entire point of IL4. Treating it as
# non-terminal would instead merge repeats into the first folded row and
# the trace would never receive more than one fold.
_TERMINAL_STATUSES = frozenset(
    {"rejected", "reinforced", "superseded", "failed", "folded"}
)


def compute_content_hash(
    user_id: int, category: str, importance_source: str, content: str,
) -> str:
    """Compute a SHA-256 hash for dedup keyed on user, category, importance_source, and normalized content."""
    normalized = content.strip().lower()
    return hashlib.sha256(
        f"{user_id}:{category}:{importance_source}:{normalized}".encode()
    ).hexdigest()


def create_memory_candidate(
    runtime_db: Session,
    *,
    user_id: int,
    content: str,
    category: str,
    importance: int = 3,
    importance_source: str = "llm",
    source: str = "llm",
    supersedes_item_id: int | None = None,
    source_message_ids: list[int] | None = None,
    extraction_model: str | None = None,
    tags: list[str] | None = None,
    salience: dict[str, Any] | None = None,
) -> MemoryCandidate | None:
    """Create a candidate with hash-based dedup. Returns None on duplicate."""
    if category not in _VALID_CATEGORIES:
        category = "fact"
    if source not in _VALID_SOURCES:
        source = "llm"
    if importance_source not in _VALID_IMPORTANCE_SOURCES:
        importance_source = "llm"
    importance = max(1, min(5, importance))

    content_hash = compute_content_hash(user_id, category, importance_source, content)
    from anima_server.services.agent.memory_salience import normalize_salience_payload

    salience_json = normalize_salience_payload(
        salience,
        content=content,
        category=category,
        importance=importance,
    )

    # Explicit dedup check — works on both PG (with partial unique index) and SQLite.
    existing = runtime_db.scalar(
        select(MemoryCandidate).where(
            MemoryCandidate.content_hash == content_hash,
            MemoryCandidate.status.not_in(_TERMINAL_STATUSES),
        )
    )
    if existing is not None:
        if existing.status == "promoted":
            existing.status = "superseded"
            runtime_db.flush()
        else:
            existing.salience_json = _merge_candidate_salience(
                existing.salience_json,
                salience_json,
            )
            if int(existing.importance or 0) == 1:
                # Weak-signal lane (IL4): an active repeat must still count
                # toward latent accumulation — record it so the fold applies
                # once per repeat, and keep the repeat's source messages for
                # trace evidence provenance.
                merged_salience = dict(existing.salience_json or {})
                merged_salience["repeat_count"] = int(
                    merged_salience.get("repeat_count", 1) or 1
                ) + 1
                existing.salience_json = merged_salience
                merged_ids = list(existing.source_message_ids or [])
                for message_id in source_message_ids or []:
                    if message_id not in merged_ids:
                        merged_ids.append(message_id)
                existing.source_message_ids = merged_ids
            runtime_db.flush()
            return None

    candidate = MemoryCandidate(
        user_id=user_id,
        content=content.strip(),
        category=category,
        importance=importance,
        importance_source=importance_source,
        source=source,
        content_hash=content_hash,
        status="extracted",
        supersedes_item_id=supersedes_item_id,
        source_message_ids=source_message_ids,
        extraction_model=extraction_model,
        tags_json=tags,
        salience_json=salience_json,
    )
    try:
        with runtime_db.begin_nested():
            runtime_db.add(candidate)
            runtime_db.flush()
        return candidate
    except IntegrityError:
        return None


def _merge_candidate_salience(
    existing: dict[str, Any] | None,
    incoming: dict[str, Any],
) -> dict[str, object]:
    merged = serialize_memory_salience(merge_salience(existing, incoming))
    existing_fields = _explicit_signal_fields(existing)
    incoming_fields = _explicit_signal_fields(incoming)
    signal_fields = sorted(existing_fields | incoming_fields)
    if signal_fields:
        merged["salience_source"] = "explicit"
        merged["salience_signal_fields"] = signal_fields
    else:
        merged["salience_source"] = "inferred"
        merged["salience_signal_fields"] = []
    return merged


def _explicit_signal_fields(value: dict[str, Any] | None) -> set[str]:
    if not isinstance(value, dict) or value.get("salience_source") != "explicit":
        return set()
    fields = value.get("salience_signal_fields")
    if not isinstance(fields, list):
        return set()
    return {str(field) for field in fields}


def count_eligible_candidates(runtime_db: Session, user_id: int, max_retry: int = 3) -> int:
    """Count candidates eligible for promotion."""
    return runtime_db.scalar(
        select(func.count(MemoryCandidate.id)).where(
            MemoryCandidate.user_id == user_id,
            or_(
                MemoryCandidate.status.in_(["extracted", "queued"]),
                and_(
                    MemoryCandidate.status == "failed",
                    MemoryCandidate.retry_count < max_retry,
                ),
            ),
        )
    ) or 0
