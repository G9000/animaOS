from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from sqlalchemy import select, text

from anima_server.config import settings
from anima_server.services.data_crypto import df
from anima_server.services.health.event_logger import emit as health_emit

logger = logging.getLogger(__name__)

_background_tasks_lock = Lock()
_background_tasks: set[asyncio.Task[None]] = set()

# Memory extraction and conflict check prompts are now in Jinja2 templates.
# Use PromptLoader.memory_extraction(), PromptLoader.conflict_check(), and PromptLoader.batch_conflict_check() instead.


@dataclass(frozen=True, slots=True)
class ExtractedTurnMemory:
    facts: tuple[str, ...] = ()
    preferences: tuple[str, ...] = ()
    current_focus: str | None = None


@dataclass(frozen=True, slots=True)
class PatternExtractor:
    pattern: re.Pattern[str]
    formatter: Callable[[str], str]


@dataclass(slots=True)
class PendingOpsConsolidationResult:
    processed_ids: list[int] = field(default_factory=list)
    failed_ids: list[int] = field(default_factory=list)
    skipped_ids: list[int] = field(default_factory=list)


_FACT_EXTRACTORS: tuple[PatternExtractor, ...] = (
    PatternExtractor(
        pattern=re.compile(r"\bI am (?P<value>\d{1,3}) years old\b", re.IGNORECASE),
        formatter=lambda value: f"Age: {value}",
    ),
    PatternExtractor(
        pattern=re.compile(r"\bmy birthday is (?P<value>[^.?!\n]+)", re.IGNORECASE),
        formatter=lambda value: f"Birthday: {value}",
    ),
    PatternExtractor(
        pattern=re.compile(r"\bI work as (?P<value>[^.?!\n]+)", re.IGNORECASE),
        formatter=lambda value: f"Works as {value}",
    ),
    PatternExtractor(
        pattern=re.compile(r"\bI work at (?P<value>[^.?!\n]+)", re.IGNORECASE),
        formatter=lambda value: f"Works at {value}",
    ),
    PatternExtractor(
        pattern=re.compile(r"\bI live in (?P<value>[^.?!\n]+)", re.IGNORECASE),
        formatter=lambda value: f"Lives in {value}",
    ),
)
_PREFERENCE_EXTRACTORS: tuple[PatternExtractor, ...] = (
    PatternExtractor(
        pattern=re.compile(
            r"\bI (?:really )?(?:like|love|enjoy) (?P<value>[^.?!\n]+)",
            re.IGNORECASE,
        ),
        formatter=lambda value: f"Likes {value}",
    ),
    PatternExtractor(
        pattern=re.compile(r"\bI prefer (?P<value>[^.?!\n]+)", re.IGNORECASE),
        formatter=lambda value: f"Prefers {value}",
    ),
    PatternExtractor(
        pattern=re.compile(
            r"\bI (?:(?:do not|don't) like|dislike|hate) (?P<value>[^.?!\n]+)",
            re.IGNORECASE,
        ),
        formatter=lambda value: f"Dislikes {value}",
    ),
)
_CURRENT_FOCUS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bmy current focus is (?P<value>[^.?!\n]+)", re.IGNORECASE),
    re.compile(r"\bmy main focus is (?P<value>[^.?!\n]+)", re.IGNORECASE),
    re.compile(r"\bmy main priority is (?P<value>[^.?!\n]+)", re.IGNORECASE),
    re.compile(r"\bI(?:'m| am) focused on (?P<value>[^.?!\n]+)", re.IGNORECASE),
    re.compile(r"\bI need to focus on (?P<value>[^.?!\n]+)", re.IGNORECASE),
)


