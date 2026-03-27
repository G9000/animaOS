from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock
from typing import Any

from anima_server.config import settings
from anima_server.services.agent.claims import upsert_claim
from anima_server.services.agent.memory_store import (
    add_daily_log,
    set_current_focus,
    store_memory_item,
    supersede_memory_item,
)
from anima_server.services.data_crypto import df

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
class MemoryConsolidationResult:
    daily_log_id: int | None = None
    facts_added: list[str] = field(default_factory=list)
    preferences_added: list[str] = field(default_factory=list)
    current_focus_updated: str | None = None
    llm_items_added: list[str] = field(default_factory=list)
    conflicts_resolved: list[str] = field(default_factory=list)


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


def consolidate_turn_memory(
    *,
    user_id: int,
    user_message: str,
    assistant_response: str,
    now: datetime | None = None,
    db_factory: Callable[..., object] | None = None,
) -> MemoryConsolidationResult:
    from anima_server.db.session import SessionLocal

    factory = db_factory or SessionLocal
    runtime_factory = _get_runtime_factory()
    result = MemoryConsolidationResult()

    with factory() as db:
        log = add_daily_log(
            db,
            user_id=user_id,
            user_message=user_message,
            assistant_response=assistant_response,
        )
        result.daily_log_id = log.id

        extracted = extract_turn_memory(user_message)

        for fact in extracted.facts:
            write_result = store_memory_item(
                db,
                user_id=user_id,
                content=fact,
                category="fact",
                source="extraction",
                allow_update=True,
            )
            if write_result.action == "added":
                result.facts_added.append(fact)
            elif write_result.action == "superseded":
                result.conflicts_resolved.append(f"{write_result.matched_item.content} -> {fact}")
                try:
                    from anima_server.services.agent.forgetting import suppress_memory

                    if write_result.matched_item and write_result.item:
                        suppress_memory(
                            db,
                            memory_id=write_result.matched_item.id,
                            superseded_by=write_result.item.id,
                            user_id=user_id,
                        )
                except Exception:
                    logger.debug("Suppression failed for regex-superseded fact")

        for pref in extracted.preferences:
            write_result = store_memory_item(
                db,
                user_id=user_id,
                content=pref,
                category="preference",
                source="extraction",
                allow_update=True,
            )
            if write_result.action == "added":
                result.preferences_added.append(pref)
            elif write_result.action == "superseded":
                result.conflicts_resolved.append(f"{write_result.matched_item.content} -> {pref}")
                try:
                    from anima_server.services.agent.forgetting import suppress_memory

                    if write_result.matched_item and write_result.item:
                        suppress_memory(
                            db,
                            memory_id=write_result.matched_item.id,
                            superseded_by=write_result.item.id,
                            user_id=user_id,
                        )
                except Exception:
                    logger.debug("Suppression failed for regex-superseded pref")

        if extracted.current_focus:
            set_current_focus(db, user_id=user_id, focus=extracted.current_focus)
            result.current_focus_updated = extracted.current_focus

        db.commit()

    return result


