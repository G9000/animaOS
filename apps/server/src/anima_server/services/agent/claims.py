"""Structured claims layer for memory deduplication.

Provides canonical slot-based claim storage that mirrors freeform
MemoryItem writes, enabling deterministic dedup by ``canonical_key``
instead of fuzzy text matching.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models import MemoryClaim, MemoryClaimEvidence, MemoryItem

logger = logging.getLogger(__name__)

# Slot-detection patterns reused from memory_store for canonical key derivation
_SLOT_PATTERNS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"^age:\s*(?P<v>.+)$", re.I), "fact", "age"),
    (re.compile(r"^birthday:\s*(?P<v>.+)$", re.I), "fact", "birthday"),
    (re.compile(r"^works as\s+(?P<v>.+)$", re.I), "fact", "occupation"),
    (re.compile(r"^works at\s+(?P<v>.+)$", re.I), "fact", "employer"),
    (re.compile(r"^lives in\s+(?P<v>.+)$", re.I), "fact", "location"),
    (re.compile(r"^(?:name is|name:\s*)(?P<v>.+)$", re.I), "fact", "name"),
    (re.compile(r"^display name:\s*(?P<v>.+)$", re.I), "fact", "display_name"),
    (re.compile(r"^username:\s*(?P<v>.+)$", re.I), "fact", "username"),
    (re.compile(r"^gender:\s*(?P<v>.+)$", re.I), "fact", "gender"),
    # Preferences
    (re.compile(r"^(?:likes|loves?|enjoys?)\s+(?P<v>.+)$", re.I), "preference", "likes"),
    (re.compile(r"^prefers?\s+(?P<v>.+)$", re.I), "preference", "prefers"),
    (re.compile(r"^(?:dislikes?|hates?)\s+(?P<v>.+)$", re.I), "preference", "dislikes"),
)


def derive_canonical_key(
    content: str,
    category: str,
) -> tuple[str, str, str] | None:
    """Try to derive a (namespace, slot, polarity) triple from content.

    Returns ``None`` when no structured slot matches.
    """
    for pattern, ns, slot in _SLOT_PATTERNS:
        m = pattern.match(content.strip())
        if m:
            polarity = "negative" if slot == "dislikes" else "positive"
            return ns, slot, polarity
    # Fallback: use category as namespace and a content-hash as the slot
    return None


def upsert_claim(
    db: Session,
    *,
    user_id: int,
    content: str,
    category: str,
    importance: int = 3,
    source_kind: str = "extraction",
    extractor: str = "llm",
    memory_item_id: int | None = None,
    evidence_text: str | None = None,
) -> MemoryClaim | None:
    """Create or supersede a structured claim for the given content.

    If a canonical key can be derived and an existing claim occupies that
    key, the old claim is marked ``superseded`` and the new claim links
    to it.  Returns the new (or existing unchanged) claim.
    """
    derived = derive_canonical_key(content, category)
    if derived is None:
        # No structured slot — store as generic claim with content-based key
        canonical_key = f"user:{category}:{_content_slug(content)}"
        namespace = category
        slot = _content_slug(content)
        polarity = "positive"
    else:
        namespace, slot, polarity = derived
        canonical_key = f"user:{namespace}:{slot}"

    # Look for existing active claim on this key
    existing = db.scalar(
        select(MemoryClaim).where(
            MemoryClaim.user_id == user_id,
            MemoryClaim.canonical_key == canonical_key,
            MemoryClaim.status == "active",
        )
    )

    if existing is not None:
        # Same value — just add evidence if new
        if existing.value_text.strip().lower() == content.strip().lower():
            if evidence_text:
                db.add(MemoryClaimEvidence(
                    claim_id=existing.id,
                    source_text=evidence_text,
                    source_kind=source_kind,
                ))
                db.flush()
            return existing

        # Different value — supersede
        existing.status = "superseded"
        existing.updated_at = datetime.now(UTC)

    new_claim = MemoryClaim(
        user_id=user_id,
        subject_type="user",
        namespace=namespace,
        slot=slot,
        value_text=content.strip(),
        polarity=polarity,
        confidence=min(1.0, importance / 5.0),
        status="active",
        canonical_key=canonical_key,
        source_kind=source_kind,
        extractor=extractor,
        memory_item_id=memory_item_id,
        superseded_by_id=None,
    )
    db.add(new_claim)
    db.flush()

    # Link superseded claim to the new one
    if existing is not None:
        existing.superseded_by_id = new_claim.id
        db.flush()

    if evidence_text:
        db.add(MemoryClaimEvidence(
            claim_id=new_claim.id,
            source_text=evidence_text,
            source_kind=source_kind,
        ))
        db.flush()

    return new_claim


def get_active_claims(
    db: Session,
    *,
    user_id: int,
    namespace: str | None = None,
) -> list[MemoryClaim]:
    """Return all active claims for a user, optionally filtered by namespace."""
    q = select(MemoryClaim).where(
        MemoryClaim.user_id == user_id,
        MemoryClaim.status == "active",
    )
    if namespace:
        q = q.where(MemoryClaim.namespace == namespace)
    q = q.order_by(MemoryClaim.updated_at.desc())
    return list(db.scalars(q).all())


def _content_slug(content: str, max_len: int = 60) -> str:
    """Derive a short slug from content for use in canonical keys."""
    slug = re.sub(r"[^a-z0-9]+", "_", content.strip().lower())
    return slug[:max_len].strip("_")