# DEPRECATED: Use run_soul_writer() instead. This function bypasses Soul Writer's
# journal and idempotency checks. Kept only for direct testing.
async def consolidate_pending_ops(
    *,
    user_id: int,
    soul_db_factory: Callable[..., object],
    runtime_db_factory: Callable[..., object],
) -> PendingOpsConsolidationResult:
    # DEPRECATED: Use run_soul_writer() instead. This function bypasses Soul Writer's
    # journal and idempotency checks. Kept only for direct testing.
    """Promote pending runtime memory ops into the soul store."""
    from anima_server.models import PendingMemoryOp
    from anima_server.services.agent.pending_ops import get_pending_ops
    from anima_server.services.agent.soul_blocks import (
        append_to_soul_block,
        full_replace_soul_block,
        replace_in_soul_block,
    )

    result = PendingOpsConsolidationResult()
    runtime_db = runtime_db_factory()
    soul_db = soul_db_factory()
    now = datetime.now(UTC)

    try:
        bind = runtime_db.get_bind()
        if bind.dialect.name == "postgresql":
            runtime_db.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": user_id},
            )

        pending_ops = get_pending_ops(runtime_db, user_id=user_id)
        if not pending_ops:
            return result

        for op in pending_ops:
            if op.source_tool_call_id:
                dedup_filters = [
                    PendingMemoryOp.user_id == user_id,
                    PendingMemoryOp.source_tool_call_id == op.source_tool_call_id,
                    PendingMemoryOp.consolidated.is_(True),
                    PendingMemoryOp.id != op.id,
                ]
                # Scope by run to avoid false matches on non-unique call IDs
                # (e.g. fallback IDs like "tool-call-0" can repeat across runs)
                if op.source_run_id is not None:
                    dedup_filters.append(PendingMemoryOp.source_run_id == op.source_run_id)
                duplicate = runtime_db.scalar(
                    select(PendingMemoryOp.id).where(*dedup_filters)
                )
                if duplicate is not None:
                    op.consolidated = True
                    op.consolidated_at = now
                    result.skipped_ids.append(op.id)
                    continue

            if op.op_type == "append":
                append_to_soul_block(
                    soul_db,
                    user_id=user_id,
                    section=op.target_block,
                    content=op.content,
                )
            elif op.op_type == "replace":
                replaced = replace_in_soul_block(
                    soul_db,
                    user_id=user_id,
                    section=op.target_block,
                    old_content=op.old_content or "",
                    new_content=op.content,
                )
                if replaced is None:
                    op.failed = True
                    op.failure_reason = "old_content not found in target block"
                    result.failed_ids.append(op.id)
                    continue
            elif op.op_type == "full_replace":
                full_replace_soul_block(
                    soul_db,
                    user_id=user_id,
                    section=op.target_block,
                    content=op.content,
                )
            else:
                op.failed = True
                op.failure_reason = f"unsupported op_type: {op.op_type}"
                result.failed_ids.append(op.id)
                continue

            op.consolidated = True
            op.consolidated_at = now
            result.processed_ids.append(op.id)

        # Commit soul first so identity mutations are durable before we
        # mark ops as consolidated. If runtime commit fails afterward,
        # ops remain unconsolidated but soul already has the data — on
        # retry the idempotency check (scoped by run + tool_call_id)
        # prevents re-application. The reverse (runtime first, soul
        # fails) would silently lose identity writes.
        soul_db.commit()
        runtime_db.commit()
        return result
    except Exception:
        soul_db.rollback()
        runtime_db.rollback()
        raise
    finally:
        soul_db.close()
        runtime_db.close()


@dataclass(slots=True)
class LLMExtractionResult:
    memories: list[dict[str, Any]] = field(default_factory=list)
    emotion: dict[str, Any] | None = None


