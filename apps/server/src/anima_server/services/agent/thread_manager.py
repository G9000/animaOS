"""Thread lifecycle management: listing, creation, and archive reactivation."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models.runtime import RuntimeMessage, RuntimeThread

logger = logging.getLogger(__name__)


_THREAD_TITLE_SPLIT_RE = re.compile(
    r"(?:\r?\n+|[.!?](?=\s|$)|\s+[\u2014\u2013-]\s+|:\s+)")
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

        stripped = candidate[len(matched_prefix):]

    return ""


def _derive_thread_title(user_message: str) -> str | None:
    text = _normalize_thread_title_text(user_message)
    if not text:
        return None

    first_clause = _THREAD_TITLE_SPLIT_RE.split(text, maxsplit=1)[0]
    cleaned = _normalize_thread_title_text(
        _strip_thread_title_prefixes(first_clause))
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
        truncated = cleaned[:48].rsplit(" ", maxsplit=1)[
            0].rstrip(_STRIP_CHARS)
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
) -> None:
    """Reactivate a closed/archived thread so the agent can continue it.

    If PG messages still exist (within TTL), just flip status to active.
    If messages are gone, rehydrate from JSONL archive and insert a summary
    system message so the agent has context without loading raw history.
    """
    has_pg_messages = db.scalar(
        select(RuntimeMessage.id)
        .where(RuntimeMessage.thread_id == thread.id)
        .limit(1)
    ) is not None

    if has_pg_messages:
        _set_active(thread)
        return

    summary = "Previous conversation"
    if transcripts_dir is not None:
        messages, summary = _load_from_archive(
            transcripts_dir, thread_id=thread.id, dek=dek)
        if messages:
            _bulk_insert_archived_history(
                db, thread=thread, user_id=user_id, messages=messages)

    _insert_summary_message(
        db, thread=thread, user_id=user_id, summary=summary)
    _set_active(thread)


def _set_active(thread: RuntimeThread) -> None:
    from datetime import UTC, datetime
    thread.status = "active"
    thread.is_archived = False
    thread.closed_at = None
    thread.updated_at = datetime.now(UTC)


def _load_from_archive(
    transcripts_dir: Path,
    *,
    thread_id: int,
    dek: bytes | None,
) -> tuple[list[dict], str]:
    """Find and decrypt the JSONL archive for a thread. Returns (messages, summary)."""
    from anima_server.services.agent.transcript_archive import decrypt_transcript

    candidates = list(transcripts_dir.glob(f"*_thread-{thread_id}.jsonl*"))
    enc_candidates = [p for p in candidates if p.suffix in (".jsonl", ".enc")]
    if not enc_candidates:
        logger.warning("No transcript archive found for thread %d", thread_id)
        return [], "Previous conversation"

    enc_path = sorted(enc_candidates)[-1]
    meta_path = enc_path.parent / \
        enc_path.name.replace(".jsonl.enc", ".meta.json").replace(
            ".jsonl", ".meta.json")

    summary = "Previous conversation"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("summary"):
                summary = str(meta["summary"])
        except (OSError, json.JSONDecodeError):
            pass

    try:
        messages = decrypt_transcript(enc_path, dek=dek, thread_id=thread_id)
    except Exception:
        logger.exception(
            "Failed to decrypt transcript for thread %d", thread_id)
        return [], summary

    return messages, summary


def _bulk_insert_archived_history(
    db: Session,
    *,
    thread: RuntimeThread,
    user_id: int,
    messages: list[dict],
) -> None:
    """Insert JSONL messages into runtime_messages with is_archived_history=True."""
    max_seq = thread.next_message_sequence
    inserted_count = 0
    for msg in messages:
        role = str(msg.get("role", "user"))
        content = str(msg.get("content", ""))
        if not content and role in ("user", "assistant"):
            continue
        db.add(
            RuntimeMessage(
                thread_id=thread.id,
                user_id=user_id,
                sequence_id=max_seq + inserted_count,
                role=role,
                content_text=content,
                is_in_context=False,
                is_archived_history=True,
            )
        )
        inserted_count += 1
    thread.next_message_sequence = max_seq + inserted_count
    db.flush()


def _insert_summary_message(
    db: Session,
    *,
    thread: RuntimeThread,
    user_id: int,
    summary: str,
) -> None:
    """Insert a system message summarizing the previous conversation."""
    seq = thread.next_message_sequence
    db.add(
        RuntimeMessage(
            thread_id=thread.id,
            user_id=user_id,
            sequence_id=seq,
            role="system",
            content_text=f"[Previous conversation summary]: {summary}",
            is_in_context=True,
            is_archived_history=False,
        )
    )
    thread.next_message_sequence = seq + 1
    db.flush()


def get_thread_messages_for_display(
    db: Session,
    *,
    thread: RuntimeThread,
    user_id: int,
    transcripts_dir: Path | None,
    dek: bytes | None,
) -> list[dict]:
    """Return all messages for UI display in chronological order.

    Active threads: query runtime_messages (all rows, including archived history).
    Archived threads (no PG messages): read from JSONL.
    """
    pg_messages = db.scalars(
        select(RuntimeMessage)
        .where(
            RuntimeMessage.thread_id == thread.id,
            RuntimeMessage.role.in_(("user", "assistant", "tool")),
        )
        .order_by(RuntimeMessage.sequence_id)
    ).all()

    if pg_messages:
        return [
            {
                "role": _display_role(m),
                "content": m.content_text or "",
                "ts": m.created_at.isoformat() if m.created_at else None,
                "isArchivedHistory": m.is_archived_history,
            }
            for m in pg_messages
            if not m.is_internal
        ]

    if transcripts_dir is None:
        return []
    messages, _summary = _load_from_archive(
        transcripts_dir, thread_id=thread.id, dek=dek)
    return [
        {
            "role": str(m.get("role", "user")),
            "content": str(m.get("content", "")),
            "ts": m.get("ts"),
            "isArchivedHistory": True,
        }
        for m in messages
        if m.get("role") in ("user", "assistant")
    ]


def _display_role(msg: RuntimeMessage) -> str:
    if msg.role == "tool" and msg.tool_name == "send_message":
        return "assistant"
    return msg.role


def maybe_set_thread_title(thread: RuntimeThread, user_message: str) -> None:
    """Set thread.title from the first substantive user message if not already set."""
    if thread.title is not None:
        return

    title = _derive_thread_title(user_message)
    if title is not None:
        thread.title = title
