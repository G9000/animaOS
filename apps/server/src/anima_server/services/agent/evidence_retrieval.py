from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from anima_server.services.agent.embeddings import hybrid_search
from anima_server.services.agent.retrieval_intent import (
    RetrievalIntent,
    RetrievalMode,
    classify_retrieval_intent,
)
from anima_server.services.data_crypto import df

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_SESSION_DATE_RE = re.compile(
    r"Session date:\s*(?P<date>[0-9]{4}[/-][0-9]{2}[/-][0-9]{2}(?:\s*\([^)]+\))?(?:\s*[0-9]{2}:[0-9]{2}(?::[0-9]{2})?)?)",
    re.IGNORECASE,
)
_DATE_ONLY_RE = re.compile(r"(?P<y>[0-9]{4})[/-](?P<m>[0-9]{2})[/-](?P<d>[0-9]{2})")
_USER_LINE_RE = re.compile(r"^\s*User\s*:\s*(?P<body>.*)$", re.IGNORECASE)
_ASSISTANT_LINE_RE = re.compile(r"^\s*Assistant\s*:\s*(?P<body>.*)$", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]+")
_STOP_WORDS = frozenset(
    {
        "the",
        "and",
        "you",
        "your",
        "are",
        "for",
        "with",
        "have",
        "had",
        "did",
        "does",
        "not",
        "but",
        "any",
        "all",
        "what",
        "where",
        "when",
        "which",
        "who",
        "whom",
        "how",
        "why",
        "this",
        "that",
        "these",
        "those",
        "from",
        "into",
        "about",
        "been",
        "was",
        "were",
        "they",
        "them",
        "there",
        "here",
        "some",
        "many",
        "much",
        "more",
        "less",
        "than",
        "then",
        "also",
        "only",
        "very",
        "just",
        "still",
        "now",
        "after",
        "before",
        "since",
        "ago",
        "ever",
        "again",
        "could",
        "would",
        "should",
        "shall",
        "will",
        "may",
        "might",
        "can",
        "cannot",
        "doing",
        "done",
        "i",
        "me",
        "my",
        "we",
        "our",
        "us",
        "his",
        "her",
        "she",
        "him",
        "their",
        "its",
        "be",
        "is",
        "do",
        "or",
        "an",
        "as",
        "at",
        "by",
        "in",
        "of",
        "on",
        "to",
        "up",
        "if",
        "no",
        "so",
        "it",
    }
)
_PREFERENCE_BOOST_PHRASES = (
    "i prefer",
    "i enjoy",
    "i like",
    "i love",
    "i use",
    "i'm using",
    "i am using",
    "i work",
    "i'm working",
    "i am working",
    "working in",
    "i want to learn",
    "i'd like to learn",
    "advanced",
    "focus",
    "specializ",
)


def extract_session_date(text: str) -> str | None:
    """Return the raw session date string from a transcript chunk, if present."""

    if not text:
        return None
    match = _SESSION_DATE_RE.search(text)
    if not match:
        return None
    return match.group("date").strip()


def _parse_session_date_sortable(text: str) -> tuple[int, int, int]:
    """Parse the session date into a sortable (y, m, d) tuple. Returns (0,0,0) if missing."""

    if not text:
        return (0, 0, 0)
    match = _DATE_ONLY_RE.search(text)
    if not match:
        return (0, 0, 0)
    return (int(match.group("y")), int(match.group("m")), int(match.group("d")))


def extract_user_lines(text: str) -> list[str]:
    """Return a list of user-spoken line bodies from a transcript chunk."""

    if not text:
        return []
    lines: list[str] = []
    for line in text.splitlines():
        match = _USER_LINE_RE.match(line)
        if match:
            body = match.group("body").strip()
            if body:
                lines.append(body)
    return lines


def _extract_assistant_lines(text: str) -> list[str]:
    if not text:
        return []
    lines: list[str] = []
    for line in text.splitlines():
        match = _ASSISTANT_LINE_RE.match(line)
        if match:
            body = match.group("body").strip()
            if body:
                lines.append(body)
    return lines


