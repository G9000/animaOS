"""MemoryCandidate creation and query helpers."""
from __future__ import annotations

import hashlib
import logging

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from anima_server.models.runtime_memory import MemoryCandidate

logger = logging.getLogger(__name__)

_VALID_CATEGORIES = frozenset({"fact", "preference", "goal", "relationship"})
_VALID_SOURCES = frozenset({"regex", "llm", "predict_calibrate", "tool", "feedback"})
_VALID_IMPORTANCE_SOURCES = frozenset({
    "regex", "llm", "predict_calibrate", "user_explicit", "correction",
})


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

    # Explicit dedup check — works on both PG (with partial unique index) and SQLite.
    existing = runtime_db.scalar(
        select(MemoryCandidate.id).where(
            MemoryCandidate.content_hash == content_hash,
            MemoryCandidate.status.not_in(["rejected", "superseded", "failed"]),
        )
    )
    if existing is not None:
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
    )
    try:
        with runtime_db.begin_nested():
            runtime_db.add(candidate)
            runtime_db.flush()
        return candidate
    except IntegrityError:
        return None


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
