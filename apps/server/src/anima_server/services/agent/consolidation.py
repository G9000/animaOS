from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from anima_server.config import settings
from anima_server.services.agent.memory_store import (
    add_daily_log,
    set_current_focus,
    store_memory_item,
    supersede_memory_item,
)
from anima_server.services.agent.claims import upsert_claim
from anima_server.services.data_crypto import df

logger = logging.getLogger(__name__)

_background_tasks_lock = Lock()
_background_tasks: set[asyncio.Task[None]] = set()

EXTRACTION_PROMPT = """You are a memory extraction system for a personal AI companion.
Given a conversation turn between a user and an assistant, extract personal facts and preferences about the user.

The assistant response may include two sections:
- [Agent's inner reasoning]: The assistant's private thoughts about the conversation. These contain useful observations about the user that should be extracted as memories.
- [Agent's response to user]: What the assistant actually said.

Both sections are valid sources for extraction.

Return a JSON object with two fields:

"memories": a JSON array. Each item:
- "content": concise statement (e.g. "Works as a software engineer")
- "category": one of "fact", "preference", "goal", "relationship"
- "importance": 1-5 (5 = identity-defining like name/age/occupation, 1 = casual mention)

"emotion": detect the user's emotional tone (or null if nothing notable):
- "emotion": primary emotion (frustrated, excited, anxious, calm, stressed, relieved, curious, disappointed, or null)
- "confidence": 0.0-1.0
- "trajectory": escalating, de-escalating, stable, or shifted
- "evidence_type": explicit, linguistic, behavioral, or contextual
- "evidence": what specifically indicated this

Rules for memories:
- Extract what the user explicitly stated or clearly implied
- Also extract observations from the agent's inner reasoning (e.g. "User seems stressed about deadline" → "Currently stressed about work deadline")
- Do not fabricate — only extract what is supported by the text
- Use empty array [] if nothing worth remembering

Rules for emotion:
- Only report if confidence > 0.4
- Set emotion to null if nothing notable
- The agent's inner reasoning about the user's emotions is strong evidence

User message:
{user_message}

Assistant response:
{assistant_response}"""

CONFLICT_CHECK_PROMPT = """Given an EXISTING memory and a NEW memory about the same user, determine if the new one updates/replaces the existing one, or if they are about different topics.

Respond with exactly one word: UPDATE or DIFFERENT

EXISTING: {existing}
NEW: {new_content}"""


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
                result.conflicts_resolved.append(
                    f"{write_result.matched_item.content} -> {fact}"
                )

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
                result.conflicts_resolved.append(
                    f"{write_result.matched_item.content} -> {pref}"
                )

        if extracted.current_focus:
            set_current_focus(db, user_id=user_id,
                              focus=extracted.current_focus)
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

    extraction = await extract_memories_via_llm(
        user_message=user_message,
        assistant_response=assistant_response,
    )
    llm_items = extraction.memories

    from anima_server.db.session import SessionLocal

    factory = db_factory or SessionLocal

    # Record any emotional signal extracted alongside memories
    emotion_data = extraction.emotion
    if emotion_data and emotion_data.get("emotion"):
        try:
            from anima_server.services.agent.emotional_intelligence import record_emotional_signal

            with factory() as _edb:
                record_emotional_signal(
                    _edb,
                    user_id=user_id,
                    emotion=str(emotion_data["emotion"]),
                    confidence=float(emotion_data.get("confidence", 0.5)),
                    evidence_type=str(emotion_data.get(
                        "evidence_type", "linguistic")),
                    evidence=str(emotion_data.get("evidence", "")),
                    trajectory=str(emotion_data.get("trajectory", "stable")),
                )
                _edb.commit()
        except Exception:  # noqa: BLE001
            logger.debug("Failed to record emotional signal from extraction")

    if not llm_items:
        return result
    regex_contents = {c.lower()
                      for c in result.facts_added + result.preferences_added}

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
                except Exception:  # noqa: BLE001
                    logger.debug("Claim dual-write failed for: %s", content)
                continue

            if write_result.action == "superseded":
                result.conflicts_resolved.append(
                    f"{write_result.matched_item.content} -> {content}"
                )
                result.llm_items_added.append(content)
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
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "Claim dual-write (supersede) failed for: %s", content)
                continue

            if write_result.action == "duplicate":
                continue

            if write_result.action == "similar" and write_result.similar_items:
                resolution = await resolve_conflict(
                    existing_content=df(user_id, write_result.similar_items[0].content),
                    new_content=content,
                )
                if resolution == "UPDATE":
                    updated_item = supersede_memory_item(
                        db,
                        old_item_id=write_result.similar_items[0].id,
                        new_content=content,
                        importance=importance,
                    )
                    if updated_item is not None:
                        result.conflicts_resolved.append(
                            f"{df(user_id, write_result.similar_items[0].content)} -> {content}"
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
                        except Exception:  # noqa: BLE001
                            logger.debug(
                                "Claim dual-write (update) failed for: %s", content)
                elif resolution == "DIFFERENT":
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
                                memory_item_id=create_result.item.id if create_result.item else None,
                                evidence_text=user_message,
                            )
                        except Exception:  # noqa: BLE001
                            logger.debug(
                                "Claim dual-write (different) failed for: %s", content)

        db.commit()

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
        from anima_server.services.agent.messages import HumanMessage, SystemMessage
        from anima_server.services.agent.llm import create_llm

        llm = create_llm()
        prompt = EXTRACTION_PROMPT.format(
            user_message=user_message,
            assistant_response=assistant_response,
        )
        response = await llm.ainvoke([
            SystemMessage(
                content="You extract memories and emotions. Respond only with JSON."),
            HumanMessage(content=prompt),
        ])
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
    except Exception:  # noqa: BLE001
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
        from anima_server.services.agent.messages import HumanMessage, SystemMessage
        from anima_server.services.agent.llm import create_llm

        llm = create_llm()
        prompt = CONFLICT_CHECK_PROMPT.format(
            existing=existing_content,
            new_content=new_content,
        )
        response = await llm.ainvoke([
            SystemMessage(
                content="Respond with exactly one word: UPDATE or DIFFERENT"),
            HumanMessage(content=prompt),
        ])
        content = getattr(response, "content", "").strip().upper()
        if content in ("UPDATE", "DIFFERENT"):
            return content
        return "DIFFERENT"
    except Exception:  # noqa: BLE001
        logger.exception("LLM conflict resolution failed")
        return "DIFFERENT"


def _parse_json_object(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from LLM response text."""
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _parse_json_array(text: str) -> list[dict[str, Any]]:
    """Extract a JSON array from LLM response text."""
    text = text.strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        parsed = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def extract_turn_memory(user_message: str) -> ExtractedTurnMemory:
    facts = tuple(extract_pattern_items(user_message, _FACT_EXTRACTORS))
    preferences = tuple(extract_pattern_items(
        user_message, _PREFERENCE_EXTRACTORS))
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

    except Exception:  # noqa: BLE001
        logger.exception(
            "Background memory consolidation failed for user %s", user_id)

    # Opportunistic embedding backfill for items without embeddings
    try:
        await _backfill_user_embeddings(user_id, db_factory=db_factory)
    except Exception:  # noqa: BLE001
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
    db_factory: Callable[..., object] | None = None,
) -> None:
    if not settings.agent_background_memory_enabled:
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

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
    except Exception:  # noqa: BLE001
        logger.exception("Background memory consolidation task failed")
