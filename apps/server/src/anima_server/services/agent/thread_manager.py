"""Thread lifecycle management: listing, creation, and archive reactivation."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models.runtime import (
    RuntimeMessage,
    RuntimeThread,
)
from anima_server.services.corefs.sealed_runtime import seal_runtime_fields

logger = logging.getLogger(__name__)


_THREAD_TITLE_SPLIT_RE = re.compile(r"(?:\r?\n+|[.!?](?=\s|$)|\s+[\u2014\u2013-]\s+|:\s+)")
_THREAD_TITLE_WORD_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9']*")
_THREAD_TITLE_PREFIXES = (
    "can you ",
    "could you ",
    "would you ",
    "please ",
    "help me with ",
    "help me ",
    "i need help with ",
    "i need help ",
    "tell me about ",
    "lets talk about ",
    "let's talk about ",
    "what do you think about ",
    "i want to talk about ",
    "i want to ",
    "how do i ",
    "how can i ",
)
_THREAD_TITLE_STOPWORDS = frozenset(
    {
        "a",
        "about",
        "an",
        "and",
        "are",
        "at",
        "be",
        "for",
        "i",
        "im",
        "in",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "us",
        "we",
        "with",
        "you",
        "your",
    }
)
_GENERIC_THREAD_MESSAGES = frozenset(
    {
        "hello",
        "hey",
        "hi",
        "new chat",
        "new conversation",
        "test",
        "testing",
        "yo",
    }
)


_STRIP_CHARS = " -\u2013\u2014:;,.!?"


def _normalize_thread_title_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(_STRIP_CHARS)


def _strip_thread_title_prefixes(text: str) -> str:
    stripped = text
    while stripped:
        candidate = stripped.lstrip()
        lowered = candidate.lower()

        if lowered.startswith(("hey ", "hi ", "hello ")):
            parts = candidate.split(maxsplit=1)
            stripped = parts[1] if len(parts) == 2 else ""
            continue

        matched_prefix = next(
            (prefix for prefix in _THREAD_TITLE_PREFIXES if lowered.startswith(prefix)),
            None,
        )
        if matched_prefix is None:
            return candidate.strip(_STRIP_CHARS)

        stripped = candidate[len(matched_prefix) :]

    return ""


def _derive_thread_title(user_message: str) -> str | None:
    text = _normalize_thread_title_text(user_message)
    if not text:
        return None

    first_clause = _THREAD_TITLE_SPLIT_RE.split(text, maxsplit=1)[0]
    cleaned = _normalize_thread_title_text(_strip_thread_title_prefixes(first_clause))
    if not cleaned:
        return None

    lowered = cleaned.lower()
    if lowered in _GENERIC_THREAD_MESSAGES:
        return None

    meaningful_words = [
        word.lower()
        for word in _THREAD_TITLE_WORD_RE.findall(cleaned)
        if word.lower() not in _THREAD_TITLE_STOPWORDS
    ]
    if len(meaningful_words) < 2:
        return None

    if len(cleaned) > 48:
        truncated = cleaned[:48].rsplit(" ", maxsplit=1)[0].rstrip(_STRIP_CHARS)
        cleaned = truncated or cleaned[:48].rstrip(_STRIP_CHARS)
        cleaned = f"{cleaned}..."

    return cleaned[:1].upper() + cleaned[1:]


def reactivate_thread_if_needed(
    db: Session,
    *,
    thread: RuntimeThread,
    user_id: int,
    transcripts_dir: Path | None,
    dek: bytes | None,
) -> list[int]:
    """Reactivate through authenticated Core conversation authority."""
    from anima_server.services.corefs.conversation_authority import (
        active_conversation_authority_session,
    )
    from anima_server.services.corefs.conversation_mutations import reactivate_canonical_thread

    session = active_conversation_authority_session(user_id)
    if session is None or thread.user_id != user_id:
        raise RuntimeError("Thread reactivation requires authenticated Core conversation authority")
    view = reactivate_canonical_thread(session=session, thread_id=thread.id)
    if view is None:
        raise ValueError("Thread not found in authenticated Core")
    displaced = _close_other_active_threads(db, user_id=user_id, keep_thread_id=thread.id)
    _set_active(thread)
    return displaced


def _close_other_active_threads(db: Session, *, user_id: int, keep_thread_id: int) -> list[int]:
    """Close every active thread for ``user_id`` except ``keep_thread_id``."""
    from datetime import UTC, datetime

    others = list(
        db.scalars(
            select(RuntimeThread).where(
                RuntimeThread.user_id == user_id,
                RuntimeThread.status == "active",
                RuntimeThread.id != keep_thread_id,
            )
        ).all()
    )
    now = datetime.now(UTC)
    closed_ids: list[int] = []
    for other in others:
        other.status = "closed"
        other.closed_at = now
        other.updated_at = now
        closed_ids.append(other.id)
    return closed_ids


def _set_active(thread: RuntimeThread) -> None:
    from datetime import UTC, datetime

    thread.status = "active"
    thread.is_archived = False
    thread.closed_at = None
    thread.updated_at = datetime.now(UTC)


def get_thread_messages_for_display(
    db: Session,
    *,
    thread: RuntimeThread,
    user_id: int,
    transcripts_dir: Path | None,
    dek: bytes | None,
) -> list[dict]:
    """Project UI history from authenticated canonical conversation objects."""
    from anima_server.services.corefs.conversation_authority import (
        active_conversation_authority_session,
        canonical_messages_for_display,
        get_canonical_thread,
    )

    session = active_conversation_authority_session(user_id)
    if session is None or thread.user_id != user_id:
        raise RuntimeError("Thread display requires authenticated Core conversation authority")
    view = get_canonical_thread(session=session, thread_id=thread.id)
    if view is None:
        return []
    return canonical_messages_for_display(view)


def _display_role(msg: RuntimeMessage) -> str:
    if msg.role == "tool" and msg.tool_name == "send_message":
        return "assistant"
    return msg.role


def maybe_set_thread_title(
    db: Session,
    thread: RuntimeThread,
    user_message: str,
) -> None:
    """Set thread.title from the first substantive user message if not already set."""
    if thread.title is not None:
        return

    title = _derive_thread_title(user_message)
    if title is not None:
        seal_runtime_fields(
            db,
            row=thread,
            row_type="runtime_thread",
            owner_id=int(thread.user_id),
            payload={"title": title},
            placeholders={"title": None},
        )
