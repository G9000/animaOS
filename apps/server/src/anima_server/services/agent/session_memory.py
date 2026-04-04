"""Session-scoped working memory.

Provides per-thread scratch notes the AI can read/write during a conversation.
These are distinct from long-term MemoryItems — they capture in-session context
like "user seems tired today", "we're debugging a Python error", or
"user asked me to be more concise this session".

Session notes can be promoted to long-term memory if they prove important.

Notes now live in PG (RuntimeSessionNote) — no field-level encryption needed.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models.runtime_memory import RuntimeSessionNote

logger = logging.getLogger(__name__)


def get_session_notes(
    runtime_db: Session,
    *,
    thread_id: int,
    active_only: bool = True,
) -> list[RuntimeSessionNote]:
    """Get all session notes for a thread."""
    query = select(RuntimeSessionNote).where(RuntimeSessionNote.thread_id == thread_id)
    if active_only:
        query = query.where(RuntimeSessionNote.is_active.is_(True))
    query = query.order_by(RuntimeSessionNote.created_at.desc())
    return list(runtime_db.scalars(query).all())


def write_session_note(
    runtime_db: Session,
    *,
    thread_id: int,
    user_id: int,
    key: str,
    value: str,
    note_type: str = "observation",
) -> RuntimeSessionNote:
    """Write or update a session note. If a note with the same key exists, update it."""
    key = key.strip()[:128]
    value = value.strip()[:2000]

    if note_type not in ("observation", "plan", "context", "emotion"):
        note_type = "observation"

    # Check for existing note with same key
    existing = runtime_db.scalar(
        select(RuntimeSessionNote).where(
            RuntimeSessionNote.thread_id == thread_id,
            RuntimeSessionNote.key == key,
            RuntimeSessionNote.is_active.is_(True),
        )
    )

    if existing is not None:
        existing.value = value
        existing.note_type = note_type
        runtime_db.flush()
        return existing

    # Enforce max active notes — deactivate oldest if at limit
    active_count = _count_active_notes(runtime_db, thread_id)
    if active_count >= settings.agent_session_memory_max_notes:
        _deactivate_oldest_note(runtime_db, thread_id)

    note = RuntimeSessionNote(
        thread_id=thread_id,
        user_id=user_id,
        key=key,
        value=value,
        note_type=note_type,
    )
    runtime_db.add(note)
    runtime_db.flush()
    return note


def remove_session_note(
    runtime_db: Session,
    *,
    thread_id: int,
    key: str,
) -> bool:
    """Deactivate a session note by key. Returns True if found."""
    note = runtime_db.scalar(
        select(RuntimeSessionNote).where(
            RuntimeSessionNote.thread_id == thread_id,
            RuntimeSessionNote.key == key,
            RuntimeSessionNote.is_active.is_(True),
        )
    )
    if note is None:
        return False
    note.is_active = False
    runtime_db.flush()
    return True


def promote_session_note(
    runtime_db: Session,
    *,
    thread_id: int,
    user_id: int,
    key: str,
    category: str = "fact",
    importance: int = 3,
    tags: list[str] | None = None,
    db: Session | None = None,
) -> bool:
    """Promote a session note to a memory candidate for Soul Writer promotion.

    Creates a ``MemoryCandidate`` in PG (Soul Writer promotes it later).
    Falls back to the legacy ``add_memory_item`` path when *db*
    is provided and no runtime_db candidate path is available.

    Returns ``True`` if the note was found and promoted, ``False`` otherwise.
    """
    note = runtime_db.scalar(
        select(RuntimeSessionNote).where(
            RuntimeSessionNote.thread_id == thread_id,
            RuntimeSessionNote.key == key,
            RuntimeSessionNote.is_active.is_(True),
        )
    )
    if note is None:
        return False

    content = note.value  # plaintext — no decryption needed

    from anima_server.services.agent.candidate_ops import create_memory_candidate

    create_memory_candidate(
        runtime_db,
        user_id=user_id,
        content=content,
        category=category,
        importance=importance,
        importance_source="user_explicit",
        source="tool",
        tags=tags,
    )

    note.is_active = False
    runtime_db.flush()
    return True


def clear_session_notes(
    runtime_db: Session,
    *,
    thread_id: int,
) -> int:
    """Deactivate all session notes for a thread. Returns count cleared."""
    notes = get_session_notes(runtime_db, thread_id=thread_id, active_only=True)
    for note in notes:
        note.is_active = False
    runtime_db.flush()
    return len(notes)


def render_session_memory_text(notes: list[RuntimeSessionNote], *, user_id: int = 0) -> str:
    """Render session notes into a text block for the system prompt, respecting budget."""
    if not notes:
        return ""

    lines: list[str] = []
    total_len = 0

    for note in notes:
        note_value = note.value  # plaintext — no decryption needed
        line = f"[{note.note_type}] {note.key}: {note_value}"
        if total_len + len(line) > settings.agent_session_memory_budget_chars:
            break
        lines.append(line)
        total_len += len(line)

    return "\n".join(lines)


def _count_active_notes(runtime_db: Session, thread_id: int) -> int:
    from sqlalchemy import func

    return (
        runtime_db.scalar(
            select(func.count(RuntimeSessionNote.id)).where(
                RuntimeSessionNote.thread_id == thread_id,
                RuntimeSessionNote.is_active.is_(True),
            )
        )
        or 0
    )


def _deactivate_oldest_note(runtime_db: Session, thread_id: int) -> None:
    oldest = runtime_db.scalar(
        select(RuntimeSessionNote)
        .where(
            RuntimeSessionNote.thread_id == thread_id,
            RuntimeSessionNote.is_active.is_(True),
        )
        .order_by(RuntimeSessionNote.created_at.asc())
        .limit(1)
    )
    if oldest is not None:
        oldest.is_active = False
        runtime_db.flush()
