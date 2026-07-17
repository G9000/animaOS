"""Intentional forgetting — F7.

Three mechanisms:
1. Passive decay — heat-based visibility floor (items below threshold excluded from retrieval)
2. Active suppression — superseded memories have derived references flagged for regeneration
3. User-initiated forgetting — hard delete with derived-reference cleanup and audit trail
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import delete, event, or_, select
from sqlalchemy.orm import Session

from anima_server.models import (
    ForesightSignal,
    ForgetAuditLog,
    MemoryClaim,
    MemoryClaimEvidence,
    MemoryEpisode,
    MemoryItem,
    MemoryItemEvidence,
    UserProfileField,
    UserProfileFieldEvidence,
)
from anima_server.models.consciousness import SelfModelBlock
from anima_server.services.data_crypto import df

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────
HEAT_VISIBILITY_FLOOR: float = 0.01
SUPERSEDED_DECAY_MULTIPLIER: float = 3.0


# ── Result types ───────────────────────────────────────────────────────


@dataclass(slots=True)
class DerivedReference:
    """A single derived reference found in episodes or self-model blocks."""

    table: str  # "memory_episodes", "self_model_blocks", or "memory_items"
    record_id: int
    section: str | None = None  # for self_model_blocks: growth_log, intentions


@dataclass(slots=True)
class DerivedReferences:
    """Collection of derived references citing a memory."""

    episodes: list[DerivedReference] = field(default_factory=list)
    self_model_blocks: list[DerivedReference] = field(default_factory=list)
    pattern_items: list[DerivedReference] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.episodes) + len(self.self_model_blocks) + len(self.pattern_items)


@dataclass(slots=True)
class ForgetResult:
    """Result of a forget operation."""

    items_forgotten: int = 0
    derived_refs_affected: int = 0
    audit_log_id: int | None = None
    # IL4 right-to-forget integration: latent traces whose evidence_refs
    # were scrubbed or which were deleted outright because forgetting this
    # memory's sources emptied them (PRD IL4 "right-to-forget integration").
    latent_traces_scrubbed: int = 0


@dataclass(frozen=True, slots=True)
class RuntimeProfileForgetContext:
    """Runtime-message fallback context for profile cleanup."""

    message_ids: tuple[int, ...]
    memory_category: str
    content_text: str


@dataclass(slots=True)
class SuppressionResult:
    """Result of a suppression operation."""

    memory_id: int = 0
    superseded_by: int = 0
    derived_refs_flagged: int = 0
    audit_log_id: int | None = None


# ── Derived reference detection ───────────────────────────────────────


def find_derived_references(
    db: Session,
    *,
    memory_content: str,
    user_id: int,
    exclude_memory_item_ids: Iterable[int] | None = None,
) -> DerivedReferences:
    """Search for the memory's content in episodes and self-model blocks.

    Uses substring matching against:
    - memory_episodes.summary
    - self_model_blocks.content WHERE section IN ('growth_log', 'intentions')
    - pattern memory items and evidence that cite stale source episodes
    """
    refs = DerivedReferences()

    if not memory_content or len(memory_content) < 3:
        return refs

    # Search episodes
    episodes = list(
        db.scalars(
            select(MemoryEpisode).where(
                MemoryEpisode.user_id == user_id,
            )
        ).all()
    )
    memory_content_lower = memory_content.lower()
    for ep in episodes:
        summary = df(user_id, ep.summary, table="memory_episodes", field="summary")
        if memory_content_lower in summary.lower():
            refs.episodes.append(
                DerivedReference(
                    table="memory_episodes",
                    record_id=ep.id,
                )
            )

    # Search self-model blocks (growth_log and intentions sections)
    blocks = list(
        db.scalars(
            select(SelfModelBlock).where(
                SelfModelBlock.user_id == user_id,
                SelfModelBlock.section.in_(["growth_log", "intentions"]),
            )
        ).all()
    )
    for block in blocks:
        content = df(user_id, block.content, table="self_model_blocks", field="content")
        if memory_content_lower in content.lower():
            refs.self_model_blocks.append(
                DerivedReference(
                    table="self_model_blocks",
                    record_id=block.id,
                    section=block.section,
                )
            )

    _find_pattern_references(
        db,
        refs=refs,
        memory_content_lower=memory_content_lower,
        user_id=user_id,
        exclude_memory_item_ids=set(exclude_memory_item_ids or ()),
    )

    return refs


def _find_pattern_references(
    db: Session,
    *,
    refs: DerivedReferences,
    memory_content_lower: str,
    user_id: int,
    exclude_memory_item_ids: set[int],
) -> None:
    from anima_server.services.agent.pattern_synthesis import PATTERN_CATEGORY, PATTERN_SOURCE

    stale_episode_ids = {ref.record_id for ref in refs.episodes}
    seen_pattern_ids: set[int] = set()

    def add_pattern_ref(item_id: int) -> None:
        if item_id in exclude_memory_item_ids or item_id in seen_pattern_ids:
            return
        seen_pattern_ids.add(item_id)
        refs.pattern_items.append(
            DerivedReference(
                table="memory_items",
                record_id=item_id,
                section=PATTERN_CATEGORY,
            )
        )

    pattern_items = list(
        db.scalars(
            select(MemoryItem).where(
                MemoryItem.user_id == user_id,
                MemoryItem.category == PATTERN_CATEGORY,
                MemoryItem.source == PATTERN_SOURCE,
                MemoryItem.superseded_by.is_(None),
            )
        ).all()
    )
    for item in pattern_items:
        if item.id in exclude_memory_item_ids:
            continue
        content = df(user_id, item.content, table="memory_items", field="content")
        if memory_content_lower in content.lower():
            add_pattern_ref(item.id)

    pattern_evidence = list(
        db.scalars(
            select(MemoryItemEvidence)
            .join(MemoryItem, MemoryItemEvidence.memory_item_id == MemoryItem.id)
            .where(
                MemoryItemEvidence.user_id == user_id,
                MemoryItem.category == PATTERN_CATEGORY,
                MemoryItem.source == PATTERN_SOURCE,
                MemoryItem.superseded_by.is_(None),
            )
        ).all()
    )
    for evidence in pattern_evidence:
        item_id = int(evidence.memory_item_id)
        if item_id in exclude_memory_item_ids or item_id in seen_pattern_ids:
            continue
        evidence_text = df(
            user_id,
            evidence.evidence_text,
            table="memory_item_evidence",
            field="evidence_text",
        )
        if memory_content_lower in evidence_text.lower():
            add_pattern_ref(item_id)
            continue
        if stale_episode_ids & _metadata_source_episode_ids(evidence.metadata_json):
            add_pattern_ref(item_id)


def _metadata_source_episode_ids(metadata: dict[str, object] | None) -> set[int]:
    if not isinstance(metadata, dict):
        return set()
    raw_ids = metadata.get("source_episode_ids")
    if not isinstance(raw_ids, list):
        return set()
    parsed_ids: set[int] = set()
    for raw_id in raw_ids:
        try:
            parsed = int(raw_id)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            parsed_ids.add(parsed)
    return parsed_ids


def redact_derived_references(
    db: Session,
    *,
    refs: DerivedReferences,
    strategy: str = "flag_for_regeneration",
) -> int:
    """Process derived references using the specified strategy.

    Strategies:
    - flag_for_regeneration: set needs_regeneration=True on affected records
    - immediate_redact: replace the citation text with '[redacted]'
    """
    count = 0
    pattern_cleanup_ids_by_user: dict[int, list[int]] = {}

    for ep_ref in refs.episodes:
        episode = db.get(MemoryEpisode, ep_ref.record_id)
        if episode is None:
            continue
        if strategy == "flag_for_regeneration":
            episode.needs_regeneration = True
        # immediate_redact not needed for episodes (flag is sufficient)
        count += 1

    for block_ref in refs.self_model_blocks:
        block = db.get(SelfModelBlock, block_ref.record_id)
        if block is None:
            continue
        if strategy == "flag_for_regeneration":
            block.needs_regeneration = True
        elif strategy == "immediate_redact":
            from anima_server.services.agent.soul_blocks import full_replace_soul_block

            full_replace_soul_block(
                db,
                user_id=block.user_id,
                section=block.section,
                content="[redacted]",
                updated_by="forgetting",
            )
        count += 1

    for pattern_ref in refs.pattern_items:
        item = db.get(MemoryItem, pattern_ref.record_id)
        if item is None:
            continue
        pattern_cleanup_ids_by_user.setdefault(int(item.user_id), []).append(int(item.id))
        db.execute(
            delete(MemoryItemEvidence).where(
                MemoryItemEvidence.user_id == item.user_id,
                MemoryItemEvidence.memory_item_id == item.id,
            )
        )
        db.delete(item)
        count += 1

    if count > 0:
        db.flush()
    for user_id, item_ids in pattern_cleanup_ids_by_user.items():
        _schedule_forget_external_cleanup_after_commit(
            db,
            user_id=user_id,
            item_ids=item_ids,
        )
    return count


# ── Active suppression ─────────────────────────────────────────────────


def suppress_memory(
    db: Session,
    *,
    memory_id: int,
    superseded_by: int,
    user_id: int,
) -> SuppressionResult:
    """Handle suppression when a memory is superseded.

    1. Find derived references citing this memory
    2. Flag them for regeneration
    3. Record suppression event in forget_audit_log
    """
    result = SuppressionResult(memory_id=memory_id, superseded_by=superseded_by)

    # Get the memory content for derived ref search
    memory = db.get(MemoryItem, memory_id)
    if memory is None:
        return result

    from anima_server.services.data_crypto import df

    content = df(user_id, memory.content, table="memory_items", field="content")

    # Find and flag derived references
    refs = find_derived_references(
        db,
        memory_content=content,
        user_id=user_id,
        exclude_memory_item_ids=(memory_id, superseded_by),
    )
    if refs.total > 0:
        result.derived_refs_flagged = redact_derived_references(
            db,
            refs=refs,
            strategy="flag_for_regeneration",
        )

    # Record audit log
    log = ForgetAuditLog(
        user_id=user_id,
        forgotten_at=datetime.now(UTC),
        trigger="suppression",
        scope="single",
        items_forgotten=0,  # suppression does not delete
        derived_refs_affected=result.derived_refs_flagged,
    )
    db.add(log)
    db.flush()
    result.audit_log_id = log.id

    return result


# ── IL4 right-to-forget integration ────────────────────────────────────


def _scrub_latent_traces_for_forget(
    db: Session,
    *,
    user_id: int,
    source_message_ids: Iterable[int],
) -> int:
    """Scrub latent-trace evidence_refs pointing at a forgotten memory's
    sources (PRD IL4 "right-to-forget integration" — binding, P1 review
    finding). A trace left with no surviving evidence is deleted outright.
    """
    from anima_server.services.agent.latent_traces import (
        scrub_latent_traces_for_forgotten_sources,
    )

    return scrub_latent_traces_for_forgotten_sources(
        db,
        user_id=user_id,
        source_message_ids=source_message_ids,
    )


def forget_latent_traces_for_topic(
    db: Session,
    *,
    user_id: int,
    topic_key: str,
) -> int:
    """Topic-scoped forget: delete the latent trace for ``topic_key``
    outright (PRD IL4 — topic-scoped forget deletes matching topic_key
    traces, distinct from the source-based scrub single-item forgets do).

    ``topic_key`` is the same structural key
    ``claims.derive_topic_key``/the IL4 fold path uses — callers doing a
    topic-scoped forget (today: ``forget_by_topic`` search + a per-item
    ``forget_memory`` confirm loop) derive it from the topic they are
    forgetting and pass it here alongside the memory-item deletions.
    """
    from anima_server.services.agent.latent_traces import (
        forget_latent_traces_by_topic,
    )

    count = forget_latent_traces_by_topic(db, user_id=user_id, topic_key=topic_key)
    if count:
        log = ForgetAuditLog(
            user_id=user_id,
            forgotten_at=datetime.now(UTC),
            trigger="user_request",
            scope="topic",
            items_forgotten=0,
            derived_refs_affected=count,
        )
        db.add(log)
        db.flush()
    return count


def purge_latent_traces_matching_topic(
    db: Session,
    *,
    user_id: int,
    topic: str,
) -> int:
    """Delete every latent trace whose topic_key contains the topic's slug.

    This is the user-facing topic-scoped trace forget (PRD IL4): fold-only
    topics — weak signals that never promoted or crystallized any
    confirmable MemoryItem — have no per-item confirm path, so an explicit
    topic purge is the only way a user can remove them. Traces are
    sub-threshold signals, never surfaced as memories, so immediate
    deletion on an explicit request is proportionate. Audited without
    recording the topic content.
    """
    from anima_server.models.agent_runtime import LatentTrace
    from anima_server.services.agent.claims import _content_slug

    slug = _content_slug(topic)
    if not slug:
        return 0
    traces = db.scalars(
        select(LatentTrace).where(
            LatentTrace.user_id == user_id,
            LatentTrace.topic_key.contains(slug),
        )
    ).all()
    for trace in traces:
        db.delete(trace)
    if traces:
        db.add(
            ForgetAuditLog(
                user_id=user_id,
                forgotten_at=datetime.now(UTC),
                trigger="user_request",
                scope="topic",
                items_forgotten=0,
                derived_refs_affected=len(traces),
            )
        )
        db.flush()
    return len(traces)


# ── User-initiated forgetting ─────────────────────────────────────────


def forget_memory(
    db: Session,
    *,
    memory_id: int,
    user_id: int,
    trigger: str = "user_request",
    runtime_db_factory: Callable[[], Session] | None = None,
) -> ForgetResult:
    """Hard-delete a memory item with full cleanup.

    1. Find derived references (episodes, growth_log, intentions)
    2. Flag derived references for regeneration
    3. Delete associated MemoryClaim + MemoryClaimEvidence records
    4. Hard-delete the memory item
    5. Remove embedding from vector store
    6. Invalidate BM25 index
    7. Record forget event in audit log (without content)
    """
    result = ForgetResult()

    memory = db.get(MemoryItem, memory_id)
    if memory is None or memory.user_id != user_id:
        return result

    from anima_server.services.data_crypto import df

    df(user_id, memory.content, table="memory_items", field="content")

    # 1. Walk the full supersession chain (A→B→C: forgetting C must
    #    also remove B and A, otherwise ON DELETE SET NULL resurrects them).
    chain_ids = [memory_id]
    chain_items = [memory]
    frontier = [memory_id]
    while frontier:
        preds = list(
            db.scalars(select(MemoryItem).where(MemoryItem.superseded_by.in_(frontier))).all()
        )
        frontier = [p.id for p in preds]
        for pred in preds:
            chain_ids.append(pred.id)
            chain_items.append(pred)

    # 2. Find and flag derived references for ALL items in the chain
    for item in chain_items:
        item_content = df(user_id, item.content, table="memory_items", field="content")
        refs = find_derived_references(
            db,
            memory_content=item_content,
            user_id=user_id,
            exclude_memory_item_ids=chain_ids,
        )
        if refs.total > 0:
            result.derived_refs_affected += redact_derived_references(
                db,
                refs=refs,
                strategy="flag_for_regeneration",
            )

    # 3. Delete associated claims and evidence for ALL items in the chain
    all_claims = list(
        db.scalars(
            select(MemoryClaim).where(
                MemoryClaim.memory_item_id.in_(chain_ids),
            )
        ).all()
    )
    claim_ids = [claim.id for claim in all_claims]
    claim_evidence_ids = (
        list(
            db.scalars(
                select(MemoryClaimEvidence.id).where(
                    MemoryClaimEvidence.claim_id.in_(claim_ids),
                )
            ).all()
        )
        if claim_ids
        else []
    )
    memory_evidence = list(
        db.scalars(
            select(MemoryItemEvidence).where(
                MemoryItemEvidence.user_id == user_id,
                MemoryItemEvidence.memory_item_id.in_(chain_ids),
            )
        ).all()
    )
    memory_evidence_ids = [evidence.id for evidence in memory_evidence]
    evidence_by_item_id: dict[int, list[MemoryItemEvidence]] = {}
    for evidence in memory_evidence:
        evidence_by_item_id.setdefault(evidence.memory_item_id, []).append(evidence)
    source_message_ids = sorted(
        {
            message_id
            for evidence in memory_evidence
            for message_id in _runtime_message_ids_for_memory_evidence(evidence)
        }
    )
    runtime_contexts = _runtime_profile_forget_contexts(
        user_id=user_id,
        chain_items=chain_items,
        evidence_by_item_id=evidence_by_item_id,
    )
    _delete_foresight_signals_for_forget(
        db,
        user_id=user_id,
        source_message_ids=source_message_ids,
        forgotten_texts=(
            df(user_id, item.content, table="memory_items", field="content")
            for item in chain_items
        ),
    )
    result.latent_traces_scrubbed = _scrub_latent_traces_for_forget(
        db,
        user_id=user_id,
        source_message_ids=source_message_ids,
    )
    # Also delete traces for the forgotten items' own topics: forgetting a
    # confirmed memory about X must take the latent buffer for X with it,
    # not just the refs that shared source messages. The fold lane writes
    # minor_observation-namespaced keys, so probe that namespace too.
    from anima_server.services.agent.claims import derive_topic_key
    from anima_server.services.agent.latent_traces import forget_latent_traces_by_topic

    for item in chain_items:
        content_plain = df(user_id, item.content, table="memory_items", field="content")
        for category in {item.category, "minor_observation"}:
            topic_key = derive_topic_key(content_plain, category)
            result.latent_traces_scrubbed += forget_latent_traces_by_topic(
                db, user_id=user_id, topic_key=topic_key
            )
    _delete_profile_fields_for_forget(
        db,
        user_id=user_id,
        source_memory_ids=chain_ids,
        source_evidence_ids=memory_evidence_ids,
        source_claim_evidence_ids=claim_evidence_ids,
        runtime_contexts=runtime_contexts,
    )
    _schedule_profile_candidate_rejection_after_commit(
        db,
        user_id=user_id,
        runtime_contexts=runtime_contexts,
        runtime_db_factory=runtime_db_factory,
    )
    for claim in all_claims:
        db.execute(
            delete(MemoryClaimEvidence).where(
                MemoryClaimEvidence.claim_id == claim.id,
            )
        )
        db.delete(claim)
    db.execute(
        delete(MemoryItemEvidence).where(
            MemoryItemEvidence.user_id == user_id,
            MemoryItemEvidence.memory_item_id.in_(chain_ids),
        )
    )

    # 4. Hard-delete all items in the chain
    for item in chain_items:
        db.delete(item)
    db.flush()
    result.items_forgotten = len(chain_items)

    # 5. Remove external indexes only after the SQLCipher transaction commits.
    _schedule_forget_external_cleanup_after_commit(
        db,
        user_id=user_id,
        item_ids=chain_ids,
    )

    # 7. Record audit log (no content stored)
    log = ForgetAuditLog(
        user_id=user_id,
        forgotten_at=datetime.now(UTC),
        trigger=trigger,
        scope="single",
        items_forgotten=result.items_forgotten,
        derived_refs_affected=result.derived_refs_affected,
    )
    db.add(log)
    db.flush()
    result.audit_log_id = log.id

    return result


def _delete_foresight_signals_for_forget(
    db: Session,
    *,
    user_id: int,
    source_message_ids: Iterable[int],
    forgotten_texts: Iterable[str],
) -> int:
    forgotten_source_ids = _coerce_message_id_set(source_message_ids)
    if not forgotten_source_ids:
        return 0
    forgotten_tokens = _combined_meaningful_tokens(forgotten_texts)
    if not forgotten_tokens:
        return 0

    deleted = 0
    signals = list(
        db.scalars(
            select(ForesightSignal).where(ForesightSignal.user_id == user_id)
        ).all()
    )
    for signal in signals:
        signal_source_ids = _coerce_message_id_set(signal.source_message_ids_json)
        if not signal_source_ids.intersection(forgotten_source_ids):
            continue
        if not _foresight_signal_matches_forgotten_text(
            user_id=user_id,
            signal=signal,
            forgotten_tokens=forgotten_tokens,
        ):
            continue
        db.delete(signal)
        deleted += 1
    return deleted


def _coerce_message_id_set(raw_ids: Iterable[object] | None) -> set[int]:
    ids: set[int] = set()
    for raw_id in raw_ids or []:
        if raw_id is None:
            continue
        with suppress(TypeError, ValueError):
            ids.add(int(raw_id))
    return ids


def _foresight_signal_matches_forgotten_text(
    *,
    user_id: int,
    signal: ForesightSignal,
    forgotten_tokens: set[str],
) -> bool:
    signal_text = " ".join(
        filter(
            None,
            [
                df(
                    user_id,
                    signal.content,
                    table="foresight_signals",
                    field="content",
                ),
                df(
                    user_id,
                    signal.evidence,
                    table="foresight_signals",
                    field="evidence",
                ),
                signal.relative_text or "",
            ],
        )
    )
    signal_tokens = _meaningful_relation_tokens(signal_text)
    shared = forgotten_tokens & signal_tokens
    return len(shared) >= 2 and any(len(token) >= 5 for token in shared)


def _combined_meaningful_tokens(texts: Iterable[str]) -> set[str]:
    tokens: set[str] = set()
    for text in texts:
        tokens.update(_meaningful_relation_tokens(text))
    return tokens


def _delete_profile_fields_for_forget(
    db: Session,
    *,
    user_id: int,
    source_memory_ids: list[int],
    source_evidence_ids: list[int],
    source_claim_evidence_ids: list[int],
    runtime_contexts: list[RuntimeProfileForgetContext],
) -> int:
    field_criteria = [
        UserProfileField.source_memory_id.in_(source_memory_ids),
    ]
    evidence_criteria = [
        UserProfileFieldEvidence.source_memory_id.in_(source_memory_ids),
    ]
    if source_evidence_ids:
        field_criteria.append(
            UserProfileField.source_evidence_id.in_(source_evidence_ids)
        )
        evidence_criteria.append(
            UserProfileFieldEvidence.source_evidence_id.in_(source_evidence_ids)
        )
    if source_claim_evidence_ids:
        field_criteria.append(
            UserProfileField.source_claim_evidence_id.in_(source_claim_evidence_ids)
        )
        evidence_criteria.append(
            UserProfileFieldEvidence.source_claim_evidence_id.in_(
                source_claim_evidence_ids,
            )
        )
    matching_evidence = list(
        db.scalars(
            select(UserProfileFieldEvidence).where(
                UserProfileFieldEvidence.user_id == user_id,
                or_(*evidence_criteria),
            )
        ).all()
    )
    seen_evidence_ids = {evidence.id for evidence in matching_evidence}
    for evidence in _matching_runtime_profile_evidence(
        db,
        user_id=user_id,
        runtime_contexts=runtime_contexts,
    ):
        if evidence.id in seen_evidence_ids:
            continue
        matching_evidence.append(evidence)
        seen_evidence_ids.add(evidence.id)
    matching_evidence_by_field: dict[int, list[UserProfileFieldEvidence]] = {}
    for evidence in matching_evidence:
        matching_evidence_by_field.setdefault(evidence.profile_field_id, []).append(
            evidence
        )
    field_ids = set(matching_evidence_by_field)
    field_ids.update(
        db.scalars(
            select(UserProfileField.id).where(
                UserProfileField.user_id == user_id,
                or_(*field_criteria),
            )
        ).all()
    )
    if not field_ids:
        return 0

    now = datetime.now(UTC)
    deleted_count = 0
    for field_id in sorted(field_ids):
        field = db.get(UserProfileField, field_id)
        if field is None or field.user_id != user_id:
            continue
        matching_ids = [
            evidence.id for evidence in matching_evidence_by_field.get(field_id, [])
        ]
        surviving_query = select(UserProfileFieldEvidence).where(
            UserProfileFieldEvidence.user_id == user_id,
            UserProfileFieldEvidence.profile_field_id == field.id,
        )
        if matching_ids:
            surviving_query = surviving_query.where(
                ~UserProfileFieldEvidence.id.in_(matching_ids)
            )
        surviving_evidence_rows = list(
            db.scalars(surviving_query.order_by(UserProfileFieldEvidence.id.desc())).all()
        )
        if not surviving_evidence_rows:
            evidence_to_delete = {
                evidence.id: evidence
                for evidence in matching_evidence_by_field.get(field_id, [])
            }
            for evidence in db.scalars(
                select(UserProfileFieldEvidence).where(
                    UserProfileFieldEvidence.user_id == user_id,
                    UserProfileFieldEvidence.profile_field_id == field.id,
                )
            ).all():
                evidence_to_delete[evidence.id] = evidence
            for evidence in evidence_to_delete.values():
                db.delete(evidence)
            _restore_previous_profile_field_before_delete(
                db,
                user_id=user_id,
                replacement_field=field,
                now=now,
            )
            db.delete(field)
            deleted_count += 1
            continue

        surviving_evidence = surviving_evidence_rows[0]
        for evidence in matching_evidence_by_field.get(field_id, []):
            db.delete(evidence)
        observed_values = [
            evidence.observed_at
            for evidence in surviving_evidence_rows
            if evidence.observed_at is not None
        ]
        field.source_kind = surviving_evidence.source_kind
        field.source_memory_id = surviving_evidence.source_memory_id
        field.source_evidence_id = surviving_evidence.source_evidence_id
        field.source_claim_evidence_id = surviving_evidence.source_claim_evidence_id
        field.first_observed_at = min(observed_values) if observed_values else None
        field.last_observed_at = max(observed_values) if observed_values else None
        field.updated_at = now
    db.flush()
    return deleted_count


def _restore_previous_profile_field_before_delete(
    db: Session,
    *,
    user_id: int,
    replacement_field: UserProfileField,
    now: datetime,
) -> None:
    previous_fields = list(
        db.scalars(
            select(UserProfileField)
            .where(
                UserProfileField.user_id == user_id,
                UserProfileField.category == replacement_field.category,
                UserProfileField.key == replacement_field.key,
                UserProfileField.superseded_by_id == replacement_field.id,
            )
            .order_by(UserProfileField.updated_at.desc(), UserProfileField.id.desc())
        ).all()
    )
    restored = False
    for previous in previous_fields:
        previous.superseded_by_id = None
        if restored or previous.status != "superseded":
            continue
        has_surviving_evidence = db.scalar(
            select(UserProfileFieldEvidence.id)
            .where(
                UserProfileFieldEvidence.user_id == user_id,
                UserProfileFieldEvidence.profile_field_id == previous.id,
            )
            .limit(1)
        )
        if has_surviving_evidence is None:
            continue
        previous.status = "active"
        previous.updated_at = now
        restored = True


def _runtime_profile_forget_contexts(
    *,
    user_id: int,
    chain_items: list[MemoryItem],
    evidence_by_item_id: dict[int, list[MemoryItemEvidence]],
) -> list[RuntimeProfileForgetContext]:
    contexts: list[RuntimeProfileForgetContext] = []
    for item in chain_items:
        message_ids = sorted(
            {
                message_id
                for evidence in evidence_by_item_id.get(item.id, [])
                for message_id in _runtime_message_ids_for_memory_evidence(evidence)
            }
        )
        if not message_ids:
            continue
        content_text = df(
            user_id,
            item.content,
            table="memory_items",
            field="content",
        ).strip()
        if not content_text:
            continue
        contexts.append(
            RuntimeProfileForgetContext(
                message_ids=tuple(message_ids),
                memory_category=item.category,
                content_text=content_text,
            )
        )
    return contexts


def _matching_runtime_profile_evidence(
    db: Session,
    *,
    user_id: int,
    runtime_contexts: list[RuntimeProfileForgetContext],
) -> list[UserProfileFieldEvidence]:
    contexts_by_message_id: dict[int, list[RuntimeProfileForgetContext]] = {}
    for context in runtime_contexts:
        for message_id in context.message_ids:
            contexts_by_message_id.setdefault(message_id, []).append(context)
    if not contexts_by_message_id:
        return []

    candidates = list(
        db.scalars(
            select(UserProfileFieldEvidence).where(
                UserProfileFieldEvidence.user_id == user_id,
                UserProfileFieldEvidence.runtime_message_id.in_(
                    sorted(contexts_by_message_id)
                ),
            )
        ).all()
    )
    matched: list[UserProfileFieldEvidence] = []
    for evidence in candidates:
        if evidence.runtime_message_id is None:
            continue
        field = db.get(UserProfileField, evidence.profile_field_id)
        if field is None or field.user_id != user_id:
            continue
        if _runtime_profile_evidence_matches_context(
            user_id=user_id,
            field=field,
            evidence=evidence,
            contexts=contexts_by_message_id.get(evidence.runtime_message_id, []),
        ):
            matched.append(evidence)
    return matched


def _runtime_profile_evidence_matches_context(
    *,
    user_id: int,
    field: UserProfileField,
    evidence: UserProfileFieldEvidence,
    contexts: list[RuntimeProfileForgetContext],
) -> bool:
    profile_texts = [
        df(
            user_id,
            field.value_text,
            table="user_profile_fields",
            field="value_text",
        ),
        df(
            user_id,
            evidence.evidence_text,
            table="user_profile_field_evidence",
            field="evidence_text",
        ),
    ]
    for context in contexts:
        if not _profile_category_can_derive_from_memory_category(
            profile_category=field.category,
            memory_category=context.memory_category,
        ):
            continue
        if any(
            _texts_are_related(
                context.content_text,
                text,
                profile_category=field.category,
                profile_key=field.key,
            )
            for text in profile_texts
        ):
            return True
    return False


def _profile_category_can_derive_from_memory_category(
    *,
    profile_category: str,
    memory_category: str,
) -> bool:
    category_map = {
        "preference": {"preferences"},
        "goal": {"goals"},
        "relationship": {"relationships"},
        "focus": {"active_projects"},
    }
    allowed = category_map.get(memory_category)
    if allowed is None:
        return True
    return profile_category in allowed


def _texts_are_related(
    source: str,
    target: str,
    *,
    profile_category: str = "",
    profile_key: str = "",
) -> bool:
    source_text = _normalize_relation_text(source)
    target_text = _normalize_relation_text(target)
    if not source_text or not target_text:
        return False

    source_tokens = _meaningful_relation_tokens(source_text)
    target_tokens = _meaningful_relation_tokens(target_text)
    shared = source_tokens & target_tokens
    if len(shared) >= 2:
        return True
    if (
        shared
        and (source_text in target_text or target_text in source_text)
        and len(source_tokens) >= 2
    ):
        if len(target_tokens) >= 2:
            return True
        return _profile_metadata_supports_relation(
            source_tokens,
            profile_category=profile_category,
            profile_key=profile_key,
        )
    if any(token.isdigit() or len(token) >= 5 for token in shared):
        return _profile_metadata_supports_relation(
            source_tokens,
            profile_category=profile_category,
            profile_key=profile_key,
        )
    return False


def _profile_metadata_supports_relation(
    source_tokens: set[str],
    *,
    profile_category: str,
    profile_key: str,
) -> bool:
    metadata_tokens = _meaningful_relation_tokens(f"{profile_category} {profile_key}")
    metadata_tokens.update(
        _PROFILE_RELATION_SOURCE_ALIASES.get((profile_category, profile_key), set())
    )
    return bool(source_tokens & metadata_tokens)


_RELATION_TOKEN_RE = re.compile(r"[a-z0-9]+")
_PROFILE_RELATION_SOURCE_ALIASES: dict[tuple[str, str], set[str]] = {
    ("identity", "location"): {
        "based",
        "live",
        "lives",
        "living",
        "located",
        "location",
        "reside",
        "resides",
    },
}
_RELATION_STOP_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "am",
    "for",
    "i",
    "in",
    "im",
    "is",
    "my",
    "of",
    "on",
    "the",
    "to",
}


def _normalize_relation_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def _meaningful_relation_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for token in _RELATION_TOKEN_RE.findall(text):
        normalized = token
        if len(normalized) > 3 and normalized.endswith("s"):
            normalized = normalized[:-1]
        if normalized in _RELATION_STOP_WORDS:
            continue
        tokens.add(normalized)
    return tokens


def _runtime_message_ids_for_memory_evidence(evidence: MemoryItemEvidence) -> list[int]:
    ids: list[int] = []
    if evidence.runtime_message_id is not None:
        ids.append(int(evidence.runtime_message_id))
    for raw_id in evidence.runtime_message_ids_json or []:
        if raw_id is None:
            continue
        ids.append(int(raw_id))
    return ids


def _schedule_profile_candidate_rejection_after_commit(
    db: Session,
    *,
    user_id: int,
    runtime_contexts: list[RuntimeProfileForgetContext],
    runtime_db_factory: Callable[[], Session] | None,
) -> None:
    if runtime_db_factory is None:
        return
    message_ids = tuple(
        sorted(
            {
                message_id
                for context in runtime_contexts
                for message_id in context.message_ids
            }
        )
    )
    if not message_ids:
        return

    contexts = tuple(runtime_contexts)

    def _cleanup(_session: Session) -> None:
        with suppress(Exception):
            event.remove(db, "after_rollback", _discard)

        try:
            with runtime_db_factory() as runtime_db:
                _reject_profile_candidates_for_forget_contexts(
                    runtime_db,
                    user_id=user_id,
                    runtime_contexts=list(contexts),
                )
                runtime_db.commit()
        except Exception:
            logger.debug(
                "Profile candidate rejection failed for messages %s",
                message_ids,
                exc_info=True,
            )

    def _discard(_session: Session) -> None:
        with suppress(Exception):
            event.remove(db, "after_commit", _cleanup)

    event.listen(db, "after_commit", _cleanup, once=True)
    event.listen(db, "after_rollback", _discard, once=True)


def _reject_profile_candidates_for_forget_contexts(
    runtime_db: Session,
    *,
    user_id: int,
    runtime_contexts: list[RuntimeProfileForgetContext],
) -> int:
    from anima_server.models.runtime_memory import ProfileUpdateCandidate

    contexts_by_message_id: dict[int, list[RuntimeProfileForgetContext]] = {}
    for context in runtime_contexts:
        for message_id in context.message_ids:
            contexts_by_message_id.setdefault(message_id, []).append(context)
    if not contexts_by_message_id:
        return 0

    candidates = list(
        runtime_db.scalars(
            select(ProfileUpdateCandidate).where(
                ProfileUpdateCandidate.user_id == user_id,
                ProfileUpdateCandidate.status.in_(["extracted", "queued", "failed"]),
            )
        ).all()
    )
    now = datetime.now(UTC)
    rejected = 0
    for candidate in candidates:
        matched_contexts = [
            context
            for message_id in _source_message_ids_for_profile_candidate(candidate)
            for context in contexts_by_message_id.get(message_id, [])
        ]
        if not matched_contexts:
            continue
        if not _runtime_profile_candidate_matches_context(
            candidate=candidate,
            contexts=matched_contexts,
        ):
            continue
        candidate.status = "rejected"
        candidate.last_error = "Source memory forgotten before profile promotion"
        candidate.processed_at = now
        rejected += 1
    if rejected:
        runtime_db.flush()
    return rejected


def _source_message_ids_for_profile_candidate(candidate: object) -> list[int]:
    ids: list[int] = []
    for raw_id in getattr(candidate, "source_message_ids", None) or []:
        if raw_id is None:
            continue
        ids.append(int(raw_id))
    return ids


def _runtime_profile_candidate_matches_context(
    *,
    candidate: object,
    contexts: list[RuntimeProfileForgetContext],
) -> bool:
    profile_category = str(getattr(candidate, "category", "") or "")
    profile_texts = [
        str(getattr(candidate, "value", "") or ""),
        str(getattr(candidate, "evidence_text", "") or ""),
    ]
    for context in contexts:
        if not _profile_category_can_derive_from_memory_category(
            profile_category=profile_category,
            memory_category=context.memory_category,
        ):
            continue
        if any(
            _texts_are_related(
                context.content_text,
                text,
                profile_category=profile_category,
                profile_key=str(getattr(candidate, "key", "") or ""),
            )
            for text in profile_texts
        ):
            return True
    return False


def _schedule_forget_external_cleanup_after_commit(
    db: Session,
    *,
    user_id: int,
    item_ids: list[int],
) -> None:
    cleanup_ids = tuple(int(item_id) for item_id in item_ids)
    if not cleanup_ids:
        return

    def _cleanup(_session: Session) -> None:
        with suppress(Exception):
            event.remove(db, "after_rollback", _discard)

        all_removed = True
        try:
            from anima_server.services.agent.memory_store import (
                invalidate_memory_retrieval_indexes,
                remove_memory_item_from_retrieval_index_by_id,
            )

            for item_id in cleanup_ids:
                if not remove_memory_item_from_retrieval_index_by_id(
                    user_id=user_id,
                    item_id=item_id,
                ):
                    all_removed = False
            invalidate_memory_retrieval_indexes(user_id, mark_dirty=not all_removed)
        except Exception:
            logger.debug("Memory retrieval index cleanup failed for chain %s", cleanup_ids)

        try:
            from anima_server.services.agent.vector_store import delete_memory

            for item_id in cleanup_ids:
                delete_memory(user_id, item_id=item_id)
        except Exception:
            logger.debug("Vector store cleanup failed for chain %s", cleanup_ids)

    def _discard(_session: Session) -> None:
        with suppress(Exception):
            event.remove(db, "after_commit", _cleanup)

    event.listen(db, "after_commit", _cleanup, once=True)
    event.listen(db, "after_rollback", _discard, once=True)


def forget_by_topic(
    db: Session,
    *,
    topic: str,
    user_id: int,
) -> list[MemoryItem]:
    """Find memories matching a topic and return them as candidates for confirmation.

    Does NOT auto-delete. Returns the list of matching MemoryItem objects
    so the caller (API layer) can present them for user confirmation.
    """
    candidates: list[MemoryItem] = []

    # Use keyword search against all active items
    from anima_server.services.data_crypto import df

    items = list(
        db.scalars(
            select(MemoryItem).where(
                MemoryItem.user_id == user_id,
                MemoryItem.superseded_by.is_(None),
            )
        ).all()
    )

    topic_lower = topic.lower()
    for item in items:
        plaintext = df(user_id, item.content, table="memory_items", field="content")
        if topic_lower in plaintext.lower():
            candidates.append(item)

    # Also try BM25 search for lexical matches beyond substring
    try:
        from anima_server.services.agent.bm25_index import bm25_search

        bm25_results = bm25_search(user_id, query=topic, limit=20, db=db)
        keyword_ids = {item.id for item in candidates}
        for item_id, _score in bm25_results:
            if item_id not in keyword_ids:
                item = db.get(MemoryItem, item_id)
                if item is not None and item.superseded_by is None:
                    candidates.append(item)
                    keyword_ids.add(item_id)
    except Exception:
        logger.debug("BM25 search unavailable for topic forget")

    return candidates
