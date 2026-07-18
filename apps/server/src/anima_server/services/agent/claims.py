"""Structured claims layer for memory deduplication.

Provides canonical slot-based claim storage that mirrors freeform
MemoryItem writes, enabling deterministic dedup by ``canonical_key``
instead of fuzzy text matching.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models import MemoryClaim, MemoryClaimEvidence
from anima_server.services.data_crypto import df, ef

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


def derive_topic_key(content: str, category: str) -> str:
    """Derive the same canonical grouping key ``upsert_claim`` would use,
    without touching the DB.

    Used by IL4 latent-trace folding (``latent_traces.py``). Structured
    matches include the captured VALUE's slug
    (``"user:{namespace}:{slot}:{value_slug}"``): claims group by slot
    alone because one claim per slot is the point (the value updates in
    place), but a latent topic must distinguish values — otherwise
    unrelated weak signals ("likes sushi" / "likes hiking") would
    accumulate weight onto one trace and crystallize from mixed
    evidence. Verb variants over the same value still collapse ("likes
    sushi" / "loves sushi" → the same key). Freeform content falls back
    to a category + content-hash slug. This intentionally skips
    ``upsert_claim``'s DB-backed fuzzy-paraphrase resolution
    (``_find_similar_freeform_claim``) — a pure key derivation has no
    session to query, so near-duplicate paraphrases may still land on
    distinct topic keys; exact-duplicate content always collapses to one.
    """
    stripped = content.strip()
    for pattern, namespace, slot in _SLOT_PATTERNS:
        m = pattern.match(stripped)
        if m:
            return f"user:{namespace}:{slot}:{_content_slug(m.group('v'))}"
    return f"user:{category}:{_content_slug(content)}"


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

    if existing is None and derived is None:
        # The content slug is lossy: paraphrases ("love hiking" vs "enjoy
        # hiking") hash to different keys and would accumulate as parallel
        # active claims.  Resolve against existing same-namespace claims
        # by text similarity and adopt the matched claim's key so the
        # lineage stays on one canonical key.
        existing = _find_similar_freeform_claim(
            db, user_id=user_id, namespace=category, content=content,
        )
        if existing is not None:
            canonical_key = existing.canonical_key
            namespace = existing.namespace
            slot = existing.slot

    if existing is not None:
        # Same value — just add evidence if new
        if (
            df(user_id, existing.value_text, table="memory_items", field="content").strip().lower()
            == content.strip().lower()
        ):
            if evidence_text:
                db.add(
                    MemoryClaimEvidence(
                        claim_id=existing.id,
                        source_text=ef(
                            user_id,
                            evidence_text,
                            table="memory_claim_evidence",
                            field="source_text",
                        ),
                        source_kind=source_kind,
                    )
                )
                # New evidence changes the claim's state, so bump its timestamp:
                # profile-synthesis freshness keys off MemoryClaim.updated_at,
                # and reconcile_profile_from_claims needs to pick up the new
                # source_claim_evidence_id on the next run.
                existing.updated_at = datetime.now(UTC)
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
        value_text=ef(user_id, content.strip(), table="memory_items", field="content"),
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
        db.add(
            MemoryClaimEvidence(
                claim_id=new_claim.id,
                source_text=ef(
                    user_id, evidence_text, table="memory_claim_evidence", field="source_text"
                ),
                source_kind=source_kind,
            )
        )
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
    normalized = content.strip().casefold()
    slug = re.sub(r"[^a-z0-9]+", "_", normalized)
    slug = slug[:max_len].strip("_")
    if slug:
        return slug
    digest = sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"u_{digest}"


# Stricter than analyze_memory_item's 0.4 "similar" threshold because a
# match here supersedes the existing claim rather than just flagging it.
_FREEFORM_SIMILARITY_THRESHOLD = 0.6


def _find_similar_freeform_claim(
    db: Session,
    *,
    user_id: int,
    namespace: str,
    content: str,
) -> MemoryClaim | None:
    """Find an active claim in *namespace* that states the same thing as
    *content*, using the same relation classifier and word-overlap
    similarity the memory store uses for write dedup."""
    from anima_server.services.agent.memory_store import (
        _classify_memory_relation,
        _similarity,
    )

    # Deterministic order so the chosen supersession target doesn't depend
    # on storage order when several candidates match.
    candidates = db.scalars(
        select(MemoryClaim)
        .where(
            MemoryClaim.user_id == user_id,
            MemoryClaim.namespace == namespace,
            MemoryClaim.status == "active",
        )
        .order_by(MemoryClaim.updated_at.desc(), MemoryClaim.id.desc())
    ).all()

    # Rank candidates: a classifier "duplicate"/"update" (same slot or
    # subject) is the strongest signal and beats raw word overlap; among
    # equal-relation matches the most-recently-updated wins (query order).
    # Scan all candidates rather than returning the first so we never
    # supersede a weaker match while a stronger one exists.
    _RELATION_RANK = {"duplicate": 3, "update": 2}
    best: MemoryClaim | None = None
    best_rank = 0.0
    for claim in candidates:
        plaintext = df(
            user_id, claim.value_text, table="memory_items", field="content")
        relation = _classify_memory_relation(plaintext, content, namespace)
        rank = float(_RELATION_RANK.get(relation, 0))
        if rank == 0.0:
            score = _similarity(plaintext, content)
            if score < _FREEFORM_SIMILARITY_THRESHOLD:
                continue
            rank = score  # 0.6..1.0, always below the relation ranks
        if rank > best_rank:
            best = claim
            best_rank = rank
    return best