async def extract_memories_via_llm(
    *,
    user_message: str,
    assistant_response: str,
) -> LLMExtractionResult:
    """Call the LLM to extract structured memories and emotion from a conversation turn."""
    if settings.agent_provider == "scaffold":
        return LLMExtractionResult()

    try:
        from anima_server.services.agent.llm import create_llm
        from anima_server.services.agent.messages import HumanMessage, SystemMessage
        from anima_server.services.agent.prompt_loader import PromptLoader

        llm = create_llm()
        prompt_loader = PromptLoader(agent_name="Anima")
        prompt = prompt_loader.memory_extraction(
            user_message=user_message,
            assistant_response=assistant_response,
        )
        response = await llm.ainvoke(
            [
                SystemMessage(content="You extract memories and emotions. Respond only with JSON."),
                HumanMessage(content=prompt),
            ]
        )
        content = getattr(response, "content", "")
        if not isinstance(content, str):
            content = str(content)

        result = LLMExtractionResult()

        # Try parsing as object with "memories" and "emotion" fields
        obj = _parse_json_object(content)
        if obj is not None:
            memories = obj.get("memories", [])
            if isinstance(memories, list):
                result.memories = [m for m in memories if isinstance(m, dict)]
                emotion = obj.get("emotion")
                if emotion and isinstance(emotion, dict):
                    result.emotion = emotion
                return result

        # Fallback: try as plain array (backward compat)
        result.memories = _parse_json_array(content)
        return result
    except Exception:
        logger.exception("LLM memory extraction failed")
        return LLMExtractionResult()


async def resolve_conflict(
    *,
    existing_content: str,
    new_content: str,
) -> str:
    """Ask LLM whether new content updates or is different from existing. Returns 'UPDATE' or 'DIFFERENT'."""
    if settings.agent_provider == "scaffold":
        return "DIFFERENT"

    try:
        from anima_server.services.agent.llm import create_llm
        from anima_server.services.agent.messages import HumanMessage, SystemMessage
        from anima_server.services.agent.prompt_loader import PromptLoader

        llm = create_llm()
        prompt_loader = PromptLoader(agent_name="Anima")
        prompt = prompt_loader.conflict_check(
            existing=existing_content,
            new_content=new_content,
        )
        response = await llm.ainvoke(
            [
                SystemMessage(content="Respond with exactly one word: UPDATE or DIFFERENT"),
                HumanMessage(content=prompt),
            ]
        )
        content = getattr(response, "content", "").strip().upper()
        if content in ("UPDATE", "DIFFERENT"):
            return content
        return "DIFFERENT"
    except Exception:
        logger.exception("LLM conflict resolution failed")
        return "DIFFERENT"


@dataclass(frozen=True, slots=True)
class BatchConflictResult:
    """Result of batch conflict resolution: UPDATE with a real DB id, or DIFFERENT."""

    action: str  # "UPDATE" or "DIFFERENT"
    matched_id: int | None = None  # real DB id of the existing memory to update


