"""Feedback signal collection: detect re-asks, corrections, and abandonment.

Analyzes conversation patterns to identify when the agent's response wasn't
satisfactory. These signals feed into procedural rule generation, helping the
agent learn from its mistakes.

Signal types:
- re_ask: user asks the same question again (agent didn't answer well)
- correction: user explicitly corrects the agent's output
- abandonment: user drops a topic without resolution

When a correction is detected, ``apply_memory_correction`` searches recent
memories for content that contradicts the corrected fact and supersedes the
wrong memory with the corrected one (keyword-based, no LLM call).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models import MemoryItem

logger = logging.getLogger(__name__)

_CORRECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:no|nope),?\s+(?:I (?:said|meant|want)|that'?s (?:not|wrong))", re.IGNORECASE),
    re.compile(
        r"(?:actually|wait),?\s+(?:I (?:said|meant|want)|that'?s (?:not|wrong))", re.IGNORECASE
    ),
    re.compile(r"that'?s not what I (?:asked|meant|said|wanted)", re.IGNORECASE),
    re.compile(r"you (?:misunderstood|got it wrong|didn'?t understand)", re.IGNORECASE),
    re.compile(r"I (?:already|just) (?:told|said|asked)", re.IGNORECASE),
    re.compile(r"let me (?:rephrase|clarify|re-?explain)", re.IGNORECASE),
)

_REASK_SIMILARITY_THRESHOLD = 0.6


@dataclass(frozen=True, slots=True)
class FeedbackSignal:
    signal_type: str  # re_ask, correction, abandonment
    evidence: str
    topic: str
    severity: float  # 0.0 to 1.0


def detect_correction(user_message: str) -> FeedbackSignal | None:
    """Check if a user message contains a correction of the agent."""
    for pattern in _CORRECTION_PATTERNS:
        match = pattern.search(user_message)
        if match:
            return FeedbackSignal(
                signal_type="correction",
                evidence=user_message[:200],
                topic=match.group(0),
                severity=0.7,
            )
    return None


def detect_reask(
    user_message: str,
    recent_user_messages: list[str],
) -> FeedbackSignal | None:
    """Check if user is re-asking something they already asked."""
    if not recent_user_messages:
        return None

    user_words = set(user_message.lower().split())
    if len(user_words) < 3:
        return None

    for prev in recent_user_messages:
        prev_words = set(prev.lower().split())
        if len(prev_words) < 3:
            continue
        overlap = len(user_words & prev_words)
        total = len(user_words | prev_words)
        if total == 0:
            continue
        similarity = overlap / total
        if similarity >= _REASK_SIMILARITY_THRESHOLD:
            return FeedbackSignal(
                signal_type="re_ask",
                evidence=f"Similar to previous: '{prev[:100]}'",
                topic=user_message[:100],
                severity=0.6,
            )
    return None


def collect_feedback_signals(
    user_id: int,
    user_message: str,
    thread_id: int | None = None,
    runtime_db: Session | None = None,
) -> list[FeedbackSignal]:
    """Collect all feedback signals from a user message in context."""
    from anima_server.models.runtime import RuntimeMessage, RuntimeThread

    signals: list[FeedbackSignal] = []

    # Check for correction
    correction = detect_correction(user_message)
    if correction is not None:
        signals.append(correction)

    # Check for re-ask by comparing to recent user messages (runtime PG)
    own_session: Session | None = None
    rt = runtime_db
    if rt is None:
        from anima_server.db.runtime import get_runtime_session_factory

        try:
            own_session = get_runtime_session_factory()()
            rt = own_session
        except RuntimeError:
            pass  # runtime engine not initialized

    try:
        if thread_id is None and rt is not None:
            thread = rt.scalar(
                select(RuntimeThread).where(
                    RuntimeThread.user_id == user_id,
                    RuntimeThread.status == "active",
                )
            )
            if thread is not None:
                thread_id = thread.id

        if thread_id is not None and rt is not None:
            recent = rt.scalars(
                select(RuntimeMessage.content_text)
                .where(
                    RuntimeMessage.thread_id == thread_id,
                    RuntimeMessage.role == "user",
                    RuntimeMessage.is_in_context.is_(True),
                )
                .order_by(RuntimeMessage.sequence_id.desc())
                .limit(5)
            ).all()

            recent_texts = [t for t in recent if t and t != user_message]
            reask = detect_reask(user_message, recent_texts)
            if reask is not None:
                signals.append(reask)
    finally:
        if own_session is not None:
            own_session.close()

    return signals


def record_feedback_signals(
    db: Session,
    *,
    user_id: int,
    signals: list[FeedbackSignal],
    runtime_db: Session | None = None,
) -> int:
    """Record feedback signals into the growth log for procedural rule generation.

    When *runtime_db* is provided, growth-log entries are written as
    ``PendingMemoryOp`` rows in PG (Soul Writer consolidates them later).
    Falls back to the legacy direct ``append_growth_log_entry`` path when
    *runtime_db* is ``None``.
    """
    if not signals:
        return 0

    from anima_server.services.agent.self_model import (
        append_growth_log_entry,
        ensure_self_model_exists,
    )

    ensure_self_model_exists(db, user_id=user_id)
    recorded = 0

    for signal in signals:
        entry = f"Feedback signal ({signal.signal_type}): {signal.evidence[:150]}"
        if runtime_db is not None:
            from anima_server.services.agent.pending_ops import create_pending_op

            create_pending_op(
                runtime_db,
                user_id=user_id,
                op_type="append",
                target_block="growth_log",
                content=entry,
                old_content=None,
                source_run_id=None,
                source_tool_call_id=None,
            )
        else:
            append_growth_log_entry(db, user_id=user_id, entry=entry)
        recorded += 1

    return recorded


# ---------------------------------------------------------------------------
# Correction-extraction patterns
# ---------------------------------------------------------------------------
# These capture the *corrected* (right) value from user messages.  They are
# ordered from most specific to least specific.  Each pattern should produce
# named groups ``wrong`` (optional) and ``right`` (required).

_CORRECTION_EXTRACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # "actually, my X is Y" / "actually X is Y"
    (
        re.compile(
            r"(?:actually|wait|no),?\s+(?:my\s+)?(?P<topic>[a-z][a-z ]{0,30}?)\s+is\s+(?P<right>[^.?!\n]+)",
            re.IGNORECASE,
        ),
        "topic_is",
    ),
    # "it's Y, not X" / "it's Y not X"
    (
        re.compile(
            r"it(?:'s| is)\s+(?P<right>[^,]+?),?\s+not\s+(?P<wrong>[^.?!\n]+)",
            re.IGNORECASE,
        ),
        "right_not_wrong",
    ),
    # "not X, it's Y" / "not X, but Y"
    (
        re.compile(
            r"not\s+(?P<wrong>[^,]+?),?\s+(?:it(?:'s| is)|but)\s+(?P<right>[^.?!\n]+)",
            re.IGNORECASE,
        ),
        "not_wrong_but_right",
    ),
    # "no, that's wrong, it's Y" / "no, that's wrong. it's Y"
    (
        re.compile(
            r"(?:no|nope),?\s+(?:that'?s (?:wrong|not right|incorrect))[.,]?\s*(?:it(?:'s| is)\s+)?(?P<right>[^.?!\n]+)",
            re.IGNORECASE,
        ),
        "wrong_its_right",
    ),
    # "I said X not Y" / "I meant X"
    (
        re.compile(
            r"I (?:said|meant|want)\s+(?P<right>[^,]+?)(?:,?\s+not\s+(?P<wrong>[^.?!\n]+))?$",
            re.IGNORECASE,
        ),
        "i_said",
    ),
    # "my name is X" (standalone correction after a correction signal)
    (
        re.compile(
            r"(?:my\s+)?(?P<topic>name|age|birthday|job|occupation|city|location)\s+is\s+(?P<right>[^.?!\n]+)",
            re.IGNORECASE,
        ),
        "topic_is_simple",
    ),
)

# Map topic words to memory categories + search keywords
_TOPIC_CATEGORY_MAP: dict[str, tuple[str, str]] = {
    "name": ("fact", "name"),
    "age": ("fact", "age"),
    "birthday": ("fact", "birthday"),
    "job": ("fact", "work"),
    "occupation": ("fact", "work"),
    "city": ("fact", "live"),
    "location": ("fact", "live"),
    "color": ("preference", "color"),
    "favourite": ("preference", ""),
    "favorite": ("preference", ""),
}


@dataclass(frozen=True, slots=True)
class CorrectionFact:
    """A structured correction extracted from a user message."""

    right: str  # the corrected (new) value
    wrong: str | None  # the old incorrect value (may be None)
    topic: str | None  # optional topic keyword (e.g. "name", "age")
    pattern_kind: str  # which pattern matched


def extract_correction_facts(user_message: str) -> list[CorrectionFact]:
    """Extract structured correction facts from a user's correction message.

    Returns a list of CorrectionFact with the corrected value (``right``),
    the wrong value if identifiable (``wrong``), and a topic hint.
    No LLM calls — purely regex-based.
    """
    results: list[CorrectionFact] = []
    seen: set[str] = set()

    for pattern, kind in _CORRECTION_EXTRACT_PATTERNS:
        for match in pattern.finditer(user_message):
            right = match.group("right").strip(" \t\r\n\"'`.,;:!?")
            if not right or len(right) < 2:
                continue

            wrong: str | None = None
            try:
                wrong_raw = match.group("wrong")
                if wrong_raw:
                    wrong = wrong_raw.strip(" \t\r\n\"'`.,;:!?")
            except IndexError:
                pass

            topic: str | None = None
            try:
                topic_raw = match.group("topic")
                if topic_raw:
                    topic = topic_raw.strip().lower()
            except IndexError:
                pass

            key = right.lower()
            if key in seen:
                continue
            seen.add(key)

            results.append(
                CorrectionFact(
                    right=right,
                    wrong=wrong,
                    topic=topic,
                    pattern_kind=kind,
                )
            )

    return results


# ---------------------------------------------------------------------------
# Apply memory correction
# ---------------------------------------------------------------------------

# Words that are too generic to match against memory content
_CORRECTION_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "i",
        "me",
        "my",
        "am",
        "is",
        "are",
        "was",
        "were",
        "as",
        "at",
        "in",
        "to",
        "for",
        "of",
        "on",
        "with",
        "and",
        "it",
        "that",
        "this",
        "not",
        "no",
        "but",
        "or",
        "so",
        "if",
        "its",
        "actually",
        "really",
        "just",
        "now",
        "like",
    }
)

_WORD_RE = re.compile(r"[a-z0-9']+")


def _correction_tokens(text: str) -> set[str]:
    """Tokenize text into lowercase words, stripping stopwords."""
    return {
        w for w in _WORD_RE.findall(text.lower()) if w not in _CORRECTION_STOPWORDS and len(w) > 1
    }


def apply_memory_correction(
    db: Session,
    *,
    user_id: int,
    user_message: str,
    thread_id: int | None = None,
    runtime_db: Session | None = None,
) -> list[dict[str, Any]]:
    """Search recent memories for content contradicted by the user's correction and fix them.

    Returns a list of dicts describing each correction applied::

        [{"old_content": ..., "new_content": ..., "memory_id": ...}]

    The function:
    1. Extracts structured correction facts (right/wrong/topic) from the message.
    2. For each fact, searches active memories for a match:
       - If ``wrong`` is known, look for memories containing those keywords.
       - If ``topic`` is known, narrow search to matching category/keywords.
       - Falls back to searching the most recent N memories for keyword overlap.
    3. Supersedes the matched memory with a corrected version.

    No LLM calls are made — purely keyword-based matching.
    """
    from anima_server.services.agent.memory_store import (
        get_memory_items,
        supersede_memory_item,
    )
    from anima_server.services.data_crypto import df

    corrections = extract_correction_facts(user_message)
    if not corrections:
        return []

    applied: list[dict[str, Any]] = []

    for correction in corrections:
        # Build search keywords from the wrong value, topic, or right value
        search_keywords: set[str] = set()
        if correction.wrong:
            search_keywords |= _correction_tokens(correction.wrong)
        if correction.topic:
            search_keywords |= _correction_tokens(correction.topic)
            # Add mapped keywords (e.g. "name" -> search for "name")
            mapped = _TOPIC_CATEGORY_MAP.get(correction.topic)
            if mapped and mapped[1]:
                search_keywords.add(mapped[1])

        # Determine which category to search
        target_category: str | None = None
        if correction.topic:
            mapped = _TOPIC_CATEGORY_MAP.get(correction.topic)
            if mapped:
                target_category = mapped[0]

        # Get recent active memories to search through
        candidates = get_memory_items(
            db,
            user_id=user_id,
            category=target_category,
            limit=50,
            active_only=True,
        )

        if not candidates:
            continue

        best_match: MemoryItem | None = None
        best_score: float = 0.0

        for item in candidates:
            plaintext = df(
                user_id,
                item.content,
                table="memory_items",
                field="content",
            )
            item_tokens = _correction_tokens(plaintext)

            if not item_tokens:
                continue

            # Strategy 1: if we know the wrong value, look for it
            if search_keywords:
                overlap = search_keywords & item_tokens
                if not overlap:
                    continue
                score = len(overlap) / len(search_keywords)
            else:
                # Strategy 2: no wrong value known — use the right value tokens
                # to find memories about the same topic and pick the most recent
                right_tokens = _correction_tokens(correction.right)
                if not right_tokens:
                    continue
                # For "no wrong value" corrections, we need the memory to be
                # about a similar topic. Use partial token overlap.
                overlap = right_tokens & item_tokens
                if not overlap:
                    continue
                score = len(overlap) / max(len(right_tokens), len(item_tokens))

            # Don't match against a memory that already contains the correct value
            right_tokens_check = _correction_tokens(correction.right)
            if right_tokens_check and right_tokens_check.issubset(item_tokens):
                continue

            if score > best_score:
                best_score = score
                best_match = item

        if best_match is None or best_score < 0.3:
            continue

        # Build the corrected content
        old_plaintext = df(
            user_id,
            best_match.content,
            table="memory_items",
            field="content",
        )

        # If we know both wrong and right, do a targeted replacement in the
        # existing memory text.  Otherwise replace the whole content with a
        # topic-appropriate version derived from the correction.
        if correction.wrong and correction.wrong.lower() in old_plaintext.lower():
            # Case-insensitive replacement of wrong->right within existing text
            escaped_wrong = re.escape(correction.wrong)
            new_content = re.sub(
                escaped_wrong,
                correction.right,
                old_plaintext,
                count=1,
                flags=re.IGNORECASE,
            )
        elif correction.topic:
            # Build a fact-style replacement: "Topic: right"
            # Preserve the existing memory's format if it uses "Topic: value"
            colon_match = re.match(
                r"^([A-Za-z ]+):\s*",
                old_plaintext,
            )
            if colon_match:
                prefix = colon_match.group(1)
                new_content = f"{prefix}: {correction.right}"
            else:
                new_content = f"{correction.topic.title()}: {correction.right}"
        else:
            # Last resort: just use the right value as the new content
            new_content = correction.right

        new_content = new_content.strip()
        if not new_content or new_content.lower() == old_plaintext.lower():
            continue

        try:
            if runtime_db is not None:
                from anima_server.services.agent.candidate_ops import create_memory_candidate

                create_memory_candidate(
                    runtime_db,
                    user_id=user_id,
                    content=new_content,
                    category=best_match.category,
                    importance=best_match.importance or 3,
                    importance_source="correction",
                    source="feedback",
                    supersedes_item_id=best_match.id,
                )
            else:
                supersede_memory_item(
                    db,
                    old_item_id=best_match.id,
                    new_content=new_content,
                )
            applied.append(
                {
                    "old_content": old_plaintext,
                    "new_content": new_content,
                    "memory_id": best_match.id,
                }
            )
            logger.info(
                "Memory correction applied for user %s: '%s' -> '%s'",
                user_id,
                old_plaintext,
                new_content,
            )
        except Exception:
            logger.warning(
                "Failed to supersede memory %d during correction",
                best_match.id,
            )

    return applied
