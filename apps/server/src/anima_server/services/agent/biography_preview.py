"""Compile backend-grounded agent biography preview data."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models import AgentProfile, User
from anima_server.services.agent.memory_blocks import MemoryBlock, build_runtime_memory_blocks
from anima_server.services.agent.self_model import ensure_self_model_exists

_MAX_SECTION_CHARS = 900
_MAX_BIOGRAPHY_CHARS = 1400
_MAX_CONTEXT_CHARS = 220

_SECTION_SPECS: tuple[tuple[str, str, str], ...] = (
    ("soul", "origin", "Origin"),
    ("self_identity", "identity", "Core Identity"),
    ("persona", "persona", "Voice & Persona"),
    ("human", "human", "Human Context"),
    ("world", "world", "World Context"),
    ("user_directive", "user_directive", "User Directive"),
    ("self_inner_state", "inner_state", "Inner State"),
    ("self_working_memory", "working_memory", "Working Memory"),
    ("self_intentions", "intentions", "Active Intentions"),
    ("emotional_context", "emotional_context", "Emotional Context"),
    ("emotional_patterns", "emotional_patterns", "Emotional Patterns"),
    ("recent_episodes", "recent_episodes", "Recent Episodes"),
    ("current_focus", "current_focus", "Current Focus"),
    ("thread_summary", "thread_summary", "Thread Summary"),
    ("session_memory", "session_memory", "Session Memory"),
)

_CONTEXT_PRIORITY = (
    "self_inner_state",
    "self_working_memory",
    "self_intentions",
    "emotional_context",
    "recent_episodes",
    "human",
)


def build_agent_biography_preview(
    db: Session,
    *,
    user_id: int,
    runtime_db: Session | None = None,
) -> dict[str, Any]:
    """Return a deterministic preview of the context that shapes the agent."""
    ensure_self_model_exists(db, user_id=user_id)

    profile = db.scalar(
        select(AgentProfile).where(AgentProfile.user_id == user_id)
    )
    user = db.get(User, user_id)
    agent_type = profile.agent_type if profile is not None else "companion"

    blocks = list(
        build_runtime_memory_blocks(
            db,
            user_id=user_id,
            thread_id=0,
            runtime_db=runtime_db,
        )
    )
    blocks = _with_runtime_emotional_context(
        blocks,
        user_id=user_id,
        runtime_db=runtime_db,
        agent_type=agent_type,
    )
    by_label = _first_by_label(blocks)

    identity = _block_value(by_label, "self_identity")
    persona = _block_value(by_label, "persona")
    biography = _join_trimmed(
        [
            identity,
            persona,
            _block_value(by_label, "human"),
            _block_value(by_label, "world"),
        ],
        max_chars=_MAX_BIOGRAPHY_CHARS,
    )

    return {
        "userId": user_id,
        "agentName": _profile_value(profile.agent_name if profile else None, "Anima"),
        "relationship": _profile_value(profile.relationship if profile else None, "companion"),
        "agentType": agent_type,
        "avatarUrl": profile.avatar_url if profile is not None else None,
        "agentBirthday": _iso_seconds(
            (profile.agent_birthday or profile.created_at) if profile is not None else None
        ),
        "birthday": user.birthday if user is not None else None,
        "dominantEmotion": _dominant_emotion(runtime_db or db, user_id=user_id),
        "identityDraft": identity,
        "personaDraft": persona,
        "biography": biography,
        "contextLine": _context_line(by_label),
        "sections": _sections_from_blocks(by_label),
        "promptBlockLabels": [block.label for block in blocks],
    }


def _with_runtime_emotional_context(
    blocks: list[MemoryBlock],
    *,
    user_id: int,
    runtime_db: Session | None,
    agent_type: str,
) -> list[MemoryBlock]:
    if runtime_db is None or any(block.label == "emotional_context" for block in blocks):
        return blocks

    from anima_server.services.agent.memory_blocks import build_emotional_context_block

    emotional = build_emotional_context_block(
        runtime_db,
        user_id=user_id,
        agent_type=agent_type,
    )
    if emotional is None:
        return blocks
    return [*blocks, emotional]


def _first_by_label(blocks: list[MemoryBlock]) -> dict[str, MemoryBlock]:
    by_label: dict[str, MemoryBlock] = {}
    for block in blocks:
        by_label.setdefault(block.label, block)
    return by_label


def _profile_value(value: str | None, fallback: str) -> str:
    stripped = (value or "").strip()
    return stripped or fallback


def _iso_seconds(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(microsecond=0).isoformat()


def _block_value(blocks: dict[str, MemoryBlock], label: str) -> str:
    block = blocks.get(label)
    return block.value.strip() if block is not None else ""


def _sections_from_blocks(blocks: dict[str, MemoryBlock]) -> list[dict[str, str]]:
    sections: list[dict[str, str]] = []
    for label, section_id, title in _SECTION_SPECS:
        content = _block_value(blocks, label)
        if not content:
            continue
        sections.append(
            {
                "id": section_id,
                "title": title,
                "content": _trim(content, _MAX_SECTION_CHARS),
                "source": label,
            }
        )
    return sections


def _context_line(blocks: dict[str, MemoryBlock]) -> str:
    for label in _CONTEXT_PRIORITY:
        compact = _compact_line(_block_value(blocks, label))
        if compact:
            return compact
    return "No active backend context yet."


def _compact_line(value: str) -> str:
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^[#>*\-\s]+", "", line).strip()
        line = re.sub(r"^[A-Za-z][A-Za-z\s_-]{1,32}:\s+", "", line).strip()
        if line:
            return _trim(re.sub(r"\s+", " ", line), _MAX_CONTEXT_CHARS)
    return ""


def _join_trimmed(parts: list[str], *, max_chars: int) -> str:
    text = "\n\n".join(part.strip() for part in parts if part.strip())
    return _trim(text, max_chars)


def _trim(value: str, max_chars: int) -> str:
    stripped = value.strip()
    if len(stripped) <= max_chars:
        return stripped
    shortened = stripped[: max_chars + 1].rsplit(" ", 1)[0].strip()
    return f"{shortened or stripped[:max_chars].strip()}..."


def _dominant_emotion(db: Session, *, user_id: int) -> str | None:
    from anima_server.services.agent.emotional_intelligence import get_recent_signals

    try:
        signals = get_recent_signals(db, user_id=user_id, limit=5)
    except Exception:
        return None
    if not signals:
        return None

    scores: dict[str, float] = {}
    for signal in signals:
        scores[signal.emotion] = scores.get(signal.emotion, 0.0) + signal.confidence
    return max(scores, key=scores.get) if scores else None
