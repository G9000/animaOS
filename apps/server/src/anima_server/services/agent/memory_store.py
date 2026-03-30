from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models import MemoryItem, MemoryItemTag
from anima_server.services.data_crypto import df, ef

# Decay half-life in days — after this many days, recency score halves
_DECAY_HALF_LIFE_DAYS = 14.0
# Weight factors for the retrieval score formula
_WEIGHT_IMPORTANCE = 0.4
_WEIGHT_RECENCY = 0.35
_WEIGHT_ACCESS = 0.25
# Per-category weights for query-aware scoring: (retrieval_weight, query_weight)
_CATEGORY_QUERY_WEIGHTS: dict[str, tuple[float, float]] = {
    "fact": (0.5, 0.5),
    "preference": (0.4, 0.6),
    "goal": (0.7, 0.3),
    "relationship": (0.3, 0.7),
}
_DEFAULT_QUERY_WEIGHTS: tuple[float, float] = (0.5, 0.5)
_WORD_RE = re.compile(r"[a-z0-9']+")
_TOKEN_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "i",
        "me",
        "my",
        "am",
        "is",
        "are",
        "was",
        "were",
        "as",
        "at",
        "in",
        "to",
        "for",
        "of",
        "on",
        "with",
        "and",
        "now",
        "today",
        "currently",
        "actually",
        "really",
        "very",
    }
)
_FACT_SLOT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^age:\s*(?P<value>.+)$", re.IGNORECASE), "age"),
    (re.compile(r"^birthday:\s*(?P<value>.+)$", re.IGNORECASE), "birthday"),
    (re.compile(r"^works as\s+(?P<value>.+)$", re.IGNORECASE), "occupation"),
    (re.compile(r"^works at\s+(?P<value>.+)$", re.IGNORECASE), "employer"),
    (re.compile(r"^lives in\s+(?P<value>.+)$", re.IGNORECASE), "location"),
    (re.compile(r"^(?:name is|name:\s*)(?P<value>.+)$", re.IGNORECASE), "name"),
    (re.compile(r"^display name:\s*(?P<value>.+)$", re.IGNORECASE), "display_name"),
    (re.compile(r"^username:\s*(?P<value>.+)$", re.IGNORECASE), "username"),
    (re.compile(r"^gender:\s*(?P<value>.+)$", re.IGNORECASE), "gender"),
)
_PREFERENCE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^(?:likes|love(?:s)?|enjoy(?:s)?)\s+(?P<value>.+)$", re.IGNORECASE), "positive"),
    (re.compile(r"^prefers?\s+(?P<value>.+)$", re.IGNORECASE), "positive"),
    (re.compile(r"^(?:dislikes?|hate(?:s)?)\s+(?P<value>.+)$", re.IGNORECASE), "negative"),
)


@dataclass(frozen=True, slots=True)
class MemoryWriteAnalysis:
    action: str
    matched_item: MemoryItem | None = None
    similar_items: tuple[MemoryItem, ...] = ()
    reason: str = ""


@dataclass(frozen=True, slots=True)
class MemoryWriteResult:
    action: str
    item: MemoryItem | None = None
    matched_item: MemoryItem | None = None
    similar_items: tuple[MemoryItem, ...] = ()
    reason: str = ""


def add_memory_item(
    db: Session,
    *,
    user_id: int,
    content: str,
    category: str,
    importance: int = 3,
    source: str = "extraction",
    tags: list[str] | None = None,
) -> MemoryItem | None:
    content = _clean_memory_text(content)
    if not content:
        return None

    existing = get_memory_items(db, user_id=user_id, category=category)
    for item in existing:
        if (
            _classify_memory_relation(
                df(user_id, item.content, table="memory_items", field="content"), content, category
            )
            == "duplicate"
        ):
            return None

    memory_item = MemoryItem(
        user_id=user_id,
        content=ef(user_id, content, table="memory_items", field="content"),
        category=category,
        importance=importance,
        source=source,
    )
    if tags:
        memory_item.tags_json = [t.strip().lower() for t in tags if t.strip()]
    db.add(memory_item)
    db.flush()

    if tags:
        _sync_tags(db, item=memory_item, user_id=user_id, tags=tags)

    return memory_item


