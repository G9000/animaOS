from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from sqlalchemy import select

from anima_server.config import settings
from anima_server.services.agent.emotional_intelligence import (
    record_emotional_signal,
)
from anima_server.services.agent.json_utils import (
    parse_json_array as _parse_json_array,
)
from anima_server.services.agent.json_utils import (
    parse_json_object as _parse_json_object,
)
from anima_server.services.agent.text_processing import prepare_memory_text
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


_FACT_EXTRACTORS: tuple[PatternExtractor, ...] = (
    PatternExtractor(
        pattern=re.compile(
            r"\bI am (?P<value>\d{1,3}) years old\b", re.IGNORECASE),
        formatter=lambda value: f"Age: {value}",
    ),
    PatternExtractor(
        pattern=re.compile(
            r"\bmy birthday is (?P<value>[^.?!\n]+)", re.IGNORECASE),
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
    re.compile(
        r"\bI(?:'m| am) focused on (?P<value>[^.?!\n]+)", re.IGNORECASE),
    re.compile(r"\bI need to focus on (?P<value>[^.?!\n]+)", re.IGNORECASE),
)


@dataclass(slots=True)
class LLMExtractionResult:
    memories: list[dict[str, Any]] = field(default_factory=list)
    profile_updates: list[dict[str, Any]] = field(default_factory=list)
    foresight: list[dict[str, Any]] = field(default_factory=list)
    emotion: dict[str, Any] | None = None
    failed: bool = False
    error: str | None = None


async def extract_memories_via_llm(
    *,
    user_message: str,
    assistant_response: str,
) -> LLMExtractionResult:
    """Call the LLM to extract structured memories and emotion from a conversation turn."""
    if settings.agent_provider == "scaffold":
        return LLMExtractionResult()

    prepared_user_message = prepare_memory_text(user_message)
    prepared_assistant_response = prepare_memory_text(assistant_response)

    try:
        from anima_server.services.agent.llm_json import call_llm_for_text
        from anima_server.services.agent.prompt_loader import PromptLoader

        prompt_loader = PromptLoader(agent_name="Anima")
        prompt = prompt_loader.memory_extraction(
            user_message=prepared_user_message or user_message,
            assistant_response=prepared_assistant_response or assistant_response,
        )
        content = await call_llm_for_text(
            "You extract memories, profile updates, foresight signals, and emotions. "
            "Respond only with JSON.",
            prompt,
        )

        result = LLMExtractionResult()

        # Try parsing as object with "memories" and "emotion" fields
        obj = _parse_json_object(content)
        if obj is not None:
            memories = obj.get("memories", [])
            if isinstance(memories, list):
                result.memories = [m for m in memories if isinstance(m, dict)]
            profile_updates = obj.get("profile_updates", [])
            if isinstance(profile_updates, list):
                result.profile_updates = [
                    update for update in profile_updates if isinstance(update, dict)
                ]
            foresight = obj.get("foresight", [])
            if isinstance(foresight, list):
                result.foresight = [item for item in foresight if isinstance(item, dict)]
            emotion = obj.get("emotion")
            if emotion and isinstance(emotion, dict):
                result.emotion = emotion
            return result

        # Fallback: try as plain array (backward compat)
        result.memories = _parse_json_array(content)
        return result
    except Exception as exc:
        logger.exception("LLM memory extraction failed")
        return LLMExtractionResult(failed=True, error=str(exc))


async def resolve_conflict(
    *,
    existing_content: str,
    new_content: str,
) -> str:
    """Ask LLM whether new content updates or is different from existing. Returns 'UPDATE' or 'DIFFERENT'."""
    if settings.agent_provider == "scaffold":
        return "DIFFERENT"

    try:
        from anima_server.services.agent.llm_json import call_llm_for_text
        from anima_server.services.agent.prompt_loader import PromptLoader

        prompt_loader = PromptLoader(agent_name="Anima")
        prompt = prompt_loader.conflict_check(
            existing=existing_content,
            new_content=new_content,
        )
        content = (
            await call_llm_for_text(
                "Respond with exactly one word: UPDATE or DIFFERENT",
                prompt,
            )
        ).strip().upper()
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
        plaintext = df(user_id, item.content,
                       table="memory_items", field="content")
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
        plaintext = df(user_id, item.content,
                       table="memory_items", field="content")
        lines.append(f"[{idx}] {plaintext}")

    existing_memories_block = "\n".join(lines)

    if settings.agent_provider == "scaffold":
        return BatchConflictResult(action="DIFFERENT")

    try:
        from anima_server.services.agent.llm_json import call_llm_for_text
        from anima_server.services.agent.prompt_loader import PromptLoader

        prompt_loader = PromptLoader(agent_name="Anima")
        prompt = prompt_loader.batch_conflict_check(
            existing_memories=existing_memories_block,
            new_content=new_content,
        )
        content = (
            await call_llm_for_text(
                "Respond with exactly: UPDATE <id> or DIFFERENT",
                prompt,
            )
        ).strip().upper()

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


def extract_turn_memory(user_message: str) -> ExtractedTurnMemory:
    prepared_message = prepare_memory_text(user_message)
    facts = tuple(extract_pattern_items(prepared_message, _FACT_EXTRACTORS))
    preferences = tuple(extract_pattern_items(
        prepared_message, _PREFERENCE_EXTRACTORS))
    current_focus = extract_current_focus(prepared_message)
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


async def run_background_extraction(
    *,
    user_id: int,
    user_message: str,
    assistant_response: str,
    runtime_db_factory: Callable[..., object] | None = None,
    trigger_soul_writer: bool = True,
    source_message_ids: list[int] | None = None,
) -> None:
    """Per-turn extraction. Writes ONLY to PG. Never touches SQLCipher."""
    from anima_server.services.agent.candidate_ops import (
        count_eligible_candidates,
        create_memory_candidate,
    )
    from anima_server.services.agent.user_profile import (
        count_eligible_profile_update_candidates,
        create_profile_update_candidates_from_payload,
    )

    try:
        rt_factory = runtime_db_factory or _get_runtime_factory()
        if rt_factory is None:
            return
    except RuntimeError:
        return

    llm_enabled = settings.agent_provider != "scaffold"
    intent_id: int | None = None

    try:
        # Phase A — durable pre-LLM work.  Regex candidates, foresight, and
        # a retryable intent row are committed BEFORE the slow LLM call, so
        # a crash, shutdown, or cancellation mid-extraction loses at most
        # the LLM enrichment (which the Soul Writer's retry loop recovers
        # from the intent row).
        with rt_factory() as rt_db:
            # 1. Regex extraction
            extracted = extract_turn_memory(user_message)
            for fact in extracted.facts:
                create_memory_candidate(
                    rt_db,
                    user_id=user_id,
                    content=fact,
                    category="fact",
                    importance=3,
                    importance_source="regex",
                    source="regex",
                    source_message_ids=source_message_ids,
                )
            for pref in extracted.preferences:
                create_memory_candidate(
                    rt_db,
                    user_id=user_id,
                    content=pref,
                    category="preference",
                    importance=3,
                    importance_source="regex",
                    source="regex",
                    source_message_ids=source_message_ids,
                )

            regex_count = len(extracted.facts) + len(extracted.preferences)
            if regex_count > 0:
                logger.info(
                    "Regex extraction for user %s: %d facts, %d preferences",
                    user_id,
                    len(extracted.facts),
                    len(extracted.preferences),
                )
            foresight_observed_at = _source_message_observed_at(
                rt_db,
                user_id=user_id,
                source_message_ids=source_message_ids,
            )
            _store_foresight_best_effort(
                user_id=user_id,
                user_message=user_message,
                source_message_ids=source_message_ids,
                observed_at=foresight_observed_at,
            )

            if llm_enabled:
                # Guard is in-flight, not yet a failure: "pending" keeps it out
                # of the retry sweep until Phase C resolves it (or a crash
                # leaves it stale for recovery).
                intent = record_memory_extraction_failure(
                    rt_db,
                    user_id=user_id,
                    source_message_ids=source_message_ids,
                    user_message=user_message,
                    assistant_response=assistant_response,
                    failure_reason="LLM extraction pending (crash-recovery guard)",
                    status="pending",
                )
                rt_db.commit()
                intent_id = intent.id
            else:
                rt_db.commit()

        # Phase B — the LLM call runs with NO session held: it used to pin
        # a pool connection per concurrent turn for the multi-second call.
        llm_result = None
        if llm_enabled:
            try:
                llm_result = await extract_memories_via_llm(
                    user_message=user_message,
                    assistant_response=assistant_response,
                )
            except Exception:
                logger.exception(
                    "LLM extraction pipeline failed for user %s. "
                    "User message preview: %.100s",
                    user_id,
                    user_message[:100],
                )

        # Phase C — persist LLM results and resolve (or keep) the intent
        # row in a fresh session; results and resolution commit atomically.
        with rt_factory() as rt_db:
            if llm_enabled:
                from anima_server.models.runtime_memory import (
                    MemoryExtractionFailure,
                )

                intent = (
                    rt_db.get(MemoryExtractionFailure, intent_id)
                    if intent_id is not None
                    else None
                )
                now = datetime.now(UTC)
                if llm_result is None or llm_result.failed:
                    reason = (
                        (llm_result.error if llm_result is not None else None)
                        or "LLM memory extraction failed"
                    )
                    if intent is not None:
                        # Promote the in-flight guard to a retryable failure so
                        # the Soul Writer sweep picks it up.
                        intent.status = "failed"
                        intent.failure_reason = reason[:2000]
                        intent.last_attempt_at = now
                        intent.updated_at = now
                    health_emit(
                        "memory",
                        "extraction_failed",
                        "warn",
                        user_id=user_id,
                        data={
                            "error": reason,
                            "source_message_ids": source_message_ids or [],
                        },
                    )
                    logger.warning(
                        "LLM extraction failed for user %s; preserved retry work",
                        user_id,
                    )
                else:
                    for item in llm_result.memories:
                        content = item.get("content", "")
                        if not content or not isinstance(content, str):
                            continue
                        create_memory_candidate(
                            rt_db,
                            user_id=user_id,
                            content=content,
                            category=item.get("category", "fact"),
                            importance=item.get("importance", 3),
                            importance_source="llm",
                            source="llm",
                            source_message_ids=source_message_ids,
                            salience=item.get("salience")
                            if isinstance(item.get("salience"), dict)
                            else None,
                        )
                    llm_count = len(llm_result.memories)
                    profile_update_count = create_profile_update_candidates_from_payload(
                        rt_db,
                        user_id=user_id,
                        profile_updates=llm_result.profile_updates,
                        source_message_ids=source_message_ids,
                    )
                    _store_foresight_best_effort(
                        user_id=user_id,
                        user_message=user_message,
                        source_message_ids=source_message_ids,
                        observed_at=foresight_observed_at,
                        llm_foresight=llm_result.foresight,
                    )

                    emotion_payload = (
                        llm_result.emotion
                        if isinstance(llm_result.emotion, dict)
                        else None
                    )
                    raw_emotion = (
                        emotion_payload.get("emotion") if emotion_payload else None
                    )
                    emotion_name = (
                        raw_emotion.strip()
                        if isinstance(raw_emotion, str)
                        else None
                    )

                    # Persist detected emotion (was previously only logged)
                    if emotion_payload and emotion_name:
                        record_emotional_signal(
                            rt_db,
                            user_id=user_id,
                            emotion=emotion_name,
                            confidence=float(
                                emotion_payload.get("confidence", 0.5)
                            ),
                            evidence_type="linguistic",
                            evidence=str(
                                emotion_payload.get("evidence", "")
                            ),
                            trajectory=str(
                                emotion_payload.get("trajectory", "stable")
                            ),
                        )

                    if intent is not None:
                        intent.status = "resolved"
                        intent.resolved_at = now
                        intent.updated_at = now

                    logger.info(
                        (
                            "LLM extraction for user %s: %d memories, "
                            "%d profile updates extracted%s"
                        ),
                        user_id,
                        llm_count,
                        profile_update_count,
                        f" (emotion: {emotion_name})" if emotion_name else "",
                    )

            rt_db.commit()

            # 3. Eager promotion — run Soul Writer unless a higher-level
            # orchestrator is taking ownership of the post-turn pipeline.
            # Check both candidates (from extraction) and pending ops
            # (from core_memory_append/replace) so neither pathway stalls.
            from anima_server.services.agent.pending_ops import count_pending_ops

            candidate_count = count_eligible_candidates(rt_db, user_id=user_id)
            profile_update_count = count_eligible_profile_update_candidates(
                rt_db,
                user_id=user_id,
            )
            pending_count = count_pending_ops(rt_db, user_id=user_id)
            if trigger_soul_writer and (
                candidate_count > 0
                or profile_update_count > 0
                or pending_count > 0
            ):
                from anima_server.services.agent.soul_writer import run_soul_writer

                task = asyncio.create_task(run_soul_writer(user_id))
                with _background_tasks_lock:
                    _background_tasks.add(task)
                task.add_done_callback(_background_tasks.discard)
                logger.info(
                    (
                        "Triggered eager Soul Writer for user %s "
                        "(%d candidates, %d profile updates, %d pending ops)"
                    ),
                    user_id,
                    candidate_count,
                    profile_update_count,
                    pending_count,
                )

    except asyncio.CancelledError:
        # Shutdown cancellation: Phase A already committed the regex
        # candidates and the retry intent, so nothing is lost — the Soul
        # Writer recovers the LLM phase on its next run.
        logger.info(
            "Background extraction cancelled for user %s; "
            "pre-LLM work is committed and the intent row will be retried",
            user_id,
        )
        raise
    except Exception as exc:
        logger.exception(
            "Background memory consolidation FAILED for user %s "
            "(pre-LLM work committed: %s)",
            user_id,
            intent_id is not None or not llm_enabled,
        )
        health_emit(
            "memory",
            "consolidation",
            "error",
            user_id=user_id,
            data={
                "error": str(exc),
                "user_message_preview": user_message[:100],
            },
        )

    # Embedding backfill moved out of the plain per-turn extraction path
    # to avoid SQLCipher writes on every chat turn. It only runs as part
    # of the full sleeptime orchestrator / inactivity reflection.


def record_memory_extraction_failure(
    runtime_db: Any,
    *,
    user_id: int,
    source_message_ids: list[int] | None,
    user_message: str,
    assistant_response: str,
    failure_reason: str,
    extraction_model: str | None = None,
    status: str = "failed",
):
    """Persist an extraction intent row.

    ``status`` defaults to ``"failed"`` (immediately retryable).  The Phase A
    crash-recovery guard passes ``"pending"`` instead: while the LLM call is
    genuinely in flight the row must NOT be retryable, or a concurrent turn /
    sleep run would re-extract the same messages and enqueue duplicate
    candidates.  Phase C flips it to ``"failed"`` if the call actually failed;
    a hard crash leaves it ``"pending"`` and the retry sweep recovers it once
    it goes stale.
    """
    from anima_server.models.runtime_memory import MemoryExtractionFailure

    failure = MemoryExtractionFailure(
        user_id=user_id,
        source_message_ids=[int(message_id) for message_id in source_message_ids or []],
        user_message_preview=_preview_text(user_message),
        assistant_response_preview=_preview_text(assistant_response),
        failure_reason=failure_reason[:2000],
        extraction_model=extraction_model,
        status=status,
        last_attempt_at=datetime.now(UTC),
    )
    runtime_db.add(failure)
    runtime_db.flush()
    return failure


def _store_foresight_best_effort(
    *,
    user_id: int,
    user_message: str,
    source_message_ids: list[int] | None,
    observed_at: datetime | None = None,
    llm_foresight: list[dict[str, Any]] | None = None,
) -> None:
    try:
        from anima_server.db.session import SessionLocal, get_user_session_factory, is_sqlite_mode
        from anima_server.services.agent.foresight import (
            mark_cancelled_from_text,
            parse_llm_foresight_payload,
            store_foresight_from_text,
            upsert_foresight_signal,
        )
        from anima_server.services.user_timezone import resolve_timezone_from_world_context

        observed_at = observed_at or datetime.now(UTC)
        factory = get_user_session_factory(user_id) if is_sqlite_mode() else SessionLocal
        with factory() as soul_db:
            timezone_name = None
            with suppress(ValueError):
                timezone_name, _timezone = resolve_timezone_from_world_context(
                    soul_db,
                    user_id=user_id,
                )
            count = store_foresight_from_text(
                soul_db,
                user_id=user_id,
                text=user_message,
                observed_at=observed_at,
                source_message_ids=source_message_ids,
                timezone_name=timezone_name,
            )
            for signal in parse_llm_foresight_payload(
                llm_foresight or (),
                observed_at=observed_at,
                timezone_name=timezone_name,
            ):
                upsert_foresight_signal(
                    soul_db,
                    user_id=user_id,
                    signal=signal,
                    source_message_ids=source_message_ids,
                    observed_at=observed_at,
                )
                count += 1
            cancelled = mark_cancelled_from_text(
                soul_db,
                user_id=user_id,
                text=user_message,
                observed_at=observed_at,
            )
            if count or cancelled:
                soul_db.commit()
                logger.info(
                    "Foresight extraction for user %s: %d upserted, %d cancelled",
                    user_id,
                    count,
                    cancelled,
                )
    except Exception:
        logger.debug("Foresight extraction skipped for user %s", user_id, exc_info=True)


def _source_message_observed_at(
    runtime_db: Any,
    *,
    user_id: int,
    source_message_ids: list[int] | None,
) -> datetime | None:
    if not source_message_ids:
        return None
    try:
        from anima_server.models.runtime import RuntimeMessage

        rows = list(
            runtime_db.scalars(
                select(RuntimeMessage)
                .where(
                    RuntimeMessage.user_id == user_id,
                    RuntimeMessage.id.in_([int(message_id) for message_id in source_message_ids]),
                )
                .order_by(
                    RuntimeMessage.role == "user",
                    RuntimeMessage.created_at.asc(),
                    RuntimeMessage.id.asc(),
                )
            ).all()
        )
    except Exception:
        return None
    if not rows:
        return None
    user_rows = [row for row in rows if row.role == "user"]
    selected = user_rows[0] if user_rows else rows[0]
    created_at = selected.created_at
    if created_at is None:
        return None
    if created_at.tzinfo is None:
        return created_at.replace(tzinfo=UTC)
    return created_at


def _preview_text(value: str, *, limit: int = 240) -> str | None:
    prepared = prepare_memory_text(value)
    if not prepared:
        return None
    return prepared[:limit]


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


async def _run_post_turn_sleeptime_pipeline(
    *,
    user_id: int,
    user_message: str,
    assistant_response: str,
    thread_id: int | None = None,
    db_factory: Callable[..., object] | None = None,
    runtime_db_factory: Callable[..., object] | None = None,
    source_message_ids: list[int] | None = None,
) -> None:
    """Run the post-turn extraction pipeline, then hand off to sleeptime orchestration."""
    await run_background_extraction(
        user_id=user_id,
        user_message=user_message,
        assistant_response=assistant_response,
        runtime_db_factory=runtime_db_factory,
        trigger_soul_writer=False,
        source_message_ids=source_message_ids,
    )

    from anima_server.services.agent.sleep_agent import run_sleeptime_agents

    await run_sleeptime_agents(
        user_id=user_id,
        user_message=user_message,
        assistant_response=assistant_response,
        thread_id=thread_id,
        db_factory=db_factory,
        runtime_db_factory=runtime_db_factory,
    )


def schedule_background_memory_consolidation(
    *,
    user_id: int,
    user_message: str,
    assistant_response: str,
    thread_id: int | None = None,
    conversation_turn_count: int | None = None,
    db_factory: Callable[..., object] | None = None,
    runtime_db_factory: Callable[..., object] | None = None,
    source_message_ids: list[int] | None = None,
) -> None:
    if not settings.agent_background_memory_enabled:
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    from anima_server.services.agent.sleep_agent import should_run_sleeptime

    if should_run_sleeptime(conversation_turn_count):
        # Every Nth turn: run extraction, then hand off to the full
        # sleeptime orchestrator.
        task = loop.create_task(
            _run_post_turn_sleeptime_pipeline(
                user_id=user_id,
                user_message=user_message,
                assistant_response=assistant_response,
                thread_id=thread_id,
                db_factory=db_factory,
                runtime_db_factory=runtime_db_factory,
                source_message_ids=source_message_ids,
            )
        )
    else:
        # Most turns only do PG extraction + eager Soul Writer promotion.
        task = loop.create_task(
            run_background_extraction(
                user_id=user_id,
                user_message=user_message,
                assistant_response=assistant_response,
                runtime_db_factory=runtime_db_factory,
                source_message_ids=source_message_ids,
            )
        )

    with _background_tasks_lock:
        _background_tasks.add(task)
    task.add_done_callback(_on_background_task_done)


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
