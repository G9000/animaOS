"""Sleep-time background tasks that run during user inactivity.

Includes:
- Contradiction scanning: finds conflicting memory items and resolves them
- Profile updating: synthesizes facts into coherent profile statements
- Episode generation: already handled by episodes.py, invoked here as part of the full suite
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from anima_server.config import settings
from anima_server.models import MemoryItem
from anima_server.services.agent.memory_store import (
    _similarity,
    get_memory_items,
    invalidate_memory_retrieval_indexes,
    remove_memory_item_from_retrieval_index,
    supersede_memory_item,
)
from anima_server.services.data_crypto import df

logger = logging.getLogger(__name__)

CONTRADICTION_PROMPT = """You are a memory consistency checker for a personal AI companion.

Given two memory items about the same user that might conflict, determine:
1. Do they contradict each other? (CONFLICT / COMPATIBLE)
2. If CONFLICT, which one is more likely current/correct? (KEEP_FIRST / KEEP_SECOND / MERGE)
3. If MERGE, provide the merged content.

Return JSON:
{{"verdict": "CONFLICT" or "COMPATIBLE", "action": "KEEP_FIRST" or "KEEP_SECOND" or "MERGE", "merged": "merged content if MERGE, else null"}}

Memory A (older): {memory_a}
Memory B (newer): {memory_b}"""

PROFILE_SYNTHESIS_PROMPT = """You are a memory system for a personal AI companion.

Given these facts about a user, identify any that could be combined into a single, more complete statement.
Only combine facts that are clearly about the same topic.

Return a JSON array of objects:
[{{"old_ids": [id1, id2], "merged": "combined statement"}}]

Return [] if no facts should be combined.