async def resolve_conflict_batch(
    *,
    similar_items: Sequence[Any],
    new_content: str,
    user_id: int,
) -> BatchConflictResult:
    """Compare new content against multiple existing memories using integer-remapped IDs.

    Maps real database IDs to sequential integers (0, 1, 2...) before
    sending to the LLM, then maps the LLM's chosen integer back to the
    real ID.  This prevents the LLM from hallucinating or garbling UUIDs
    / large integer IDs.

    Falls back to single-item ``resolve_conflict()`` when there is only
    one similar item.
    """
    if not similar_items:
        return BatchConflictResult(action="DIFFERENT")

    # --- Single item: delegate to the simpler prompt ---
    if len(similar_items) == 1:
        item = similar_items[0]
        plaintext = df(user_id, item.content, table="memory_items", field="content")
        verdict = await resolve_conflict(
            existing_content=plaintext,
            new_content=new_content,
        )
        if verdict == "UPDATE":
            return BatchConflictResult(action="UPDATE", matched_id=item.id)
        return BatchConflictResult(action="DIFFERENT")

    # --- Multiple items: batch with integer-remapped IDs ---
    # Build the id mapping: sequential int -> real DB id
    int_to_real: dict[int, int] = {}
    lines: list[str] = []
    for idx, item in enumerate(similar_items):
        int_to_real[idx] = item.id
        plaintext = df(user_id, item.content, table="memory_items", field="content")
        lines.append(f"[{idx}] {plaintext}")

    existing_memories_block = "\n".join(lines)

    if settings.agent_provider == "scaffold":
        return BatchConflictResult(action="DIFFERENT")

    try:
        from anima_server.services.agent.llm import create_llm
        from anima_server.services.agent.messages import HumanMessage, SystemMessage
        from anima_server.services.agent.prompt_loader import PromptLoader

        llm = create_llm()
        prompt_loader = PromptLoader(agent_name="Anima")
        prompt = prompt_loader.batch_conflict_check(
            existing_memories=existing_memories_block,
            new_content=new_content,
        )
        response = await llm.ainvoke(
            [
                SystemMessage(content="Respond with exactly: UPDATE <id> or DIFFERENT"),
                HumanMessage(content=prompt),
            ]
        )
        content = getattr(response, "content", "").strip().upper()

        # Parse "UPDATE <int>"
        m = re.match(r"UPDATE\s+(\d+)", content)
        if m:
            chosen_int = int(m.group(1))
            real_id = int_to_real.get(chosen_int)
            if real_id is not None:
                return BatchConflictResult(action="UPDATE", matched_id=real_id)
            # LLM returned an integer outside our range — treat as DIFFERENT
            logger.warning(
                "LLM returned out-of-range id %d (max %d) in batch conflict resolution",
                chosen_int,
                len(int_to_real) - 1,
            )
            return BatchConflictResult(action="DIFFERENT")

        if content.startswith("DIFFERENT"):
            return BatchConflictResult(action="DIFFERENT")

        # Unrecognised response — safe default
        logger.debug("Unrecognised batch conflict response: %s", content)
        return BatchConflictResult(action="DIFFERENT")

    except Exception:
        logger.exception("LLM batch conflict resolution failed")
        return BatchConflictResult(action="DIFFERENT")


from anima_server.services.agent.json_utils import (
    parse_json_array as _parse_json_array,
)
from anima_server.services.agent.json_utils import (
    parse_json_object as _parse_json_object,
)


def extract_turn_memory(user_message: str) -> ExtractedTurnMemory:
    facts = tuple(extract_pattern_items(user_message, _FACT_EXTRACTORS))
    preferences = tuple(extract_pattern_items(user_message, _PREFERENCE_EXTRACTORS))
    current_focus = extract_current_focus(user_message)
    return ExtractedTurnMemory(
        facts=facts,
        preferences=preferences,
        current_focus=current_focus,
    )


def extract_pattern_items(
    text: str,
    extractors: Sequence[PatternExtractor],
) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for extractor in extractors:
        for match in extractor.pattern.finditer(text):
            normalized_value = normalize_fragment(match.group("value"))
            if not is_viable_memory_fragment(normalized_value):
                continue
            item = normalize_fragment(extractor.formatter(normalized_value))
            if not item:
                continue
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
    return items


def extract_current_focus(text: str) -> str | None:
    for pattern in _CURRENT_FOCUS_PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue
        value = normalize_fragment(match.group("value"))
        if value.lower().startswith("to "):
            value = value[3:].strip()
        if is_viable_memory_fragment(value):
            return value
    return None


def normalize_fragment(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" \t\r\n\"'`.,;:!?")


def is_viable_memory_fragment(value: str) -> bool:
    if not value:
        return False
    lowered = value.lower()
    if lowered in {"it", "that", "this", "them", "something", "stuff"}:
        return False
    return 3 <= len(value) <= 160


SOUL_WRITER_CANDIDATE_THRESHOLD = 15