def analyze_memory_item(
    db: Session,
    *,
    user_id: int,
    content: str,
    category: str,
    similarity_threshold: float = 0.4,
) -> MemoryWriteAnalysis:
    content = _clean_memory_text(content)
    if not content:
        return MemoryWriteAnalysis(action="rejected", reason="empty_content")

    existing = get_memory_items(db, user_id=user_id, category=category)
    similar_items: list[MemoryItem] = []

    for item in existing:
        item_plaintext = df(user_id, item.content, table="memory_items", field="content")
        relation = _classify_memory_relation(item_plaintext, content, category)
        if relation == "duplicate":
            return MemoryWriteAnalysis(
                action="duplicate",
                matched_item=item,
                reason="equivalent_memory",
            )
        if relation == "update":
            return MemoryWriteAnalysis(
                action="update",
                matched_item=item,
                reason="same_slot_new_value",
            )
        if _similarity(item_plaintext, content) >= similarity_threshold:
            similar_items.append(item)

    if similar_items:
        return MemoryWriteAnalysis(
            action="similar",
            similar_items=tuple(similar_items),
            reason="semantic_overlap",
        )

    return MemoryWriteAnalysis(action="add", reason="new_memory")


def store_memory_item(
    db: Session,
    *,
    user_id: int,
    content: str,
    category: str,
    importance: int = 3,
    source: str = "extraction",
    allow_update: bool = False,
    defer_on_similar: bool = False,
    tags: list[str] | None = None,
) -> MemoryWriteResult:
    cleaned_content = _clean_memory_text(content)
    analysis = analyze_memory_item(
        db,
        user_id=user_id,
        content=cleaned_content,
        category=category,
    )

    if analysis.action == "duplicate":
        return MemoryWriteResult(
            action="duplicate",
            matched_item=analysis.matched_item,
            reason=analysis.reason,
        )

    if analysis.action == "update":
        if not allow_update or analysis.matched_item is None:
            return MemoryWriteResult(
                action="conflict",
                matched_item=analysis.matched_item,
                reason=analysis.reason,
            )
        item = supersede_memory_item(
            db,
            old_item_id=analysis.matched_item.id,
            new_content=cleaned_content,
            importance=importance,
        )
        if tags:
            _sync_tags(db, item=item, user_id=user_id, tags=tags)
        return MemoryWriteResult(
            action="superseded",
            item=item,
            matched_item=analysis.matched_item,
            reason=analysis.reason,
        )

    if analysis.action == "similar" and defer_on_similar:
        return MemoryWriteResult(
            action="similar",
            similar_items=analysis.similar_items,
            reason=analysis.reason,
        )

    if analysis.action == "rejected":
        return MemoryWriteResult(action="rejected", reason=analysis.reason)

    item = MemoryItem(
        user_id=user_id,
        content=ef(user_id, cleaned_content, table="memory_items", field="content"),
        category=category,
        importance=importance,
        source=source,
    )
    if tags:
        item.tags_json = [t.strip().lower() for t in tags if t.strip()]
    db.add(item)
    db.flush()

    if tags:
        _sync_tags(db, item=item, user_id=user_id, tags=tags)

    return MemoryWriteResult(
        action="added",
        item=item,
        similar_items=analysis.similar_items,
        reason=analysis.reason,
    )


def get_memory_items(
    db: Session,
    *,
    user_id: int,
    category: str | None = None,
    limit: int = 50,
    active_only: bool = True,
) -> list[MemoryItem]:
    query = select(MemoryItem).where(MemoryItem.user_id == user_id)
    if category is not None:
        query = query.where(MemoryItem.category == category)
    if active_only:
        query = query.where(MemoryItem.superseded_by.is_(None))
    query = query.order_by(MemoryItem.created_at.desc()).limit(limit)
    return list(db.scalars(query).all())