Facts:
{facts}"""


def _pair_hash(content_a: str, content_b: str) -> str:
    """Order-normalized identity of a checked pair, derived from content.

    Editing either item changes its content hash, which naturally
    invalidates every cached verdict the item participated in.
    """
    hash_a = hashlib.sha256(content_a.strip().lower().encode()).hexdigest()
    hash_b = hashlib.sha256(content_b.strip().lower().encode()).hexdigest()
    low, high = sorted((hash_a, hash_b))
    return hashlib.sha256(f"{low}:{high}".encode()).hexdigest()


def _load_checked_pair_hashes(
    runtime_db_factory: Callable[..., object] | None,
    *,
    user_id: int,
) -> set[str]:
    """Previously-checked pair hashes; empty set when the cache is unavailable."""
    from anima_server.models.runtime_memory import ContradictionCheck

    try:
        if runtime_db_factory is None:
            from anima_server.db.runtime import get_runtime_session_factory

            runtime_db_factory = get_runtime_session_factory()
        with runtime_db_factory() as rt_db:
            return set(
                rt_db.scalars(
                    select(ContradictionCheck.pair_hash).where(
                        ContradictionCheck.user_id == user_id
                    )
                ).all()
            )
    except Exception:
        logger.debug("Contradiction verdict cache unavailable", exc_info=True)
        return set()


def _record_checked_pair(
    runtime_db_factory: Callable[..., object] | None,
    *,
    user_id: int,
    pair_hash: str,
    verdict: str,
) -> None:
    from anima_server.models.runtime_memory import ContradictionCheck

    try:
        if runtime_db_factory is None:
            from anima_server.db.runtime import get_runtime_session_factory

            runtime_db_factory = get_runtime_session_factory()
        with runtime_db_factory() as rt_db:
            rt_db.add(
                ContradictionCheck(
                    user_id=user_id,
                    pair_hash=pair_hash,
                    verdict=verdict[:16],
                )
            )
            rt_db.commit()
    except Exception:
        # A concurrent scan may have inserted the same pair; losing one
        # cache write only costs one future re-check.
        logger.debug("Failed to record contradiction verdict", exc_info=True)


async def scan_contradictions(
    *,
    user_id: int,
    db_factory: Callable[..., object] | None = None,
    runtime_db_factory: Callable[..., object] | None = None,
) -> tuple[int, int]:
    """Scan memory items for contradictions within each category. Returns (found, resolved).

    Verdicts are persisted per content-hash pair: stable pairs are checked
    once ever instead of re-buying up to 40 identical LLM calls per cycle.
    """
    from anima_server.db.session import SessionLocal

    factory = db_factory or SessionLocal
    found = 0
    resolved = 0
    checked_pairs = _load_checked_pair_hashes(runtime_db_factory, user_id=user_id)

    for category in ("fact", "preference", "goal", "relationship"):
        with factory() as db:
            items = get_memory_items(db, user_id=user_id, category=category, limit=100)
            if len(items) < 2:
                continue

            # Decrypt each item once — the pair loop is O(n²) and used to
            # re-decrypt both sides per comparison.
            plaintext: dict[int, str] = {
                item.id: df(user_id, item.content, table="memory_items", field="content")
                for item in items
            }

            # Find pairs with moderate similarity (potential conflicts)
            # items are newest-first; swap so item_a=older, item_b=newer
            # to match the contradiction prompt labels ("Memory A (older)")
            pairs: list[tuple[MemoryItem, MemoryItem]] = []
            for i, newer_item in enumerate(items):
                for older_item in items[i + 1 :]:
                    sim = _similarity(
                        plaintext[older_item.id],
                        plaintext[newer_item.id],
                    )
                    if 0.3 < sim < 0.95:  # Similar but not duplicate
                        pairs.append((older_item, newer_item))

            resolved_ids: set[int] = set()
            # Verdicts checked this category, persisted only AFTER the soul
            # commit below succeeds.  Recording a pair as "checked" before the
            # resolution is durable would strand the contradiction: a failed
            # commit loses the supersede, yet the pair is never re-examined.
            pending_records: list[tuple[str, str]] = []
            for item_a, item_b in pairs[:10]:  # Cap per category
                # Skip if either side was already superseded in this scan
                if item_a.id in resolved_ids or item_b.id in resolved_ids:
                    continue
                pair_key = _pair_hash(plaintext[item_a.id], plaintext[item_b.id])
                if pair_key in checked_pairs:
                    continue
                found += 1
                resolution = await _check_contradiction(
                    plaintext[item_a.id],
                    plaintext[item_b.id],
                )
                if resolution is None:
                    continue

                verdict = resolution.get("verdict", "COMPATIBLE")
                # Suppress re-checking within this run immediately (avoid a
                # second LLM call for the same pair this pass).
                checked_pairs.add(pair_key)
                if verdict != "CONFLICT":
                    # COMPATIBLE is terminal — no memory change needed, so it is
                    # safe to persist immediately (post-commit) as checked.
                    pending_records.append((pair_key, str(verdict)))
                    continue

                action = resolution.get("action", "KEEP_SECOND")
                merged = resolution.get("merged")

                resolved_this_pair = False
                if action == "KEEP_SECOND":
                    # Mark A as superseded by B (no new row needed)
                    item_a.superseded_by = item_b.id
                    item_a.updated_at = datetime.now(UTC)
                    item_b.importance = max(item_a.importance, item_b.importance)
                    _cleanup_superseded_indexes(user_id, item_a.id, db)
                    _suppress_after_contradiction(db, item_a.id, item_b.id, user_id)
                    resolved_ids.add(item_a.id)
                    resolved_this_pair = True
                elif action == "KEEP_FIRST":
                    # Mark B as superseded by A (no new row needed)
                    item_b.superseded_by = item_a.id
                    item_b.updated_at = datetime.now(UTC)
                    item_a.importance = max(item_a.importance, item_b.importance)
                    _cleanup_superseded_indexes(user_id, item_b.id, db)
                    _suppress_after_contradiction(db, item_b.id, item_a.id, user_id)
                    resolved_ids.add(item_b.id)
                    resolved_this_pair = True
                elif action == "MERGE" and merged:
                    # Create one merged item, point both old items at it
                    merged_item = supersede_memory_item(
                        db,
                        old_item_id=item_a.id,
                        new_content=merged,
                        importance=max(item_a.importance, item_b.importance),
                        evidence_text=merged,
                        evidence_source_kind="maintenance_merge",
                        evidence_metadata={"memory_source": "contradiction_merge"},
                    )
                    item_b.superseded_by = merged_item.id
                    item_b.updated_at = datetime.now(UTC)
                    _cleanup_superseded_indexes(user_id, item_b.id, db)
                    _suppress_after_contradiction(db, item_a.id, merged_item.id, user_id)
                    _suppress_after_contradiction(db, item_b.id, merged_item.id, user_id)
                    resolved_ids.add(item_a.id)
                    resolved_ids.add(item_b.id)
                    resolved_this_pair = True

                # Only persist a CONFLICT verdict once a branch actually resolved
                # it.  An unresolvable verdict (MERGE without merged content, or
                # an unsupported action) must NOT be cached, or the still-active
                # contradiction would be skipped forever until an item's content
                # changes.
                if resolved_this_pair:
                    pending_records.append((pair_key, str(verdict)))
                    resolved += 1

            db.commit()

        # Only now that the resolutions are durable, persist the per-pair
        # verdicts so stable pairs are skipped in future cycles.  If the commit
        # above raised, we never get here and the pairs are re-checked next
        # cycle rather than being silently marked resolved.
        for pair_hash, verdict in pending_records:
            _record_checked_pair(
                runtime_db_factory,
                user_id=user_id,
                pair_hash=pair_hash,
                verdict=verdict,
            )

    return found, resolved


def _cleanup_superseded_indexes(user_id: int, item_id: int, db: Any) -> None:
    """Remove a superseded item from vector, keyword, and Rust retrieval indexes."""
    try:
        from anima_server.services.agent.vector_store import delete_memory

        delete_memory(user_id, item_id=item_id, db=db)
    except Exception:
        logger.debug("Vector cleanup failed for superseded item %d", item_id)
    try:
        item = db.get(MemoryItem, item_id)
        removed = True
        if item is not None:
            removed = remove_memory_item_from_retrieval_index(item)
        invalidate_memory_retrieval_indexes(user_id, mark_dirty=not removed)
    except Exception:
        logger.debug("Retrieval index cleanup failed for user %d", user_id)


def _suppress_after_contradiction(
    db: Any,
    loser_id: int,
    winner_id: int,
    user_id: int,
) -> None:
    """Flag derived references for the losing item in a contradiction."""
    try:
        from anima_server.services.agent.forgetting import suppress_memory

        suppress_memory(db, memory_id=loser_id, superseded_by=winner_id, user_id=user_id)
    except Exception:
        logger.debug("Suppress failed for contradiction loser %d", loser_id)


async def synthesize_profile(
    *,
    user_id: int,
    db_factory: Callable[..., object] | None = None,
) -> int:
    """Find and merge related facts into more complete statements. Returns merge count."""
    if settings.agent_provider == "scaffold":
        return 0

    from anima_server.db.session import SessionLocal

    factory = db_factory or SessionLocal
    merged_count = 0

    with factory() as db:
        facts = get_memory_items(db, user_id=user_id, category="fact", limit=50)
        if len(facts) < 2:
            return 0

        merges = await _call_profile_synthesis(facts, user_id=user_id)
        for merge in merges:
            old_ids = merge.get("old_ids", [])
            merged_content = merge.get("merged", "")
            if not merged_content or len(old_ids) < 2:
                continue

            # Find the actual items
            merge_items = [f for f in facts if f.id in old_ids]
            if len(merge_items) < 2:
                continue

            max_importance = max(item.importance for item in merge_items)
            # Create one merged item from the first, point remaining at it
            merged_item = supersede_memory_item(
                db,
                old_item_id=merge_items[0].id,
                new_content=merged_content,
                importance=max_importance,
                evidence_text=merged_content,
                evidence_source_kind="maintenance_merge",
                evidence_metadata={"memory_source": "profile_synthesis_merge"},
            )
            for item in merge_items[1:]:
                item.superseded_by = merged_item.id
                item.updated_at = datetime.now(UTC)
                _cleanup_superseded_indexes(user_id, item.id, db)
                _suppress_after_contradiction(db, item.id, merged_item.id, user_id)
            merged_count += 1

        if merged_count > 0:
            db.commit()

    return merged_count


_DEEP_MONOLOGUE_INTERVAL_HOURS = 24
_last_deep_monologue: dict[int, datetime] = {}


def _should_run_deep_monologue(
    user_id: int,
    *,
    db_factory: Callable[..., object] | None = None,
    runtime_db_factory: Callable[..., object] | None = None,
) -> bool:
    """Return True if enough time has passed since the last deep monologue.

    Does NOT update the timestamp — call ``mark_deep_monologue_done()``
    after the monologue succeeds.

    The process-local dict is only a fast path: on a fresh process (this is
    a desktop app — restarts are frequent) the gate is recovered from the
    newest completed ``deep_monologue`` RuntimeBackgroundTaskRun so a
    restart does not re-arm the most expensive reflection.
    """
    del db_factory
    last = _last_deep_monologue.get(user_id)
    if last is None:
        last = _last_completed_deep_monologue_at(
            user_id, runtime_db_factory=runtime_db_factory
        )
        if last is not None:
            _last_deep_monologue[user_id] = last
    if last is not None:
        now = datetime.now(UTC)
        hours_since = (now - last).total_seconds() / 3600
        if hours_since < _DEEP_MONOLOGUE_INTERVAL_HOURS:
            return False
    return True


def _last_completed_deep_monologue_at(
    user_id: int,
    *,
    runtime_db_factory: Callable[..., object] | None = None,
) -> datetime | None:
    """Completion time of the newest successful deep-monologue task run."""
    from sqlalchemy import desc, select

    from anima_server.models.runtime import RuntimeBackgroundTaskRun

    try:
        if runtime_db_factory is None:
            from anima_server.db.runtime import get_runtime_session_factory

            runtime_db_factory = get_runtime_session_factory()
        with runtime_db_factory() as rt_db:
            run = rt_db.scalar(
                select(RuntimeBackgroundTaskRun)
                .where(
                    RuntimeBackgroundTaskRun.user_id == user_id,
                    RuntimeBackgroundTaskRun.task_type == "deep_monologue",
                    RuntimeBackgroundTaskRun.status == "completed",
                )
                .order_by(desc(RuntimeBackgroundTaskRun.completed_at))
                .limit(1)
            )
    except Exception:
        logger.debug(
            "Could not read deep-monologue gate from task runs", exc_info=True
        )
        return None
    if run is None or run.completed_at is None:
        return None
    if (run.result_json or {}).get("errors"):
        # Task completed but the monologue itself failed — let it run again.
        return None
    completed = run.completed_at
    if completed.tzinfo is None:
        completed = completed.replace(tzinfo=UTC)
    return completed


def mark_deep_monologue_done(user_id: int) -> None:
    """Record that a deep monologue completed successfully."""
    _last_deep_monologue[user_id] = datetime.now(UTC)


async def _check_contradiction(
    content_a: str,
    content_b: str,
) -> dict | None:
    """Ask LLM to check if two memories contradict each other."""
    if settings.agent_provider == "scaffold":
        return None

    try:
        from anima_server.services.agent.llm_json import call_llm_for_json
        from anima_server.services.agent.prompt_loader import PromptLoader

        prompt_loader = PromptLoader(agent_name="Anima")
        prompt = prompt_loader.contradiction_check(
            memory_a=content_a,
            memory_b=content_b,
        )
        parsed = await call_llm_for_json(
            "You check memory consistency. Respond only with JSON.",
            prompt,
        )
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        logger.exception("Contradiction check failed")
        return None


async def _call_profile_synthesis(facts: list[MemoryItem], *, user_id: int = 0) -> list[dict]:
    """Ask LLM to identify mergeable facts."""
    try:
        from anima_server.services.agent.llm_json import call_llm_for_json
        from anima_server.services.agent.prompt_loader import PromptLoader

        facts_text = "\n".join(
            f"[id={f.id}] {df(user_id, f.content, table='memory_items', field='content')}"
            for f in facts
        )
        prompt_loader = PromptLoader(agent_name="Anima")
        prompt = prompt_loader.profile_synthesis(facts=facts_text)

        parsed = await call_llm_for_json(
            "You synthesize user profiles. Respond only with JSON.",
            prompt,
            expect="array",
        )
        return parsed if isinstance(parsed, list) else []
    except Exception:
        logger.exception("Profile synthesis LLM call failed")
        return []
