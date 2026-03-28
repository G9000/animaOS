"""Search archived transcripts via sidecar filtering and decryption."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from anima_server.services.agent.transcript_archive import decrypt_transcript

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
    query_words = set(re.findall(r"[a-zA-Z]{3,}", query.lower()))
    if not query_words or not keywords:
        return 0.0

    keyword_words: set[str] = set()
    for keyword in keywords:
        keyword_words.update(re.findall(r"[a-zA-Z]{3,}", str(keyword).lower()))

    if not keyword_words:
        return 0.0

    overlap = len(query_words & keyword_words)
    return overlap / max(len(query_words), 1)


def _text_overlap_score(query: str, text: str) -> float:
    """Score a transcript message against the search query."""
    query_words = set(re.findall(r"[a-zA-Z]{3,}", query.lower()))
    text_words = set(re.findall(r"[a-zA-Z]{3,}", text.lower()))
    if not query_words or not text_words:
        return 0.0
    if query.lower() in text.lower():
        return 1.0
    overlap = len(query_words & text_words)
    return overlap / max(len(query_words), 1)


def _date_recency_bonus(date_str: str) -> float:
    """Give a small bonus to more recent transcripts."""
    try:
        date_value = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return 0.0

    age_days = (datetime.now(UTC) - date_value).days
    return max(0.0, 1.0 - (age_days / 365.0))


def _load_sidecar(meta_path: Path) -> dict | None:
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Failed to read transcript sidecar %s", meta_path.name, exc_info=True)
        return None


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
    """Search archived transcripts and return context-windowed snippets."""
    if not transcripts_dir.exists():
        return TranscriptSearchResults()

    cutoff = datetime.now(UTC) - timedelta(days=days_back)
    candidates: list[tuple[float, Path, dict]] = []

    for meta_path in transcripts_dir.glob("*.meta.json"):
        meta = _load_sidecar(meta_path)
        if meta is None:
            continue
        if meta.get("user_id") != user_id:
            continue

        date_start_str = str(meta.get("date_start", ""))
        try:
            date_start = datetime.fromisoformat(date_start_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        if date_start < cutoff:
            continue

        enc_path = meta_path.parent / meta_path.name.replace(".meta.json", ".jsonl.enc")
        if not enc_path.exists():
            enc_path = meta_path.parent / meta_path.name.replace(".meta.json", ".jsonl")
        if not enc_path.exists():
            continue

        keyword_score = _keyword_overlap_score(query, list(meta.get("keywords", [])))
        score = (keyword_score * 2.0) + _date_recency_bonus(date_start_str)
        candidates.append((score, enc_path, meta))

    candidates.sort(key=lambda item: item[0], reverse=True)
    if not candidates:
        return TranscriptSearchResults()

    snippets: list[TranscriptSnippet] = []
    chars_used = 0
    total_matches = 0

    for _score, enc_path, meta in candidates[:max_transcripts]:
        thread_id = int(meta.get("thread_id", 0))
        try:
            messages = decrypt_transcript(enc_path, dek=dek, thread_id=thread_id)
        except Exception:
            logger.warning("Failed to decrypt transcript %s", enc_path.name, exc_info=True)
            continue

        scored_hits: list[tuple[float, int]] = []
        for index, message in enumerate(messages):
            content = str(message.get("content", ""))
            if not query.strip():
                score = 0.5
            else:
                score = _text_overlap_score(query, content)
            if score > 0:
                scored_hits.append((score, index))

        scored_hits.sort(key=lambda item: item[0], reverse=True)
        seen_indices: set[int] = set()
        date_str = str(meta.get("date_start", "unknown"))[:10]

        for _hit_score, hit_idx in scored_hits:
            start = max(0, hit_idx - snippet_context)
            end = min(len(messages), hit_idx + snippet_context + 1)
            window_indices = set(range(start, end))
            if window_indices & seen_indices:
                continue
            seen_indices |= window_indices

            lines = [
                f"{str(messages[idx].get('role', 'unknown')).capitalize()}: "
                f"{str(messages[idx].get('content', ''))}"
                for idx in range(start, end)
            ]
            text = "\n".join(lines)
            total_matches += 1
            if len(snippets) >= max_snippets or chars_used + len(text) > budget_chars:
                continue

            snippets.append(
                TranscriptSnippet(
                    date=date_str,
                    thread_id=thread_id,
                    text=text,
                )
            )
            chars_used += len(text)

    return TranscriptSearchResults(snippets, total_matches=total_matches)


def format_snippets(snippets: list[TranscriptSnippet]) -> str:
    """Format transcript snippets for tool output."""
    if not snippets:
        return "No matching transcripts found."
    parts = [
        f"[{snippet.date}, thread {snippet.thread_id}]\n{snippet.text}"
        for snippet in snippets
    ]
    total_matches = max(getattr(snippets, "total_matches", len(snippets)), len(snippets))
    remaining = total_matches - len(snippets)
    if remaining > 0:
        parts.append(f"({remaining} more matches found, use a more specific query to narrow results)")
    return "\n\n".join(
        parts
    )