def extract_question_terms(query: str) -> set[str]:
    """Lower-case content tokens from the query, with stopwords stripped."""

    if not query:
        return set()
    tokens: set[str] = set()
    for raw in _TOKEN_RE.findall(query.lower()):
        if len(raw) < 3:
            continue
        if raw in _STOP_WORDS:
            continue
        tokens.add(raw)
    return tokens


def _line_matches_terms(line: str, terms: set[str]) -> bool:
    if not terms:
        return False
    lowered = line.lower()
    return any(term in lowered for term in terms)


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    head = text[:max_chars]
    cut = max(head.rfind(". "), head.rfind("\n"))
    if cut > max_chars // 2:
        return head[: cut + 1].rstrip()
    return head.rstrip()


def compact_evidence_text(
    text: str,
    query_terms: set[str] | None = None,
    max_chars: int = 900,
) -> str:
    """Compact a verbose chunk into a short evidence snippet.

    Always preserves the session date prefix, prefers user lines that match query
    terms, and falls back to the first user line, then to assistant lines if no
    user lines exist.
    """

    if not text:
        return ""
    terms = query_terms or set()
    date = extract_session_date(text)
    user_lines = extract_user_lines(text)
    matching_user_lines = [line for line in user_lines if _line_matches_terms(line, terms)]

    if matching_user_lines:
        body_lines = matching_user_lines
    elif user_lines:
        body_lines = [user_lines[0]]
    else:
        assistant_lines = _extract_assistant_lines(text)
        matching_asst = [line for line in assistant_lines if _line_matches_terms(line, terms)]
        if matching_asst:
            body_lines = [matching_asst[0]]
        elif assistant_lines:
            body_lines = [assistant_lines[0]]
        else:
            stripped = text.strip()
            return _truncate(stripped, max_chars) if stripped else ""

    parts: list[str] = []
    if date:
        parts.append(f"Session date: {date}")
    parts.extend(f"User: {line}" for line in body_lines)
    out = "\n".join(parts)
    return _truncate(out, max_chars)


def _score_candidate(
    text: str,
    query_terms: set[str],
    intent: RetrievalIntent,
) -> tuple[float, tuple[int, int, int]]:
    """Score a candidate chunk under the given intent. Returns (score, sort_date)."""

    lowered = text.lower()
    base = sum(1 for term in query_terms if term in lowered)

    user_lines = extract_user_lines(text)
    user_blob = " ".join(user_lines).lower()
    user_match = sum(1 for term in query_terms if term in user_blob)
    score = float(base) + 1.5 * float(user_match)

    if user_lines:
        score += 1.0

    sort_date = _parse_session_date_sortable(text)

    if intent.mode is RetrievalMode.AGGREGATE:
        numbers = re.findall(r"\b\d+(?:[/.]\d+)?\b", text)
        score += 0.4 * len(numbers)
        if user_match:
            score += 1.0

    if intent.mode is RetrievalMode.LATEST_UPDATE and sort_date != (0, 0, 0):
        score += (sort_date[0] - 2000) * 0.05
        score += sort_date[1] * 0.02
        score += sort_date[2] * 0.005

    if intent.mode is RetrievalMode.TEMPORAL and _DATE_ONLY_RE.search(text):
        score += 1.0

    if intent.mode is RetrievalMode.PREFERENCE:
        for phrase in _PREFERENCE_BOOST_PHRASES:
            if phrase in user_blob:
                score += 1.5

    return score, sort_date


