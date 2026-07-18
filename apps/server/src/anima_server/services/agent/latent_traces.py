"""IL4 latent-trace persistence and crystallization — the soul-store edge
around the pure math in ``inner_life/latent.py``.

Fold/decay/cap are plain DB mutations; crystallization is the ONE LLM
consumer in the IL4 pipeline (one call per crystallizing topic, capped per
sleep run) — see ``docs/prds/presence/inner-life-v1.md`` "IL4" and
"§5 Architecture Rules".
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models import LatentTrace, MemoryItem
from anima_server.services.agent.inner_life.latent import (
    LatentConfig,
    decay_weight,
    fold_weight,
    should_prune,
)
from anima_server.services.agent.llm_json import call_llm_for_json

logger = logging.getLogger(__name__)

# Marks a crystallized memory's provenance the same way pattern_synthesis
# marks PATTERN_SOURCE — read back by MemoryItem.source, not a dedicated
# "origin" column (there isn't one; this IS the origin marker).
CRYSTALLIZED_SOURCE = "latent_crystallization"
_VALID_CRYSTALLIZED_CATEGORIES = frozenset({"fact", "preference", "goal", "relationship"})
_DEFAULT_CRYSTALLIZED_CATEGORY = "fact"
_MAX_EVIDENCE_REFS_PER_TRACE = 50
_DEFAULT_CRYSTALLIZED_IMPORTANCE = 3


def get_latent_config() -> LatentConfig:
    """Build a ``LatentConfig`` from ``Settings`` (mirrors
    ``inner_life.store.get_affect_config``)."""
    return LatentConfig(
        promotion_threshold=settings.latent_promotion_threshold,
        floor_ratio=settings.latent_floor_ratio,
        crystallization_threshold=settings.latent_crystallization_threshold,
        fold_rate=settings.latent_fold_rate,
        weekly_decay=settings.latent_weekly_decay,
        max_traces_per_user=settings.latent_max_traces_per_user,
    )


# ── Fold ─────────────────────────────────────────────────────────────


def evidence_ref_for_candidate(candidate: object) -> dict[str, object]:
    """Build the evidence-ref identifier stored on a trace for one folded
    candidate — identifiers only (candidate id, content hash, source
    message ids), never copied content; see ``LatentTrace`` docstring."""
    observed_at = getattr(candidate, "created_at", None)
    candidate_id = getattr(candidate, "id", None)
    return {
        "candidate_id": int(candidate_id) if candidate_id is not None else None,
        "content_hash": getattr(candidate, "content_hash", None),
        "source_message_ids": [
            int(message_id)
            for message_id in (getattr(candidate, "source_message_ids", None) or [])
            if message_id is not None
        ],
        "observed_at": observed_at.isoformat() if observed_at is not None else None,
        "extractor": getattr(candidate, "extraction_model", None) or getattr(candidate, "source", None),
    }


def fold_candidate_into_trace(
    soul_db: Session,
    *,
    user_id: int,
    candidate: object,
    topic_key: str,
    score: float,
    config: LatentConfig | None = None,
) -> LatentTrace:
    """Upsert-and-fold: additive leaky-integrator accumulation by topic key.

    Same (user_id, topic_key) always resolves to the same row (the unique
    constraint), so duplicate-topic churn accumulates onto one trace instead
    of double-counting across parallel rows.
    """
    cfg = config or get_latent_config()
    now = datetime.now(UTC)

    trace = soul_db.scalar(
        select(LatentTrace).where(
            LatentTrace.user_id == user_id,
            LatentTrace.topic_key == topic_key,
        )
    )
    if trace is None:
        trace = LatentTrace(
            user_id=user_id,
            topic_key=topic_key,
            kind="observation",
            weight=0.0,
            evidence_refs=[],
            first_seen=now,
            last_seen=now,
        )
        soul_db.add(trace)
        soul_db.flush()

    # An active-window repeat merged into one candidate row still counts
    # once per mention (candidate_ops records repeat_count on merge) —
    # bounded so a runaway extractor can't leap a trace to the cap.
    salience = getattr(candidate, "salience_json", None) or {}
    repeats = max(1, min(10, int(salience.get("repeat_count", 1) or 1)))
    for _ in range(repeats):
        trace.weight = fold_weight(trace.weight, score, cfg)
    refs = list(trace.evidence_refs or [])
    refs.append(evidence_ref_for_candidate(candidate))
    # Weight caps at 1.0 but refs would otherwise append forever on a
    # recurring topic that never crystallizes (e.g. scaffold provider) —
    # keep the newest window only.
    trace.evidence_refs = refs[-_MAX_EVIDENCE_REFS_PER_TRACE:]

    # The weekly sweep enforces the per-user cap, but a burst of
    # minor_observation extractions could grow the table well past it
    # between sweeps — enforce opportunistically once clearly over.
    from sqlalchemy import func as _func

    count = soul_db.scalar(
        select(_func.count()).select_from(LatentTrace).where(LatentTrace.user_id == user_id)
    )
    if count is not None and count > 2 * cfg.max_traces_per_user:
        surplus = soul_db.scalars(
            select(LatentTrace)
            .where(LatentTrace.user_id == user_id)
            .order_by(LatentTrace.weight.asc(), LatentTrace.id.asc())
            .limit(int(count) - cfg.max_traces_per_user)
        ).all()
        for stale_trace in surplus:
            if stale_trace.id != trace.id:
                soul_db.delete(stale_trace)
    trace.last_seen = now
    soul_db.flush()
    return trace


# ── Decay + cap ──────────────────────────────────────────────────────


def decay_and_cap_traces(
    soul_db: Session,
    *,
    user_id: int,
    config: LatentConfig | None = None,
) -> dict[str, int]:
    """Weekly leak (``weight *= weekly_decay``), prune sub-floor traces, and
    enforce the per-user cap by dropping the lowest-weight surplus."""
    cfg = config or get_latent_config()

    traces = list(
        soul_db.scalars(
            select(LatentTrace).where(LatentTrace.user_id == user_id)
        ).all()
    )
    decayed = 0
    pruned = 0
    for trace in traces:
        trace.weight = decay_weight(trace.weight, cfg)
        decayed += 1
        if should_prune(trace.weight, cfg):
            soul_db.delete(trace)
            pruned += 1
    soul_db.flush()

    capped = 0
    remaining = list(
        soul_db.scalars(
            select(LatentTrace)
            .where(LatentTrace.user_id == user_id)
            .order_by(LatentTrace.weight.asc(), LatentTrace.id.asc())
        ).all()
    )
    surplus = len(remaining) - cfg.max_traces_per_user
    if surplus > 0:
        for trace in remaining[:surplus]:
            soul_db.delete(trace)
            capped += 1
    if capped:
        soul_db.flush()

    return {"decayed": decayed, "pruned": pruned, "capped": capped}


# ── F7 right-to-forget integration ──────────────────────────────────


def _ref_message_ids(ref: dict[str, object]) -> set[int]:
    ids: set[int] = set()
    for raw in ref.get("source_message_ids") or []:
        try:
            ids.add(int(raw))
        except (TypeError, ValueError):
            continue
    return ids


def scrub_latent_traces_for_forgotten_sources(
    soul_db: Session,
    *,
    user_id: int,
    source_message_ids: Iterable[int],
) -> int:
    """Remove evidence_refs pointing at forgotten sources; delete traces
    left with no surviving evidence.

    Called from ``forgetting.forget_memory`` with the same
    ``source_message_ids`` it already computes for its own derived-reference
    cleanup, so a single-item forget also scrubs any latent trace an
    evidence event of the forgotten memory's sources contributed to.
    """
    forgotten = {int(mid) for mid in source_message_ids if mid is not None}
    if not forgotten:
        return 0

    traces = list(
        soul_db.scalars(
            select(LatentTrace).where(LatentTrace.user_id == user_id)
        ).all()
    )
    scrubbed = 0
    for trace in traces:
        refs = trace.evidence_refs or []
        surviving = [
            ref for ref in refs if not forgotten.intersection(_ref_message_ids(ref))
        ]
        if len(surviving) == len(refs):
            continue
        scrubbed += 1
        if surviving:
            trace.evidence_refs = surviving
        else:
            soul_db.delete(trace)
    soul_db.flush()
    return scrubbed


def forget_latent_traces_by_topic(
    soul_db: Session,
    *,
    user_id: int,
    topic_key: str,
) -> int:
    """Topic-scoped forget: delete the matching trace outright."""
    result = soul_db.execute(
        delete(LatentTrace).where(
            LatentTrace.user_id == user_id,
            LatentTrace.topic_key == topic_key,
        )
    )
    soul_db.flush()
    return int(result.rowcount or 0)


# ── Crystallization ──────────────────────────────────────────────────


def _resolve_trace_evidence(
    trace: LatentTrace,
    *,
    rt_db: Session,
    user_id: int,
) -> tuple[list[str], list[dict[str, object]], int]:
    """Defense-in-depth re-validation (F7): re-fetch each evidence ref's
    source candidate and drop any that no longer resolve — independent of
    (and in addition to) the proactive scrub in ``forget_memory``. Also
    supplies the evidence text for synthesis, since traces themselves never
    store content (see ``LatentTrace`` docstring)."""
    from anima_server.models.runtime_memory import MemoryCandidate

    refs = trace.evidence_refs or []
    texts: list[str] = []
    surviving: list[dict[str, object]] = []
    stale = 0
    for ref in refs:
        candidate_id = ref.get("candidate_id")
        candidate = rt_db.get(MemoryCandidate, int(candidate_id)) if candidate_id is not None else None
        if candidate is None or candidate.user_id != user_id:
            stale += 1
            continue
        # Candidate ids are runtime-local, but traces are soul-store
        # portable: after a vault import into a different runtime, the same
        # numeric id can name an unrelated candidate. The fold path stores
        # the candidate's content_hash in the ref — a mismatch means this
        # is not the evidence that was folded, so treat it as stale.
        # (Refs written before hashes existed carry None and pass — bounded
        # legacy grace; every new fold records the hash.)
        ref_hash = ref.get("content_hash")
        if ref_hash is not None and ref_hash != candidate.content_hash:
            stale += 1
            continue
        content = (candidate.content or "").strip()
        if not content:
            # A ref that can't contribute text can't contribute to
            # synthesis — treat it as stale so an all-contentless trace is
            # deleted via the no-survivors path instead of permanently
            # occupying a per-run crystallization slot.
            stale += 1
            continue
        surviving.append(ref)
        texts.append(content)
    return texts, surviving, stale


def _clean_crystallized_response(
    response: object,
) -> tuple[str, dict[str, object] | None]:
    """Classify the synthesis response.

    Returns ``("too_thin", None)`` only for the LLM's explicit
    ``content: null`` verdict (the prompt's authored "not enough signal"
    answer) — that is a decision and clears the trace. Anything else
    non-conforming is ``("invalid", None)``: transient garbage that must
    NOT consume the trace, so the next sleep run retries.
    """
    if not isinstance(response, dict):
        return "invalid", None
    if "content" in response and response.get("content") is None:
        return "too_thin", None
    content = response.get("content")
    if not isinstance(content, str) or not content.strip():
        return "invalid", None
    category = response.get("category")
    if category not in _VALID_CRYSTALLIZED_CATEGORIES:
        category = _DEFAULT_CRYSTALLIZED_CATEGORY
    try:
        importance = int(response.get("importance", _DEFAULT_CRYSTALLIZED_IMPORTANCE))
    except (TypeError, ValueError):
        importance = _DEFAULT_CRYSTALLIZED_IMPORTANCE
    importance = max(1, min(5, importance))
    return "ok", {"content": content.strip(), "category": category, "importance": importance}


async def _synthesize_and_store_crystallized_memory(
    soul_db: Session,
    *,
    user_id: int,
    topic_key: str,
    evidence_texts: list[str],
    surviving_refs: list[dict[str, object]],
) -> tuple[str, MemoryItem | None]:
    """Synthesize and store one crystallized memory.

    Returns ``(outcome, item)`` where outcome is ``"stored"`` (item +
    evidence rows are staged in the session, ready to commit),
    ``"too_thin"`` (LLM-authored verdict — nothing staged), or
    ``"invalid"`` (unusable response or store miss — the caller must
    roll the session back; a flushed-but-unevidenced MemoryItem must
    never reach a commit).
    """
    from anima_server.services.agent.memory_store import store_memory_item
    from anima_server.services.agent.prompt_loader import PromptLoader
    from anima_server.services.agent.provenance import add_memory_item_evidence

    prompt = PromptLoader.from_db(soul_db, user_id).latent_crystallization(
        topic_key=topic_key,
        evidence="\n".join(f"- {text}" for text in evidence_texts),
    )
    response = await call_llm_for_json(
        "You synthesize a single durable memory from repeated weak signals. "
        "Respond only with strict JSON.",
        prompt,
        expect="object",
    )
    verdict, parsed = _clean_crystallized_response(response)
    if verdict != "ok" or parsed is None:
        return verdict, None

    result = store_memory_item(
        soul_db,
        user_id=user_id,
        content=parsed["content"],
        category=parsed["category"],
        importance=parsed["importance"],
        source=CRYSTALLIZED_SOURCE,
        allow_update=False,
        defer_on_similar=False,
    )
    if result.action in ("conflict", "rejected"):
        # The synthesized aggregate CONTRADICTS (or was rejected against)
        # an established memory. matched_item is the OLD memory — grafting
        # crystallized evidence onto it would misattribute provenance, and
        # retrying forever would burn a crystallization slot every run. The
        # trace is cleared as a decision: established memories win over
        # sub-threshold accumulation (genuine contradictions between real
        # memories are the contradiction scan's job, not this path's).
        return "conflicted", None

    item = result.item or result.matched_item
    if item is None:
        return "invalid", None

    add_memory_item_evidence(
        soul_db,
        user_id=user_id,
        memory_item_id=item.id,
        evidence_text="\n".join(evidence_texts)[:4000],
        source_kind=CRYSTALLIZED_SOURCE,
        speaker="system",
        extractor=CRYSTALLIZED_SOURCE,
        metadata={
            "memory_source": CRYSTALLIZED_SOURCE,
            "topic_key": topic_key,
            "contributing_evidence_refs": surviving_refs,
        },
    )
    return "stored", item


async def crystallize_due_traces(
    *,
    user_id: int,
    db_factory: Callable[..., object] | None = None,
    runtime_db_factory: Callable[..., object] | None = None,
    max_per_run: int = 3,
    config: LatentConfig | None = None,
) -> dict[str, int]:
    """For each topic at/above the crystallization threshold (capped at
    ``max_per_run`` per call to bound LLM cost): re-validate evidence,
    synthesize ONE memory item with full provenance, then clear the topic.

    A trace whose evidence is entirely stale (F7 defense-in-depth — none of
    its refs still resolve) is deleted WITHOUT synthesizing anything.
    """
    if settings.agent_provider == "scaffold":
        return {
            "crystallized": 0,
            "dropped_stale": 0,
            "skipped_empty": 0,
            "too_thin": 0,
            "kept_for_retry": 0,
            "conflicted": 0,
            "requalified": 0,
        }

    from anima_server.db.runtime import get_runtime_session_factory
    from anima_server.db.session import SessionLocal

    factory = db_factory or SessionLocal
    rt_factory = runtime_db_factory or get_runtime_session_factory()
    cfg = config or get_latent_config()

    crystallized = 0
    dropped_stale = 0
    skipped_empty = 0
    too_thin = 0
    conflicted = 0
    requalified = 0
    kept_for_retry = 0

    with factory() as soul_db:
        due = list(
            soul_db.scalars(
                select(LatentTrace)
                .where(
                    LatentTrace.user_id == user_id,
                    LatentTrace.weight >= cfg.crystallization_threshold,
                )
                .order_by(LatentTrace.weight.desc(), LatentTrace.id.asc())
                .limit(max(0, max_per_run))
            ).all()
        )

        for trace in due:
            with rt_factory() as rt_db:
                evidence_texts, surviving_refs, stale = _resolve_trace_evidence(
                    trace, rt_db=rt_db, user_id=user_id,
                )
            dropped_stale += stale

            if not surviving_refs:
                # Defense-in-depth: nothing left resolves — delete without
                # synthesizing anything (never crystallize from evidence
                # the user asked to remove).
                soul_db.delete(trace)
                soul_db.commit()
                continue

            total_refs = len(trace.evidence_refs or [])
            if stale and total_refs:
                # The threshold crossing was partly backed by refs that no
                # longer resolve (vault import, runtime cleanup). Requalify
                # at the survival-scaled weight: if the surviving evidence
                # alone wouldn't have crossed, keep the trace (pruned to
                # survivors) and let future folds rebuild before synthesis.
                effective_weight = trace.weight * (len(surviving_refs) / total_refs)
                if effective_weight < cfg.crystallization_threshold:
                    trace.weight = effective_weight
                    trace.evidence_refs = surviving_refs
                    requalified += 1
                    soul_db.commit()
                    continue

            try:
                outcome, _item = await _synthesize_and_store_crystallized_memory(
                    soul_db,
                    user_id=user_id,
                    topic_key=trace.topic_key,
                    evidence_texts=evidence_texts,
                    surviving_refs=surviving_refs,
                )
            except Exception:
                logger.exception(
                    "Latent trace crystallization failed for user %s topic %s",
                    user_id,
                    trace.topic_key,
                )
                # Discard anything staged mid-synthesis (a flushed
                # MemoryItem without its evidence rows must never reach a
                # commit) and keep the trace — the next sleep run retries.
                soul_db.rollback()
                kept_for_retry += 1
                continue

            if outcome == "stored":
                # Item + evidence rows are staged in this session; deleting
                # the trace and committing persists all three atomically.
                crystallized += 1
                soul_db.delete(trace)
                soul_db.commit()
            elif outcome == "too_thin":
                # LLM-authored verdict: the accumulated evidence cannot
                # support a durable memory. Clearing here is the decision,
                # not an accident of failure handling.
                too_thin += 1
                soul_db.delete(trace)
                soul_db.commit()
            elif outcome == "conflicted":
                # Synthesis clashed with an established memory — nothing
                # stored (see _synthesize_and_store_crystallized_memory);
                # clear the trace so it can't retry into the same wall.
                conflicted += 1
                soul_db.rollback()
                soul_db.delete(trace)
                soul_db.commit()
            else:
                # "invalid": transient garbage (malformed response, store
                # miss). Keep the trace intact for the next run.
                soul_db.rollback()
                kept_for_retry += 1

    return {
        "crystallized": crystallized,
        "dropped_stale": dropped_stale,
        "skipped_empty": skipped_empty,
        "too_thin": too_thin,
        "kept_for_retry": kept_for_retry,
        "conflicted": conflicted,
        "requalified": requalified,
    }