def get_memory_items_scored(
    db: Session,
    *,
    user_id: int,
    category: str | None = None,
    limit: int = 50,
    now: datetime | None = None,
    query_embedding: list[float] | None = None,
) -> list[MemoryItem]:
    """Retrieve memory items ranked by a multi-factor score: importance, recency, access frequency.

    When *query_embedding* is provided, blends the retrieval score with cosine
    similarity to the query using per-category weights.
    """
    from sqlalchemy import or_

    from anima_server.services.agent.forgetting import HEAT_VISIBILITY_FLOOR

    query = select(MemoryItem).where(
        MemoryItem.user_id == user_id,
        MemoryItem.superseded_by.is_(None),
        # Exclude items that have been scored (heat > 0) but decayed below floor.
        # Items with heat == 0.0 or NULL are unscored and should NOT be excluded.
        or_(
            MemoryItem.heat.is_(None),
            MemoryItem.heat == 0.0,
            MemoryItem.heat >= HEAT_VISIBILITY_FLOOR,
        ),
    )
    if category is not None:
        query = query.where(MemoryItem.category == category)
    # Fetch a larger pool, then rank in Python
    pool_limit = min(limit * 3, 200)
    query = query.order_by(MemoryItem.created_at.desc()).limit(pool_limit)
    items = list(db.scalars(query).all())

    if not items:
        return []

    ref_now = now or datetime.now(UTC)
    scored = [(_retrieval_score(item, ref_now), item) for item in items]

    if query_embedding is not None:
        from anima_server.services.agent.embeddings import _parse_embedding, cosine_similarity

        w_retrieval, w_query = _CATEGORY_QUERY_WEIGHTS.get(
            category or "",
            _DEFAULT_QUERY_WEIGHTS,
        )
        blended: list[tuple[float, MemoryItem]] = []
        for base_score, item in scored:
            item_emb = _parse_embedding(item.embedding_json)
            if item_emb is not None:
                sim = cosine_similarity(query_embedding, item_emb)
                # Normalize sim from [-1,1] to [0,1] for blending
                sim_norm = (sim + 1.0) / 2.0
                final = w_retrieval * base_score + w_query * sim_norm
            else:
                final = base_score
            blended.append((final, item))
        blended.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in blended[:limit]]

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[:limit]]


def _retrieval_score(item: MemoryItem, now: datetime) -> float:
    """Return a normalized [0,1] retrieval score based on item heat.

    Uses a sigmoid-like function ``heat / (heat + k)`` to map unbounded
    heat values into [0,1], keeping the output compatible with the
    query-aware blending in ``get_memory_items_scored()``.
    """
    if hasattr(item, "heat") and item.heat is not None and item.heat > 0.0:
        raw = item.heat
    else:
        # Fallback: compute heat on-the-fly for items that haven't been scored yet
        from anima_server.services.agent.heat_scoring import compute_heat

        ref_count = item.reference_count or 0
        raw = compute_heat(
            access_count=ref_count,
            interaction_depth=ref_count,
            last_accessed_at=item.last_referenced_at,
            importance=float(item.importance),
            now=now,
            created_at=item.created_at,
        )
    # Normalize: k chosen so that heat=5 maps to ~0.5 (typical mid-range
    # for a moderately accessed item with average importance).
    return raw / (raw + _HEAT_NORM_K)


# Normalization constant for heat-to-score mapping.  heat/(heat+k) maps
# heat=k to 0.5.  k=5 means a heat of 5 gives a 0.5 score, 10 gives 0.67,
# 20 gives 0.8 — compressing the range for fair blending with query cosine.
_HEAT_NORM_K: float = 5.0


def touch_memory_items(
    db: Session,
    items: list[MemoryItem],
    *,
    now: datetime | None = None,
    runtime_db: Session | None = None,
) -> None:
    """Log memory access to PG (if runtime_db available) for later sync to SQLCipher."""
    if not items:
        return
    ref_now = now or datetime.now(UTC)

    if runtime_db is not None:
        from anima_server.models.runtime_memory import MemoryAccessLog

        for item in items:
            runtime_db.add(MemoryAccessLog(
                user_id=item.user_id,
                memory_item_id=item.id,
                accessed_at=ref_now,
            ))
        runtime_db.flush()
    else:
        # Fallback: direct SQLCipher write (legacy path)
        for item in items:
            item.reference_count = (item.reference_count or 0) + 1
            item.last_referenced_at = ref_now
        db.flush()
        try:
            from anima_server.services.agent.heat_scoring import update_heat_on_access

            update_heat_on_access(db, items, now=ref_now)
        except Exception:
            pass


