"""Search archived transcripts via sidecar filtering and decryption."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from anima_server.services import anima_core_retrieval
from anima_server.services.agent import transcript_archive as transcript_archive_module

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
    return transcript_archive_module.load_transcript_sidecar(meta_path)


def _transcript_index_is_dirty(root: Path) -> bool:
    try:
        return anima_core_retrieval.is_retrieval_family_dirty(root=root, family="transcript")
    except Exception:
        logger.debug("Failed to inspect transcript index manifest state", exc_info=True)
        return False


def _candidate_transcripts_from_rust_index(
    *,
    query: str,
    user_id: int,
    transcripts_dir: Path,
    max_transcripts: int,
) -> list[tuple[Path, int, str]] | None:
    root = anima_core_retrieval.get_retrieval_root()
    try:
        hits = anima_core_retrieval.transcript_index_search(
            root=root,
            user_id=user_id,
            query=query,
            limit=max_transcripts,
        )
    except Exception:
        logger.debug(
            "Falling back to sidecar transcript search after Rust index failure",
            exc_info=True,
        )
        return None

    candidates: list[tuple[Path, int, str]] = []
    for hit in hits:
        transcript_ref = str(hit.get("transcript_ref", "")).strip()
        if not transcript_ref:
            continue
        enc_path = transcripts_dir / transcript_ref
        if not enc_path.exists():
            try:
                anima_core_retrieval.mark_retrieval_index_dirty(root=root, family="transcript")
            except Exception:
                logger.debug("Failed to mark transcript index dirty after missing artifact", exc_info=True)
            return None
        thread_id = int(hit.get("thread_id", 0))
        date_start = int(hit.get("date_start", 0) or 0)
        if date_start > 0:
            date_str = datetime.fromtimestamp(date_start, tz=UTC).date().isoformat()
        else:
            date_str = "unknown"
        candidates.append((enc_path, thread_id, date_str))
    return candidates


def _candidate_transcripts_from_sidecars(
    *,
    query: str,
    user_id: int,
    transcripts_dir: Path,
    days_back: int,
    max_transcripts: int,
) -> list[tuple[Path, int, str]]:
    cutoff = datetime.now(UTC) - timedelta(days=days_back)
    candidates: list[tuple[float, Path, int, str]] = []

    for meta_path in transcripts_dir.glob("*.meta.json"):
        meta = _load_sidecar(meta_path)
        if meta is None:
            continue
        if meta.get("user_id") != user_id:
            continue

        date_start_str = str(meta.get("date_start", ""))
        try:
            date_start = datetime.fromisoformat(
                date_start_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        if date_start < cutoff:
            continue

        enc_path = meta_path.parent / \
            meta_path.name.replace(".meta.json", ".jsonl.enc")
        if not enc_path.exists():
            enc_path = meta_path.parent / \
                meta_path.name.replace(".meta.json", ".jsonl")
        if not enc_path.exists():
            continue

        keyword_score = _keyword_overlap_score(
            query, list(meta.get("keywords", [])))
        score = (keyword_score * 2.0) + _date_recency_bonus(date_start_str)
        candidates.append((score, enc_path, int(meta.get("thread_id", 0)), date_start_str[:10]))

    candidates.sort(key=lambda item: item[0], reverse=True)
    return [
        (enc_path, thread_id, date_str)
        for _score, enc_path, thread_id, date_str in candidates[:max_transcripts]
    ]


def _snippets_from_candidates(
    *,
    query: str,
    dek: bytes | None,
    candidates: list[tuple[Path, int, str]],
    max_transcripts: int,
    max_snippets: int,
    snippet_context: int,
    budget_chars: int,
    mark_index_dirty_on_failure: bool,
    root: Path,
) -> tuple[list[TranscriptSnippet], int, bool]:
    snippets: list[TranscriptSnippet] = []
    chars_used = 0
    total_matches = 0
    had_candidate_failures = False

    for enc_path, thread_id, date_str in candidates[:max_transcripts]:
        try:
            messages = transcript_archive_module.decrypt_transcript(
                enc_path, dek=dek, thread_id=thread_id)
        except Exception:
            logger.warning("Failed to decrypt transcript %s",
                           enc_path.name, exc_info=True)
            if mark_index_dirty_on_failure:
                had_candidate_failures = True
                try:
                    anima_core_retrieval.mark_retrieval_index_dirty(root=root, family="transcript")
                except Exception:
                    logger.debug("Failed to mark transcript index dirty after decrypt failure", exc_info=True)
            continue

        scored_hits: list[tuple[float, int]] = []
        for index, message in enumerate(messages):
            content = str(message.get("content", ""))
            score = 0.5 if not query.strip() else _text_overlap_score(query, content)
            if score > 0:
                scored_hits.append((score, index))

        scored_hits.sort(key=lambda item: item[0], reverse=True)
        seen_indices: set[int] = set()

        for _hit_score, hit_idx in scored_hits:
            start = max(0, hit_idx - snippet_context)
            end = min(len(messages), hit_idx + snippet_context + 1)
            window_indices = set(range(start, end))
            if window_indices & seen_indices:
                continue
            seen_indices |= window_indices

            lines = [
                f"{str(messages[idx].get('role', 'unknown')).capitalize()}: "
                f"{messages[idx].get('content', '')!s}"
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

    return snippets, total_matches, had_candidate_failures


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

    candidates: list[tuple[Path, int, str]] | None
    used_rust_candidates = False
    root = anima_core_retrieval.get_retrieval_root()
    if _transcript_index_is_dirty(root):
        transcript_archive_module.rebuild_transcript_index(
            user_id=user_id,
            dek=dek,
            transcripts_dir=transcripts_dir,
            root=root,
        )

    candidates = None
    if not _transcript_index_is_dirty(root):
        rust_candidates = _candidate_transcripts_from_rust_index(
            query=query,
            user_id=user_id,
            transcripts_dir=transcripts_dir,
            max_transcripts=max_transcripts,
        )
        if rust_candidates:
            candidates = rust_candidates
            used_rust_candidates = True
    if not candidates:
        candidates = _candidate_transcripts_from_sidecars(
            query=query,
            user_id=user_id,
            transcripts_dir=transcripts_dir,
            days_back=days_back,
            max_transcripts=max_transcripts,
        )
    if not candidates:
        return TranscriptSearchResults()

    snippets, total_matches, had_rust_candidate_failures = _snippets_from_candidates(
        query=query,
        dek=dek,
        candidates=candidates,
        max_transcripts=max_transcripts,
        max_snippets=max_snippets,
        snippet_context=snippet_context,
        budget_chars=budget_chars,
        mark_index_dirty_on_failure=used_rust_candidates,
        root=root,
    )

    if used_rust_candidates and had_rust_candidate_failures and not snippets:
        sidecar_candidates = _candidate_transcripts_from_sidecars(
            query=query,
            user_id=user_id,
            transcripts_dir=transcripts_dir,
            days_back=days_back,
            max_transcripts=max_transcripts,
        )
        snippets, total_matches, _had_sidecar_failures = _snippets_from_candidates(
            query=query,
            dek=dek,
            candidates=sidecar_candidates,
            max_transcripts=max_transcripts,
            max_snippets=max_snippets,
            snippet_context=snippet_context,
            budget_chars=budget_chars,
            mark_index_dirty_on_failure=False,
            root=root,
        )

    return TranscriptSearchResults(snippets, total_matches=total_matches)


def format_snippets(snippets: list[TranscriptSnippet]) -> str:
    """Format transcript snippets for tool output."""
    if not snippets:
        return "No matching transcripts found."
    parts = [
        f"[{snippet.date}, thread {snippet.thread_id}]\n{snippet.text}"
        for snippet in snippets
    ]
    total_matches = max(getattr(snippets, "total_matches",
                        len(snippets)), len(snippets))
    remaining = total_matches - len(snippets)
    if remaining > 0:
        parts.append(
            f"({remaining} more matches found, use a more specific query to narrow results)")
    return "\n\n".join(
        parts
    )