def rerank_evidence_texts(
    texts: Sequence[str],
    query: str,
    intent: RetrievalIntent,
) -> list[str]:
    """Rerank a candidate pool for the given retrieval intent.

    Returns up to ``intent.max_evidence`` items, ordered for prompt injection.
    For aggregation intents, prefers distinct session dates so multiple events
    are preserved across the candidate pool. For latest-update intents, prefers
    newer session dates among same-entity evidence.
    """

    if not texts:
        return []

    query_terms = extract_question_terms(query)
    scored: list[tuple[float, tuple[int, int, int], int, str]] = []
    for idx, text in enumerate(texts):
        if not text:
            continue
        score, sort_date = _score_candidate(text, query_terms, intent)
        scored.append((score, sort_date, idx, text))

    if not scored:
        return []

    if intent.mode is RetrievalMode.AGGREGATE:
        scored.sort(key=lambda row: (-row[0], row[2]))
        kept: list[tuple[float, tuple[int, int, int], int, str]] = []
        seen_dates: set[tuple[int, int, int]] = set()
        leftovers: list[tuple[float, tuple[int, int, int], int, str]] = []
        for row in scored:
            sort_date = row[1]
            if sort_date == (0, 0, 0) or sort_date not in seen_dates:
                kept.append(row)
                seen_dates.add(sort_date)
            else:
                leftovers.append(row)
            if len(kept) >= intent.max_evidence:
                break
        if len(kept) < intent.max_evidence:
            for row in leftovers:
                kept.append(row)
                if len(kept) >= intent.max_evidence:
                    break
        return [row[3] for row in kept]

    if intent.mode is RetrievalMode.LATEST_UPDATE:
        scored.sort(key=lambda row: (-row[1][0], -row[1][1], -row[1][2], -row[0], row[2]))
        return [row[3] for row in scored[: intent.max_evidence]]

    scored.sort(key=lambda row: (-row[0], row[2]))
    return [row[3] for row in scored[: intent.max_evidence]]


@dataclass(slots=True)
class WideEvidenceResult:
    intent: RetrievalIntent
    semantic_results: list[tuple[int, str, float]] = field(default_factory=list)
    query_embedding: list[float] | None = None
    total_considered: int = 0


async def retrieve_wide_evidence(
    *,
    db: Session | Any,
    user_id: int,
    query: str,
    runtime_db: Session | None = None,
    similarity_threshold: float = 0.1,
) -> WideEvidenceResult:
    """Wide candidate retrieval for cross-session memory questions.

    Classifies the query into a retrieval intent. For DIRECT intents, returns
    an empty result so callers can fall back to normal adaptive retrieval.
    For wide-evidence intents, calls ``hybrid_search`` with a larger candidate
    limit, decrypts content, then compacts and reranks the candidate pool by
    intent-specific evidence rules.
    """

    intent = classify_retrieval_intent(query)
    if not intent.needs_wide_evidence:
        return WideEvidenceResult(intent=intent)

    search_result = await hybrid_search(
        db,
        user_id=user_id,
        query=query,
        limit=intent.candidate_limit,
        similarity_threshold=similarity_threshold,
        runtime_db=runtime_db,
    )

    items = list(getattr(search_result, "items", []) or [])
    query_embedding = getattr(search_result, "query_embedding", None)

    if not items:
        return WideEvidenceResult(
            intent=intent,
            query_embedding=query_embedding,
            total_considered=0,
        )

    query_terms = extract_question_terms(query)
    decoded_pairs: list[tuple[int, str, float]] = []
    compacted_lookup: dict[str, tuple[int, float]] = {}
    candidate_texts: list[str] = []
    for item, score in items:
        raw = df(user_id, item.content, table="memory_items", field="content")
        compacted = compact_evidence_text(raw, query_terms=query_terms)
        if not compacted:
            continue
        decoded_pairs.append((int(item.id), compacted, float(score)))
        candidate_texts.append(compacted)
        # Last-write-wins is fine; identical compacted text means duplicate evidence.
        compacted_lookup[compacted] = (int(item.id), float(score))

    if not candidate_texts:
        return WideEvidenceResult(
            intent=intent,
            query_embedding=query_embedding,
            total_considered=len(items),
        )

    ranked_texts = rerank_evidence_texts(candidate_texts, query=query, intent=intent)

    semantic_results: list[tuple[int, str, float]] = []
    for text in ranked_texts:
        meta = compacted_lookup.get(text)
        if meta is None:
            continue
        item_id, score = meta
        semantic_results.append((item_id, text, score))

    return WideEvidenceResult(
        intent=intent,
        semantic_results=semantic_results,
        query_embedding=query_embedding,
        total_considered=len(items),
    )