def supersede_memory_item(
    db: Session,
    *,
    old_item_id: int,
    new_content: str,
    importance: int | None = None,
) -> MemoryItem:
    old_item = db.get(MemoryItem, old_item_id)
    if old_item is None:
        raise ValueError(f"Memory item {old_item_id} not found")

    new_item = MemoryItem(
        user_id=old_item.user_id,
        content=ef(old_item.user_id, new_content, table="memory_items", field="content"),
        category=old_item.category,
        importance=importance if importance is not None else old_item.importance,
        source=old_item.source,
    )
    db.add(new_item)
    db.flush()

    old_item.superseded_by = new_item.id
    old_item.updated_at = datetime.now(UTC)
    db.add(old_item)
    db.flush()

    # Remove superseded item from vector store
    try:
        from anima_server.services.agent.vector_store import delete_memory

        delete_memory(old_item.user_id, item_id=old_item_id, db=db)
    except Exception:
        pass

    return new_item



def get_current_focus(db: Session, *, user_id: int) -> str | None:
    item = db.scalar(
        select(MemoryItem)
        .where(
            MemoryItem.user_id == user_id,
            MemoryItem.category == "focus",
            MemoryItem.superseded_by.is_(None),
        )
        .order_by(MemoryItem.created_at.desc())
        .limit(1)
    )
    if item is None:
        return None
    return df(user_id, item.content, table="memory_items", field="content")


def set_current_focus(
    db: Session,
    *,
    user_id: int,
    focus: str,
) -> MemoryItem:
    focus = _clean_memory_text(focus)
    existing = db.scalar(
        select(MemoryItem)
        .where(
            MemoryItem.user_id == user_id,
            MemoryItem.category == "focus",
            MemoryItem.superseded_by.is_(None),
        )
        .order_by(MemoryItem.created_at.desc())
        .limit(1)
    )
    if existing is not None:
        relation = _classify_memory_relation(
            df(user_id, existing.content, table="memory_items", field="content"), focus, "focus"
        )
        if relation == "duplicate":
            return existing
        return supersede_memory_item(
            db,
            old_item_id=existing.id,
            new_content=focus,
        )

    item = MemoryItem(
        user_id=user_id,
        content=ef(user_id, focus, table="memory_items", field="content"),
        category="focus",
        importance=4,
        source="user",
    )
    db.add(item)
    db.flush()
    return item


def find_similar_items(
    db: Session,
    *,
    user_id: int,
    content: str,
    category: str,
    threshold: float = 0.5,
) -> list[MemoryItem]:
    existing = get_memory_items(db, user_id=user_id, category=category)
    return [
        item
        for item in existing
        if _similarity(df(user_id, item.content, table="memory_items", field="content"), content)
        > threshold
        and _classify_memory_relation(
            df(user_id, item.content, table="memory_items", field="content"), content, category
        )
        != "duplicate"
    ]


def _is_duplicate(existing_content: str, new_content: str) -> bool:
    return (
        _clean_memory_text(existing_content).casefold()
        == _clean_memory_text(new_content).casefold()
    )


def _classify_memory_relation(
    existing_content: str,
    new_content: str,
    category: str,
) -> str:
    normalized_existing = _clean_memory_text(existing_content)
    normalized_new = _clean_memory_text(new_content)

    if not normalized_existing or not normalized_new:
        return "different"
    if normalized_existing.casefold() == normalized_new.casefold():
        return "duplicate"

    if category == "fact":
        existing_slot = _extract_fact_slot(normalized_existing)
        new_slot = _extract_fact_slot(normalized_new)
        if existing_slot is not None and new_slot is not None and existing_slot[0] == new_slot[0]:
            return "duplicate" if existing_slot[1] == new_slot[1] else "update"

    if category == "preference":
        existing_pref = _extract_preference_signal(normalized_existing)
        new_pref = _extract_preference_signal(normalized_new)
        if existing_pref is not None and new_pref is not None and existing_pref[0] == new_pref[0]:
            return "duplicate" if existing_pref[1] == new_pref[1] else "update"

    if category == "focus":
        return (
            "duplicate"
            if _normalize_subject(normalized_existing) == _normalize_subject(normalized_new)
            else "update"
        )

    return "different"