async def consolidate_turn_memory_with_llm(
    *,
    user_id: int,
    user_message: str,
    assistant_response: str,
    db_factory: Callable[..., object] | None = None,
) -> MemoryConsolidationResult:
    """Full consolidation: regex extraction + LLM extraction + conflict resolution."""
    result = consolidate_turn_memory(
        user_id=user_id,
        user_message=user_message,
        assistant_response=assistant_response,
        db_factory=db_factory,
    )

    from anima_server.db.session import SessionLocal

    factory = db_factory or SessionLocal
    runtime_factory = _get_runtime_factory()

    # --- Predict-Calibrate path (F3) ---
    # Try predict-calibrate extraction when enough facts exist.
    # On failure, fall back to direct extraction (F3.9).
    pc_items: list[dict[str, Any]] | None = None
    pc_emotion_data: dict[str, Any] | None = None
    try:
        from anima_server.services.agent.predict_calibrate import predict_calibrate_extraction

        with factory() as _pcdb:
            pc_items, pc_emotion_data = await predict_calibrate_extraction(
                user_id=user_id,
                user_message=user_message,
                assistant_response=assistant_response,
                db=_pcdb,
            )
    except Exception:
        logger.debug("predict_calibrate_extraction failed, falling back to direct extraction")

    if pc_items is not None:
        llm_items = pc_items
        emotion_data = pc_emotion_data
    else:
        # Fallback: direct extraction (original path)
        extraction = await extract_memories_via_llm(
            user_message=user_message,
            assistant_response=assistant_response,
        )
        llm_items = extraction.memories
        emotion_data = extraction.emotion

    # Record any emotional signal extracted alongside memories
    if emotion_data and emotion_data.get("emotion"):
        try:
            from anima_server.services.agent.emotional_intelligence import record_emotional_signal

            emotion_session_factory = runtime_factory or factory
            with emotion_session_factory() as _edb:
                record_emotional_signal(
                    _edb,
                    user_id=user_id,
                    emotion=str(emotion_data["emotion"]),
                    confidence=float(emotion_data.get("confidence", 0.5)),
                    evidence_type=str(emotion_data.get("evidence_type", "linguistic")),
                    evidence=str(emotion_data.get("evidence", "")),
                    trajectory=str(emotion_data.get("trajectory", "stable")),
                )
                _edb.commit()
        except Exception:
            logger.debug("Failed to record emotional signal from extraction")

    if not llm_items:
        _promote_runtime_emotional_patterns(user_id=user_id, db_factory=db_factory)
        return result
    regex_contents = {c.lower() for c in result.facts_added + result.preferences_added}

    with factory() as db:
        for llm_item in llm_items:
            content = llm_item.get("content", "").strip()
            category = llm_item.get("category", "fact")
            importance = llm_item.get("importance", 3)

            if not content or len(content) < 3:
                continue
            if content.lower() in regex_contents:
                continue
            if category not in ("fact", "preference", "goal", "relationship"):
                category = "fact"
            if not isinstance(importance, int) or not 1 <= importance <= 5:
                importance = 3

            write_result = store_memory_item(
                db,
                user_id=user_id,
                content=content,
                category=category,
                importance=importance,
                source="extraction",
                allow_update=True,
                defer_on_similar=True,
            )

            if write_result.action == "added":
                result.llm_items_added.append(content)
                # Dual-write: create structured claim for the new item
                try:
                    upsert_claim(
                        db,
                        user_id=user_id,
                        content=content,
                        category=category,
                        importance=importance,
                        source_kind="extraction",
                        extractor="llm",
                        memory_item_id=write_result.item.id if write_result.item else None,
                        evidence_text=user_message,
                    )
                except Exception:
                    logger.debug("Claim dual-write failed for: %s", content)
                continue

            if write_result.action == "superseded":
                result.conflicts_resolved.append(
                    f"{write_result.matched_item.content} -> {content}"
                )
                result.llm_items_added.append(content)
                # F7: suppress the old memory (flag derived refs for regeneration)
                try:
                    from anima_server.services.agent.forgetting import suppress_memory

                    if write_result.matched_item and write_result.item:
                        suppress_memory(
                            db,
                            memory_id=write_result.matched_item.id,
                            superseded_by=write_result.item.id,
                            user_id=user_id,
                        )
                except Exception:
                    logger.debug("Suppression failed for superseded item")
                # Dual-write: supersede the structured claim too
                try:
                    upsert_claim(
                        db,
                        user_id=user_id,
                        content=content,
                        category=category,
                        importance=importance,
                        source_kind="extraction",
                        extractor="llm",
                        memory_item_id=write_result.item.id if write_result.item else None,
                        evidence_text=user_message,
                    )
                except Exception:
                    logger.debug("Claim dual-write (supersede) failed for: %s", content)
                continue

            if write_result.action == "duplicate":
                continue

            if write_result.action == "similar" and write_result.similar_items:
                batch_result = await resolve_conflict_batch(
                    similar_items=write_result.similar_items,
                    new_content=content,
                    user_id=user_id,
                )
                if batch_result.action == "UPDATE" and batch_result.matched_id is not None:
                    old_similar_id = batch_result.matched_id
                    # Find the matched item for logging
                    matched_item = next(
                        (it for it in write_result.similar_items if it.id == old_similar_id),
                        write_result.similar_items[0],
                    )
                    updated_item = supersede_memory_item(
                        db,
                        old_item_id=old_similar_id,
                        new_content=content,
                        importance=importance,
                    )
                    if updated_item is not None:
                        # F7: suppress the old memory
                        try:
                            from anima_server.services.agent.forgetting import suppress_memory

                            suppress_memory(
                                db,
                                memory_id=old_similar_id,
                                superseded_by=updated_item.id,
                                user_id=user_id,
                            )
                        except Exception:
                            logger.debug("Suppression failed for similar-update item")
                        result.conflicts_resolved.append(
                            f"{df(user_id, matched_item.content, table='memory_items', field='content')} -> {content}"
                        )
                        result.llm_items_added.append(content)
                        try:
                            upsert_claim(
                                db,
                                user_id=user_id,
                                content=content,
                                category=category,
                                importance=importance,
                                source_kind="extraction",
                                extractor="llm",
                                memory_item_id=updated_item.id,
                                evidence_text=user_message,
                            )
                        except Exception:
                            logger.debug("Claim dual-write (update) failed for: %s", content)
                elif batch_result.action == "DIFFERENT":
                    create_result = store_memory_item(
                        db,
                        user_id=user_id,
                        content=content,
                        category=category,
                        importance=importance,
                        source="extraction",
                    )
                    if create_result.action == "added":
                        result.llm_items_added.append(content)
                        try:
                            upsert_claim(
                                db,
                                user_id=user_id,
                                content=content,
                                category=category,
                                importance=importance,
                                source_kind="extraction",
                                extractor="llm",
                                memory_item_id=create_result.item.id
                                if create_result.item
                                else None,
                                evidence_text=user_message,
                            )
                        except Exception:
                            logger.debug("Claim dual-write (different) failed for: %s", content)

        db.commit()

    _promote_runtime_emotional_patterns(user_id=user_id, db_factory=db_factory)
    return result


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


