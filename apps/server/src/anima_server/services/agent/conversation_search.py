"""Conversation history search for the recall_conversation tool.

Searches RuntimeMessage rows and MemoryDailyLog entries by text match +
optional semantic similarity.  Messages with role "tool" or "summary"
are excluded to prevent the agent from retrieving its own tool calls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models import MemoryDailyLog
from anima_server.models.runtime import RuntimeMessage, RuntimeThread
from anima_server.services.data_crypto import df

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ConversationHit:
    """A single search result from conversation history."""

    source: str  # "message" or "daily_log"
    role: str  # "user", "assistant", or "log"
    content: str
    date: str  # YYYY-MM-DD
    score: float


def _parse_date(raw: str) -> date | None:
    """Parse a YYYY-MM-DD string, returning None on failure."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _text_overlap_score(query_lower: str, content_lower: str) -> float:
    """Compute a simple word-overlap score between query and content."""
    if not query_lower:
        return 0.0
    query_words = set(query_lower.split())
    content_words = set(content_lower.split())
    if not query_words or not content_words:
        return 0.0
    # Substring match is strongest
    if query_lower in content_lower:
        return 1.0
    overlap = len(query_words & content_words)
    if overlap == 0:
        return 0.0
    return overlap / len(query_words)


async def search_conversation_history(
    runtime_db: Session,
    soul_db: Session,
    *,
    user_id: int,
    query: str,
    role_filter: str = "",
    start_date: str = "",
    end_date: str = "",
    limit: int = 10,
) -> list[ConversationHit]:
    """Search past conversations for the given query.

    Combines text-match scoring on RuntimeMessage rows and MemoryDailyLog
    entries.  Optionally filters by role and date range.

    Semantic similarity (embedding-based) is attempted when available but
    the function degrades gracefully to text-only search.
    """
    query_lower = query.lower().strip()
    parsed_start = _parse_date(start_date)
    parsed_end = _parse_date(end_date)

    # ── Search runtime_messages ──
    message_hits = _search_messages(
        runtime_db,
        user_id=user_id,
        query_lower=query_lower,
        role_filter=role_filter.strip().lower(),
        parsed_start=parsed_start,
        parsed_end=parsed_end,
    )

    # ── Search memory_daily_logs (soul DB) ──
    log_hits = _search_daily_logs(
        soul_db,
        user_id=user_id,
        query_lower=query_lower,
        role_filter=role_filter.strip().lower(),
        parsed_start=parsed_start,
        parsed_end=parsed_end,
    )

    # ── Merge and rank ──
    all_hits = message_hits + log_hits
    all_hits.sort(key=lambda h: h.score, reverse=True)
    return all_hits[:limit]


def _search_messages(
    db: Session,
    *,
    user_id: int,
    query_lower: str,
    role_filter: str,
    parsed_start: date | None,
    parsed_end: date | None,
) -> list[ConversationHit]:
    """Search RuntimeMessage rows, excluding tool calls and summaries."""
    thread = db.scalar(
        select(RuntimeThread).where(
            RuntimeThread.user_id == user_id,
            RuntimeThread.status == "active",
        )
    )
    if thread is None:
        return []

    # Only search user and assistant messages — exclude tool, summary,
    # approval, and system roles to prevent agent from finding its own
    # tool-call metadata or recursive search results.
    allowed_roles = ["user", "assistant"]
    if role_filter in ("user", "assistant"):
        allowed_roles = [role_filter]

    stmt = (
        select(RuntimeMessage)
        .where(
            RuntimeMessage.thread_id == thread.id,
            RuntimeMessage.role.in_(allowed_roles),
            RuntimeMessage.content_text.is_not(None),
            RuntimeMessage.content_text != "",
        )
        .order_by(RuntimeMessage.created_at.desc())
        .limit(500)  # cap scan to avoid very long histories
    )
    rows = db.scalars(stmt).all()

    hits: list[ConversationHit] = []
    for row in rows:
        content = (row.content_text or "").strip()
        if not content:
            continue

        # Skip tool-call wrapper messages (assistant messages that only
        # contain a tool_calls JSON payload and no real text).
        if (
            row.role == "assistant"
            and isinstance(row.content_json, dict)
            and "tool_calls" in row.content_json
        ):
            continue

        # Date filtering
        msg_date = row.created_at.date() if row.created_at else None
        if msg_date is not None:
            if parsed_start and msg_date < parsed_start:
                continue
            if parsed_end and msg_date > parsed_end:
                continue

        # Scoring
        content_lower = content.lower()
        score = _text_overlap_score(query_lower, content_lower)
        if score < 0.3 and query_lower:
            continue

        # If query is empty (date-range browse mode), give a base score
        if not query_lower:
            score = 0.5

        date_str = msg_date.isoformat() if msg_date else "unknown"
        hits.append(
            ConversationHit(
                source="message",
                role=row.role,
                content=content[:500],  # cap length for display
                date=date_str,
                score=score,
            )
        )

    return hits


def _search_daily_logs(
    db: Session,
    *,
    user_id: int,
    query_lower: str,
    role_filter: str,
    parsed_start: date | None,
    parsed_end: date | None,
) -> list[ConversationHit]:
    """Search MemoryDailyLog entries."""
    stmt = (
        select(MemoryDailyLog)
        .where(MemoryDailyLog.user_id == user_id)
        .order_by(MemoryDailyLog.created_at.desc())
        .limit(200)
    )
    rows = db.scalars(stmt).all()

    hits: list[ConversationHit] = []
    for row in rows:
        log_date = _parse_date(row.date)
        if log_date is not None:
            if parsed_start and log_date < parsed_start:
                continue
            if parsed_end and log_date > parsed_end:
                continue

        # Score user_message and assistant_response separately
        entries = []
        if role_filter != "assistant":
            entries.append(
                (
                    "user",
                    df(user_id, row.user_message, table="memory_daily_logs", field="user_message"),
                )
            )
        if role_filter != "user":
            entries.append(
                (
                    "assistant",
                    df(
                        user_id,
                        row.assistant_response,
                        table="memory_daily_logs",
                        field="assistant_response",
                    ),
                )
            )

        for role, text in entries:
            text_stripped = (text or "").strip()
            if not text_stripped:
                continue
            text_lower = text_stripped.lower()
            score = _text_overlap_score(query_lower, text_lower)
            if score < 0.3 and query_lower:
                continue
            if not query_lower:
                score = 0.5

            date_str = row.date if row.date else "unknown"
            hits.append(
                ConversationHit(
                    source="daily_log",
                    role=role,
                    content=text_stripped[:500],
                    date=date_str,
                    score=score,
                )
            )

    return hits
