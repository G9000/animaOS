from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock

from anima_server.config import settings
from anima_server.services.agent.memory_store import (
    FACTS_PATH,
    PREFERENCES_PATH,
    append_daily_log_entry,
    append_unique_bullets,
    write_current_focus,
)

logger = logging.getLogger(__name__)

_background_tasks_lock = Lock()
_background_tasks: set[asyncio.Task[None]] = set()


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
    daily_log_path: str | None = None
    facts_added: list[str] = field(default_factory=list)
    preferences_added: list[str] = field(default_factory=list)
    current_focus_updated: str | None = None


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
) -> MemoryConsolidationResult:
    timestamp = now or datetime.now(UTC)
    result = MemoryConsolidationResult()
    daily_log_path = append_daily_log_entry(
        user_id,
        user_message=user_message,
        assistant_response=assistant_response,
        now=timestamp,
    )
    result.daily_log_path = daily_log_path.as_posix()

    extracted = extract_turn_memory(user_message)
    result.facts_added = append_unique_bullets(user_id, FACTS_PATH, extracted.facts)
    result.preferences_added = append_unique_bullets(
        user_id,
        PREFERENCES_PATH,
        extracted.preferences,
    )

    if extracted.current_focus and write_current_focus(user_id, extracted.current_focus):
        result.current_focus_updated = extracted.current_focus

    return result


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
) -> None:
    try:
        consolidate_turn_memory(
            user_id=user_id,
            user_message=user_message,
            assistant_response=assistant_response,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Background memory consolidation failed for user %s", user_id)


def schedule_background_memory_consolidation(
    *,
    user_id: int,
    user_message: str,
    assistant_response: str,
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
