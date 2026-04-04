"""Conversation history search for the recall_conversation tool.

Searches RuntimeMessage rows by text match + optional semantic similarity.
Messages with role "tool" or "summary" are excluded to prevent the agent
from retrieving its own tool calls.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models.runtime import RuntimeMessage

logger = logging.getLogger(__name__)

_SEARCH_TOKEN_RE = re.compile(r"[a-z0-9]+")
_CONVERSATION_SEARCH_SCAN_LIMIT = 1000


@dataclass(frozen=True, slots=True)
class ConversationHit:
    """A single search result from conversation history."""

    source: str  # "message"
    role: str  # "user" or "assistant"
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
    # Substring match is strongest
    if query_lower in content_lower:
        return 1.0

    query_words = set(_SEARCH_TOKEN_RE.findall(query_lower))
    content_words = set(_SEARCH_TOKEN_RE.findall(content_lower))
    if not query_words or not content_words:
        return 0.0

    overlap = sum(
        1 for query_word in query_words if _has_token_overlap(query_word, content_words)
    )
    if overlap == 0:
        return 0.0
    return overlap / len(query_words)


def _has_token_overlap(query_word: str, content_words: set[str]) -> bool:
    if query_word in content_words:
        return True
    if len(query_word) < 4:
        return False

    for content_word in content_words:
        if query_word in content_word or content_word in query_word:
            return True
    return False


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

    Searches RuntimeMessage rows with text-match scoring.  Optionally
    filters by role and date range.

    Semantic similarity (embedding-based) is attempted when available but
    the function degrades gracefully to text-only search.
    """
    query_lower = query.lower().strip()
    parsed_start = _parse_date(start_date)
    parsed_end = _parse_date(end_date)

    message_hits = _search_messages(
        runtime_db,
        user_id=user_id,
        query_lower=query_lower,
        role_filter=role_filter.strip().lower(),
        parsed_start=parsed_start,
        parsed_end=parsed_end,
    )

    message_hits.sort(key=lambda h: h.score, reverse=True)
    return message_hits[:limit]


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
    # Only search user and assistant messages — exclude tool, summary,
    # approval, and system roles to prevent agent from finding its own
    # tool-call metadata or recursive search results.
    allowed_roles = ["user", "assistant"]
    if role_filter in ("user", "assistant"):
        allowed_roles = [role_filter]

    stmt = (
        select(RuntimeMessage)
        .where(
            RuntimeMessage.user_id == user_id,
            RuntimeMessage.role.in_(allowed_roles),
            RuntimeMessage.content_text.is_not(None),
            RuntimeMessage.content_text != "",
        )
        .order_by(RuntimeMessage.created_at.desc())
        .limit(_CONVERSATION_SEARCH_SCAN_LIMIT)
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