async def run_background_memory_consolidation(
    *,
    user_id: int,
    user_message: str,
    assistant_response: str,
    db_factory: Callable[..., object] | None = None,
) -> None:
    try:
        if settings.agent_provider != "scaffold":
            await consolidate_turn_memory_with_llm(
                user_id=user_id,
                user_message=user_message,
                assistant_response=assistant_response,
                db_factory=db_factory,
            )
        else:
            consolidate_turn_memory(
                user_id=user_id,
                user_message=user_message,
                assistant_response=assistant_response,
                db_factory=db_factory,
            )

        # Invalidate companion memory cache so the next turn sees fresh data.
        from anima_server.services.agent.companion import get_companion

        companion = get_companion(user_id)
        if companion is not None:
            companion.invalidate_memory()

    except Exception:
        logger.exception("Background memory consolidation failed for user %s", user_id)

    # Opportunistic embedding backfill for items without embeddings
    try:
        await _backfill_user_embeddings(user_id, db_factory=db_factory)
    except Exception:
        logger.debug("Embedding backfill skipped for user %s", user_id)


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
) -> None:
    if not settings.agent_background_memory_enabled:
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    from anima_server.services.agent.sleep_agent import (
        bump_turn_counter,
        run_sleeptime_agents,
        should_run_sleeptime,
    )

    bump_turn_counter(user_id)
    run_full_orchestrator = should_run_sleeptime(user_id)

    if run_full_orchestrator:
        # Every N turns: run the full orchestrator (consolidation + KG + heat decay + episodes + …)
        task = loop.create_task(
            run_sleeptime_agents(
                user_id=user_id,
                user_message=user_message,
                assistant_response=assistant_response,
                thread_id=thread_id,
                db_factory=db_factory,
            )
        )
    else:
        # Every turn: at minimum run consolidation + embedding backfill
        task = loop.create_task(
            run_background_memory_consolidation(
                user_id=user_id,
                user_message=user_message,
                assistant_response=assistant_response,
                db_factory=db_factory,
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


def _promote_runtime_emotional_patterns(
    *,
    user_id: int,
    db_factory: Callable[..., object] | None = None,
) -> int:
    runtime_factory = _get_runtime_factory()
    if runtime_factory is None:
        return 0

    from anima_server.db.session import SessionLocal
    from anima_server.services.agent.emotional_patterns import promote_emotional_patterns

    factory = db_factory or SessionLocal

    try:
        with factory() as soul_db, runtime_factory() as pg_db:
            promoted = promote_emotional_patterns(
                soul_db=soul_db,
                pg_db=pg_db,
                user_id=user_id,
            )
            if promoted > 0:
                soul_db.commit()
            return promoted
    except Exception:
        logger.debug("Failed to promote emotional patterns for user %s", user_id)
        return 0


def _get_runtime_factory() -> Callable[..., object] | None:
    from anima_server.db.runtime import get_runtime_session_factory

    try:
        return get_runtime_session_factory()
    except RuntimeError:
        return None
    except Exception:
        logger.exception("Background memory consolidation task failed")
