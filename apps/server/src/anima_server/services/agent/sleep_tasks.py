"""Sleep-time background tasks that run during user inactivity.

Includes:
- Contradiction scanning: finds conflicting memory items and resolves them
- Profile updating: synthesizes facts into coherent profile statements
- Episode generation: already handled by episodes.py, invoked here as part of the full suite
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models import MemoryItem
from anima_server.services.agent.memory_store import (
    get_memory_items,
    supersede_memory_item,
    _similarity,
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


@dataclass(slots=True)
class SleepTaskResult:
    contradictions_found: int = 0
    contradictions_resolved: int = 0
    items_merged: int = 0
    episodes_generated: int = 0
    embeddings_backfilled: int = 0
    deep_monologue_ran: bool = False
    errors: list[str] = field(default_factory=list)


async def run_sleep_tasks(
    *,
    user_id: int,
    db_factory: Callable[..., object] | None = None,
) -> SleepTaskResult:
    """Run the full suite of sleep-time maintenance tasks."""
    result = SleepTaskResult()

    # 1. Scan for contradictions
    try:
        cr = await scan_contradictions(user_id=user_id, db_factory=db_factory)
        result.contradictions_found = cr[0]
        result.contradictions_resolved = cr[1]
    except Exception as e:  # noqa: BLE001
        logger.exception("Contradiction scan failed for user %s", user_id)
        result.errors.append(f"contradiction_scan: {e}")

    # 2. Profile synthesis (merge related facts)
    try:
        result.items_merged = await synthesize_profile(user_id=user_id, db_factory=db_factory)
    except Exception as e:  # noqa: BLE001
        logger.exception("Profile synthesis failed for user %s", user_id)
        result.errors.append(f"profile_synthesis: {e}")

    # 3. Episode generation
    try:
        from anima_server.services.agent.episodes import maybe_generate_episode

        episode = await maybe_generate_episode(user_id=user_id, db_factory=db_factory)
        if episode is not None:
            result.episodes_generated = 1
    except Exception as e:  # noqa: BLE001
        logger.exception("Episode generation failed for user %s", user_id)
        result.errors.append(f"episode_generation: {e}")

    # 4. Deep inner monologue (full self-model reflection)
    # Only run once per 24 hours to avoid identity thrashing and LLM cost.
    try:
        from anima_server.services.agent.inner_monologue import run_deep_monologue

        if _should_run_deep_monologue(user_id, db_factory=db_factory):
            monologue = await run_deep_monologue(user_id=user_id, db_factory=db_factory)
            result.deep_monologue_ran = True
            if monologue.errors:
                result.errors.extend(f"monologue: {e}" for e in monologue.errors)
        else:
            logger.debug("Deep monologue skipped for user %s (ran recently)", user_id)
    except Exception as e:  # noqa: BLE001
        logger.debug("Deep monologue skipped: %s", e)

    # 5. Embedding backfill
    try:
        from anima_server.services.agent.embeddings import backfill_embeddings
        from anima_server.db.session import SessionLocal

        factory = db_factory or SessionLocal
        with factory() as db:
            count = await backfill_embeddings(db, user_id=user_id, batch_size=50)
            if count > 0:
                db.commit()
            result.embeddings_backfilled = count
    except Exception as e:  # noqa: BLE001
        logger.debug("Embedding backfill skipped: %s", e)

    return result


async def scan_contradictions(
    *,
    user_id: int,
    db_factory: Callable[..., object] | None = None,
) -> tuple[int, int]:
    """Scan memory items for contradictions within each category. Returns (found, resolved)."""
    from anima_server.db.session import SessionLocal

    factory = db_factory or SessionLocal
    found = 0
    resolved = 0

    for category in ("fact", "preference", "goal", "relationship"):
        with factory() as db:
            items = get_memory_items(
                db, user_id=user_id, category=category, limit=100)
            if len(items) < 2:
                continue

            # Find pairs with moderate similarity (potential conflicts)
            # items are newest-first; swap so item_a=older, item_b=newer
            # to match the contradiction prompt labels ("Memory A (older)")
            pairs: list[tuple[MemoryItem, MemoryItem]] = []
            for i, newer_item in enumerate(items):
                for older_item in items[i + 1:]:
                    sim = _similarity(
                        df(user_id, older_item.content),
                        df(user_id, newer_item.content),
                    )
                    if 0.3 < sim < 0.95:  # Similar but not duplicate
                        pairs.append((older_item, newer_item))

            for item_a, item_b in pairs[:10]:  # Cap per category
                found += 1
                resolution = await _check_contradiction(
                    df(user_id, item_a.content),
                    df(user_id, item_b.content),
                )
                if resolution is None:
                    continue

                verdict = resolution.get("verdict", "COMPATIBLE")
                if verdict != "CONFLICT":
                    continue

                action = resolution.get("action", "KEEP_SECOND")
                merged = resolution.get("merged")

                if action == "KEEP_SECOND":
                    supersede_memory_item(
                        db,
                        old_item_id=item_a.id,
                        new_content=df(user_id, item_b.content),
                        importance=max(item_a.importance, item_b.importance),
                    )
                    resolved += 1
                elif action == "KEEP_FIRST":
                    supersede_memory_item(
                        db,
                        old_item_id=item_b.id,
                        new_content=df(user_id, item_a.content),
                        importance=max(item_a.importance, item_b.importance),
                    )
                    resolved += 1
                elif action == "MERGE" and merged:
                    # Create one merged item, point both old items at it
                    merged_item = supersede_memory_item(
                        db, old_item_id=item_a.id, new_content=merged,
                        importance=max(item_a.importance, item_b.importance),
                    )
                    item_b.superseded_by = merged_item.id
                    item_b.updated_at = datetime.now(UTC)
                    resolved += 1

            db.commit()

    return found, resolved


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
        facts = get_memory_items(
            db, user_id=user_id, category="fact", limit=50)
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
            )
            for item in merge_items[1:]:
                item.superseded_by = merged_item.id
                item.updated_at = datetime.now(UTC)
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
) -> bool:
    """Return True if enough time has passed since the last deep monologue."""
    last = _last_deep_monologue.get(user_id)
    now = datetime.now(UTC)
    if last is not None:
        hours_since = (now - last).total_seconds() / 3600
        if hours_since < _DEEP_MONOLOGUE_INTERVAL_HOURS:
            return False
    _last_deep_monologue[user_id] = now
    return True


async def _check_contradiction(
    content_a: str,
    content_b: str,
) -> dict | None:
    """Ask LLM to check if two memories contradict each other."""
    if settings.agent_provider == "scaffold":
        return None

    try:
        from anima_server.services.agent.llm import create_llm
        from anima_server.services.agent.messages import HumanMessage, SystemMessage
        from anima_server.services.agent.consolidation import _parse_json_array

        llm = create_llm()
        prompt = CONTRADICTION_PROMPT.format(
            memory_a=content_a, memory_b=content_b)
        response = await llm.ainvoke([
            SystemMessage(
                content="You check memory consistency. Respond only with JSON."),
            HumanMessage(content=prompt),
        ])
        content = getattr(response, "content", "")
        if not isinstance(content, str):
            content = str(content)

        # Parse as JSON object
        import json
        text = content.strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            return None
        return json.loads(text[start:end + 1])
    except Exception:  # noqa: BLE001
        logger.exception("Contradiction check failed")
        return None


async def _call_profile_synthesis(facts: list[MemoryItem], *, user_id: int = 0) -> list[dict]:
    """Ask LLM to identify mergeable facts."""
    try:
        from anima_server.services.agent.llm import create_llm
        from anima_server.services.agent.messages import HumanMessage, SystemMessage
        from anima_server.services.agent.consolidation import _parse_json_array

        facts_text = "\n".join(
            f"[id={f.id}] {df(user_id, f.content)}" for f in facts)
        prompt = PROFILE_SYNTHESIS_PROMPT.format(facts=facts_text)

        llm = create_llm()
        response = await llm.ainvoke([
            SystemMessage(
                content="You synthesize user profiles. Respond only with JSON."),
            HumanMessage(content=prompt),
        ])
        content = getattr(response, "content", "")
        if not isinstance(content, str):
            content = str(content)
        return _parse_json_array(content)
    except Exception:  # noqa: BLE001
        logger.exception("Profile synthesis LLM call failed")
        return []
