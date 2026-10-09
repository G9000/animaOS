"""Search authenticated native conversation objects."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from anima_server.services.agent.text_processing import unicode_lexical_tokens

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TranscriptSnippet:
    date: str
    thread_id: int
    text: str


class TranscriptSearchResults(list[TranscriptSnippet]):
    def __init__(
        self,
        snippets: list[TranscriptSnippet] | None = None,
        *,
        total_matches: int = 0,
    ) -> None:
        super().__init__(snippets or [])
        self.total_matches = total_matches


def _keyword_overlap_score(query: str, keywords: list[str]) -> float:
    """Score a sidecar's keywords against the search query."""
    query_words = set(unicode_lexical_tokens(query, min_word_chars=1))
    if not query_words or not keywords:
        return 0.0

    keyword_words: set[str] = set()
    for keyword in keywords:
        keyword_words.update(unicode_lexical_tokens(str(keyword), min_word_chars=1))

    if not keyword_words:
        return 0.0

    overlap = len(query_words & keyword_words)
    return overlap / max(len(query_words), 1)


def _text_overlap_score(query: str, text: str) -> float:
    """Score a transcript message against the search query."""
    if len(query.strip()) > 1 and query.casefold() in text.casefold():
        return 1.0
    query_words = set(unicode_lexical_tokens(query, min_word_chars=1))
    text_words = set(unicode_lexical_tokens(text, min_word_chars=1))
    if not query_words or not text_words:
        return 0.0
    overlap = len(query_words & text_words)
    return overlap / max(len(query_words), 1)


def _cutoff_for_days_back(days_back: int) -> datetime | None:
    if days_back <= 0:
        return None
    return datetime.now(UTC) - timedelta(days=days_back)


def search_transcripts(
    *,
    query: str,
    user_id: int,
    dek: bytes | None,
    transcripts_dir: Path,
    days_back: int = 30,
    max_transcripts: int = 5,
    max_snippets: int = 10,
    snippet_context: int = 2,
    budget_chars: int = 3000,
) -> list[TranscriptSnippet]:
    """Search archived transcripts and return context-windowed snippets.

    ``days_back`` values less than or equal to zero search all archived
    transcripts.
    """
    from anima_server.services.corefs.conversation_authority import (
        active_conversation_authority_session,
        list_canonical_threads,
    )

    authority_session = active_conversation_authority_session(user_id)
    if authority_session is not None:
        cutoff = _cutoff_for_days_back(days_back)
        candidates: list[tuple[float, TranscriptSnippet]] = []
        for view in list_canonical_threads(session=authority_session):
            legacy_id = view.document.legacy_thread_id
            thread_id = legacy_id if isinstance(legacy_id, int) else 0
            for message in view.messages:
                created_at = datetime.fromisoformat(message.created_at)
                if cutoff is not None and created_at < cutoff:
                    continue
                score = 0.5 if not query.strip() else _text_overlap_score(query, message.content)
                if score <= 0:
                    continue
                candidates.append(
                    (
                        score,
                        TranscriptSnippet(
                            date=created_at.date().isoformat(),
                            thread_id=thread_id,
                            text=f"{message.role.capitalize()}: {message.content}",
                        ),
                    )
                )
        candidates.sort(key=lambda item: (item[0], item[1].date), reverse=True)
        snippets: list[TranscriptSnippet] = []
        chars = 0
        for _score, snippet in candidates:
            if len(snippets) >= max_snippets or chars + len(snippet.text) > budget_chars:
                continue
            snippets.append(snippet)
            chars += len(snippet.text)
        return TranscriptSearchResults(snippets, total_matches=len(candidates))

    raise RuntimeError("Transcript search requires authenticated Core conversation authority")


def format_snippets(snippets: list[TranscriptSnippet]) -> str:
    """Format transcript snippets for tool output."""
    if not snippets:
        return "No matching transcripts found."
    parts = [
        f"[{snippet.date}, thread {snippet.thread_id}]\n{snippet.text}" for snippet in snippets
    ]
    total_matches = max(getattr(snippets, "total_matches", len(snippets)), len(snippets))
    remaining = total_matches - len(snippets)
    if remaining > 0:
        parts.append(
            f"({remaining} more matches found, use a more specific query to narrow results)"
        )
    return "\n\n".join(parts)