async def run_background_extraction(
    *,
    user_id: int,
    user_message: str,
    assistant_response: str,
    runtime_db_factory: Callable[..., object] | None = None,
) -> None:
    """Per-turn extraction. Writes ONLY to PG. Never touches SQLCipher."""
    from anima_server.services.agent.candidate_ops import (
        count_eligible_candidates,
        create_memory_candidate,
    )

    try:
        rt_factory = runtime_db_factory or _get_runtime_factory()
        if rt_factory is None:
            return
    except RuntimeError:
        return

    try:
        with rt_factory() as rt_db:
            # 1. Regex extraction
            extracted = extract_turn_memory(user_message)
            for fact in extracted.facts:
                create_memory_candidate(rt_db, user_id=user_id, content=fact,
                                        category="fact", importance=3,
                                        importance_source="regex", source="regex")
            for pref in extracted.preferences:
                create_memory_candidate(rt_db, user_id=user_id, content=pref,
                                        category="preference", importance=3,
                                        importance_source="regex", source="regex")

            # 2. LLM extraction
            if settings.agent_provider != "scaffold":
                try:
                    llm_result = await extract_memories_via_llm(
                        user_message=user_message,
                        assistant_response=assistant_response,
                    )
                    for item in llm_result.memories:
                        content = item.get("content", "")
                        if not content or not isinstance(content, str):
                            continue
                        create_memory_candidate(
                            rt_db, user_id=user_id,
                            content=content,
                            category=item.get("category", "fact"),
                            importance=item.get("importance", 3),
                            importance_source="llm", source="llm",
                        )
                except Exception:
                    logger.exception("LLM extraction failed for user %s", user_id)

            rt_db.commit()

            # 3. Threshold check
            count = count_eligible_candidates(rt_db, user_id=user_id)
            if count >= SOUL_WRITER_CANDIDATE_THRESHOLD:
                from anima_server.services.agent.soul_writer import run_soul_writer
                asyncio.create_task(run_soul_writer(user_id))

    except Exception as exc:
        logger.exception("Background memory consolidation failed for user %s", user_id)
        health_emit("memory", "consolidation", "error", user_id=user_id, data={
            "error": str(exc),
        })

    # Embedding backfill moved to inactivity-only path (reflection.py)
    # to avoid per-turn SQLCipher writes from the conversation hot path.


async def _backfill_user_embeddings(
    user_id: int,
    *,
    db_factory: Callable[..., object] | None = None,
) -> None:
    """Embed any memory items that don't have embeddings yet."""
    if settings.agent_provider == "scaffold":
        return
    from anima_server.db.session import SessionLocal
    from anima_server.services.agent.embeddings import backfill_embeddings

    factory = db_factory or SessionLocal
    with factory() as db:
        count = await backfill_embeddings(db, user_id=user_id, batch_size=10)
        if count > 0:
            db.commit()
            logger.info("Backfilled %d embeddings for user %s", count, user_id)


def schedule_background_memory_consolidation(
    *,
    user_id: int,
    user_message: str,
    assistant_response: str,
    thread_id: int | None = None,
    db_factory: Callable[..., object] | None = None,
    runtime_db_factory: Callable[..., object] | None = None,
) -> None:
    if not settings.agent_background_memory_enabled:
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    # Per-turn: PG-only extraction + embedding backfill
    task = loop.create_task(
        run_background_extraction(
            user_id=user_id,
            user_message=user_message,
            assistant_response=assistant_response,
            runtime_db_factory=runtime_db_factory,
        )
    )
    with _background_tasks_lock:
        _background_tasks.add(task)
    task.add_done_callback(_on_background_task_done)

    # Embedding backfill moved to inactivity-only path (reflection.py)
    # to avoid per-turn SQLCipher writes from the conversation hot path.


async def drain_background_memory_tasks() -> None:
    with _background_tasks_lock:
        tasks = tuple(_background_tasks)
    if not tasks:
        return
    await asyncio.gather(*tasks, return_exceptions=True)


def _on_background_task_done(task: asyncio.Task[None]) -> None:
    with _background_tasks_lock:
        _background_tasks.discard(task)
    try:
        task.result()
    except asyncio.CancelledError:
        return


def _get_runtime_factory() -> Callable[..., object] | None:
    from anima_server.db.runtime import get_runtime_session_factory

    try:
        return get_runtime_session_factory()
    except RuntimeError:
        return None
    except Exception:
        logger.exception("Background memory consolidation task failed")
