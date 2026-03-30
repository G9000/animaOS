"""Session-scoped working memory.

Provides per-thread scratch notes the AI can read/write during a conversation.
These are distinct from long-term MemoryItems — they capture in-session context
like "user seems tired today", "we're debugging a Python error", or
"user asked me to be more concise this session".

Session notes can be promoted to long-term memory if they prove important.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models import MemoryItem, SessionNote
from anima_server.services.data_crypto import df, ef

logger = logging.getLogger(__name__)


def get_session_notes(
    db: Session,
    *,
    thread_id: int,
    active_only: bool = True,
) -> list[SessionNote]:
    """Get all session notes for a thread."""
    query = select(SessionNote).where(SessionNote.thread_id == thread_id)
    if active_only:
        query = query.where(SessionNote.is_active.is_(True))
    query = query.order_by(SessionNote.updated_at.desc())
    return list(db.scalars(query).all())


def write_session_note(
    db: Session,
    *,
    thread_id: int,
    user_id: int,
    key: str,
    value: str,
    note_type: str = "observation",
) -> SessionNote:
    """Write or update a session note. If a note with the same key exists, update it."""
    key = key.strip()[:128]
    value = value.strip()[:2000]

    if note_type not in ("observation", "plan", "context", "emotion"):
        note_type = "observation"

    # Check for existing note with same key
    existing = db.scalar(
        select(SessionNote).where(
            SessionNote.thread_id == thread_id,
            SessionNote.key == key,
            SessionNote.is_active.is_(True),
        )
    )

    if existing is not None:
        existing.value = ef(user_id, value, table="session_notes", field="value")
        existing.note_type = note_type
        existing.updated_at = datetime.now(UTC)
        db.flush()
        return existing

    # Enforce max active notes — deactivate oldest if at limit
    active_count = _count_active_notes(db, thread_id)
    if active_count >= settings.agent_session_memory_max_notes:
        _deactivate_oldest_note(db, thread_id)

    note = SessionNote(
        thread_id=thread_id,
        user_id=user_id,
        key=key,
        value=ef(user_id, value, table="session_notes", field="value"),
        note_type=note_type,
    )
    db.add(note)
    db.flush()
    return note


def remove_session_note(
    db: Session,
    *,
    thread_id: int,
    key: str,
) -> bool:
    """Deactivate a session note by key. Returns True if found."""
    note = db.scalar(
        select(SessionNote).where(
            SessionNote.thread_id == thread_id,
            SessionNote.key == key,
            SessionNote.is_active.is_(True),
        )
    )
    if note is None:
        return False
    note.is_active = False
    note.updated_at = datetime.now(UTC)
    db.flush()
    return True


def promote_session_note(
    db: Session,
    *,
    thread_id: int,
    user_id: int,
    key: str,
    category: str = "fact",
    importance: int = 3,
    tags: list[str] | None = None,
    runtime_db: Session | None = None,
) -> bool:
    """Promote a session note to a memory candidate for Soul Writer promotion.

    When *runtime_db* is provided the note content is written to
    ``MemoryCandidate`` in PG (Soul Writer promotes it later).
    Falls back to the legacy ``add_memory_item`` path when *runtime_db*
    is ``None``.

    Returns ``True`` if the note was found and promoted, ``False`` otherwise.
    """
    note = db.scalar(
        select(SessionNote).where(
            SessionNote.thread_id == thread_id,
            SessionNote.key == key,
            SessionNote.is_active.is_(True),
        )
    )
    if note is None:
        return False

    content = df(user_id, note.value, table="session_notes", field="value")

    if runtime_db is not None:
        from anima_server.services.agent.candidate_ops import create_memory_candidate

        create_memory_candidate(
            runtime_db,
            user_id=user_id,
            content=content,
            category=category,
            importance=importance,
            importance_source="user_explicit",
            source="tool",
        )
    else:
        from anima_server.services.agent.memory_store import add_memory_item

        item = add_memory_item(
            db,
            user_id=user_id,
            content=content,
            category=category,
            importance=importance,
            source="session",
            tags=tags,
        )
        if item is not None:
            note.promoted_to_item_id = item.id

    note.is_active = False
    note.updated_at = datetime.now(UTC)
    db.flush()
    return True


def clear_session_notes(
    db: Session,
    *,
    thread_id: int,
) -> int:
    """Deactivate all session notes for a thread. Returns count cleared."""
    notes = get_session_notes(db, thread_id=thread_id, active_only=True)
    for note in notes:
        note.is_active = False
        note.updated_at = datetime.now(UTC)
    db.flush()
    return len(notes)


def render_session_memory_text(notes: list[SessionNote], *, user_id: int = 0) -> str:
    """Render session notes into a text block for the system prompt, respecting budget."""
    if not notes:
        return ""

    lines: list[str] = []
    total_len = 0

    for note in notes:
        note_value = df(user_id, note.value, table="session_notes", field="value")
        line = f"[{note.note_type}] {note.key}: {note_value}"
        if total_len + len(line) > settings.agent_session_memory_budget_chars:
            break
        lines.append(line)
        total_len += len(line)

    return "\n".join(lines)


def _count_active_notes(db: Session, thread_id: int) -> int:
    from sqlalchemy import func

    return (
        db.scalar(
            select(func.count(SessionNote.id)).where(
                SessionNote.thread_id == thread_id,
                SessionNote.is_active.is_(True),
            )
        )
        or 0
    )


def _deactivate_oldest_note(db: Session, thread_id: int) -> None:
    oldest = db.scalar(
        select(SessionNote)
        .where(
            SessionNote.thread_id == thread_id,
            SessionNote.is_active.is_(True),
        )
        .order_by(SessionNote.updated_at.asc())
        .limit(1)
    )
    if oldest is not None:
        oldest.is_active = False
        oldest.updated_at = datetime.now(UTC)
        db.flush()