def _similarity(a: str, b: str) -> float:
    """Simple word-overlap (Jaccard) similarity. Returns 0.0-1.0."""
    words_a = set(_tokenize(a))
    words_b = set(_tokenize(b))
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)


def _clean_memory_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" \t\r\n\"'`.,;:!?")


def _tokenize(value: str) -> list[str]:
    return [token for token in _WORD_RE.findall(value.lower()) if token not in _TOKEN_STOPWORDS]


def _normalize_subject(value: str) -> str:
    return " ".join(_tokenize(value))


def _extract_fact_slot(value: str) -> tuple[str, str] | None:
    for pattern, slot in _FACT_SLOT_PATTERNS:
        match = pattern.match(value)
        if match is None:
            continue
        normalized = _normalize_subject(match.group("value"))
        if normalized:
            return slot, normalized
    return None


def _extract_preference_signal(value: str) -> tuple[str, str] | None:
    for pattern, polarity in _PREFERENCE_PATTERNS:
        match = pattern.match(value)
        if match is None:
            continue
        normalized = _normalize_subject(match.group("value"))
        if normalized:
            return normalized, polarity
    return None


# ---------------------------------------------------------------------------
# Tag helpers (Phase 3)
# ---------------------------------------------------------------------------


def _sync_tags(
    db: Session,
    *,
    item: MemoryItem,
    user_id: int,
    tags: list[str],
) -> None:
    """Synchronize junction-table tags with the provided list.

    Also updates item.tags_json for dual-storage consistency.
    """
    clean_tags = sorted({t.strip().lower() for t in tags if t.strip()})
    item.tags_json = clean_tags if clean_tags else None
    for tag_value in clean_tags:
        existing = db.scalar(
            select(MemoryItemTag).where(
                MemoryItemTag.item_id == item.id,
                MemoryItemTag.tag == tag_value,
            )
        )
        if existing is None:
            db.add(
                MemoryItemTag(
                    tag=tag_value,
                    item_id=item.id,
                    user_id=user_id,
                )
            )
    db.flush()


def add_tags_to_item(
    db: Session,
    *,
    item_id: int,
    user_id: int,
    tags: list[str],
) -> list[str]:
    """Add tags to an existing memory item. Returns the new full tag list."""
    item = db.get(MemoryItem, item_id)
    if item is None or item.user_id != user_id:
        return []

    existing_tags = set(item.tags_json or [])
    new_tags = {t.strip().lower() for t in tags if t.strip()}
    merged = sorted(existing_tags | new_tags)

    _sync_tags(db, item=item, user_id=user_id, tags=merged)
    return merged


def get_items_by_tags(
    db: Session,
    *,
    user_id: int,
    tags: list[str],
    match_mode: str = "any",
    limit: int = 50,
) -> list[MemoryItem]:
    """Retrieve memory items filtered by tags.

    match_mode:
        "any" — items matching at least one tag
        "all" — items matching every tag
    """
    clean_tags = [t.strip().lower() for t in tags if t.strip()]
    if not clean_tags:
        return []

    # Query junction table for item IDs matching the tags
    tag_query = select(MemoryItemTag.item_id).where(
        MemoryItemTag.user_id == user_id,
        MemoryItemTag.tag.in_(clean_tags),
    )

    if match_mode == "all":
        from sqlalchemy import func as sa_func

        tag_query = tag_query.group_by(MemoryItemTag.item_id).having(
            sa_func.count(MemoryItemTag.tag.distinct()) >= len(clean_tags)
        )

    item_ids = [row[0] for row in db.execute(tag_query.distinct()).all()]
    if not item_ids:
        return []

    items = list(
        db.scalars(
            select(MemoryItem)
            .where(
                MemoryItem.id.in_(item_ids),
                MemoryItem.superseded_by.is_(None),
            )
            .order_by(MemoryItem.created_at.desc())
            .limit(limit)
        ).all()
    )
    return items


def get_all_tags(db: Session, *, user_id: int) -> list[str]:
    """Return all distinct tags for a user."""
    rows = db.execute(
        select(MemoryItemTag.tag)
        .where(MemoryItemTag.user_id == user_id)
        .distinct()
        .order_by(MemoryItemTag.tag)
    ).all()
    return [row[0] for row in rows]
