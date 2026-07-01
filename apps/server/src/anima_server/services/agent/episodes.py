from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from anima_server.config import settings  # noqa: F401 — tests patch this attribute
from anima_server.models import AgentProfile, MemoryEpisode, User
from anima_server.models.runtime import RuntimeMessage
from anima_server.services.data_crypto import df, ef

logger = logging.getLogger(__name__)

EPISODE_MIN_TURNS = 3
EPISODE_SEQUENTIAL_MAX_TURNS = 6
_CONCRETE_DETAIL_EXCERPT_MAX_CHARS = 180
_RELATIVE_DAY_OFFSETS = {
    "today": 0,
    "tonight": 0,
    "tomorrow": 1,
    "yesterday": -1,
}
_RELATIVE_DAY_RE = re.compile(r"\b(today|tonight|tomorrow|yesterday)\b", re.IGNORECASE)
_RELATIVE_WEEK_RE = re.compile(r"\b(last|this|next)\s+week\b", re.IGNORECASE)


def _clean_participant_name(value: str | None, *, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    cleaned = " ".join(value.strip().split())
    return cleaned or fallback


def _resolve_episode_participants(db: Session, *, user_id: int) -> tuple[str, str]:
    user = db.get(User, user_id)
    user_name = _clean_participant_name(
        user.display_name if user is not None else None,
        fallback="the person I'm talking with",
    )
    profile = db.scalar(select(AgentProfile).where(AgentProfile.user_id == user_id))
    agent_name = _clean_participant_name(
        profile.agent_name if profile is not None else None,
        fallback="Anima",
    )
    return user_name, agent_name


async def maybe_generate_episode(
    *,
    user_id: int,
    thread_id: int | None = None,
    db_factory: Callable[..., object] | None = None,
    runtime_db_factory: Callable[..., object] | None = None,
) -> MemoryEpisode | None:
    """Check if there are enough un-episoded turns today and generate an episode if so."""
    from anima_server.db.session import get_user_session_factory

    factory = db_factory or get_user_session_factory(user_id)

    # ── Resolve runtime session factory ──────────────────────
    if runtime_db_factory is None:
        from anima_server.db.runtime import get_runtime_session_factory

        try:
            runtime_db_factory = get_runtime_session_factory()
        except RuntimeError:
            return None

    # ── Phase 1: Read — gather messages then release sessions ────
    today = datetime.now(UTC).date().isoformat()

    with factory() as db:
        consumed_turns = (
            db.scalar(
                select(func.coalesce(func.sum(MemoryEpisode.turn_count), 0)).where(
                    MemoryEpisode.user_id == user_id,
                    MemoryEpisode.date == today,
                )
            )
            or 0
        )
        user_name, agent_name = _resolve_episode_participants(db, user_id=user_id)

    # Fetch user/assistant messages from the last 24 hours via RuntimeMessage
    with runtime_db_factory() as rt_db:
        messages_raw = list(
            rt_db.scalars(
                select(RuntimeMessage)
                .where(
                    RuntimeMessage.user_id == user_id,
                    RuntimeMessage.role.in_(("user", "assistant")),
                    RuntimeMessage.created_at >= datetime.now(UTC) - timedelta(hours=24),
                )
                .order_by(RuntimeMessage.created_at)
            ).all()
        )

    # Pair consecutive user/assistant messages into tuples
    pairs: list[tuple[str, str]] = []
    pair_started_at: list[datetime | None] = []
    i = 0
    while i < len(messages_raw) - 1:
        if messages_raw[i].role == "user" and messages_raw[i + 1].role == "assistant":
            pairs.append((messages_raw[i].content_text or "", messages_raw[i + 1].content_text or ""))
            pair_started_at.append(messages_raw[i].created_at)
            i += 2
        else:
            i += 1

    # Calculate how many new pairs we have since last episode
    available_pairs = pairs[consumed_turns:] if consumed_turns < len(pairs) else []
    available_pair_times = (
        pair_started_at[consumed_turns:] if consumed_turns < len(pair_started_at) else []
    )

    if len(available_pairs) < EPISODE_MIN_TURNS:
        return None

    # ── Phase 2: LLM call — no session held open ────────────
    from anima_server.services.agent import batch_segmenter as _bs

    if _bs.should_batch_segment(len(available_pairs)):
        # Batch path: LLM groups messages into topic-coherent segments.
        try:
            groups_1based = await _bs.segment_messages_batch(
                available_pairs,
                user_id=user_id,
                user_name=user_name,
                agent_name=agent_name,
            )
            segments_0based = _bs.indices_to_0based(groups_1based)
        except Exception:
            segments_0based = []

        if segments_0based:
            with factory() as db:
                episodes = await _bs.generate_episodes_from_segments(
                    db,
                    user_id=user_id,
                    thread_id=thread_id,
                    pairs=available_pairs,
                    pair_started_at=available_pair_times,
                    segments=segments_0based,
                    today=today,
                    user_name=user_name,
                    agent_name=agent_name,
                )
                db.commit()
                return episodes[-1] if episodes else None

    # Sequential path (below BATCH_THRESHOLD or batch segmentation failed).
    sequential_pairs = available_pairs[:EPISODE_SEQUENTIAL_MAX_TURNS]
    sequential_pair_times = available_pair_times[:EPISODE_SEQUENTIAL_MAX_TURNS]
    conversation_started_at = _first_pair_timestamp(sequential_pair_times)
    parsed = await _call_llm_for_episode_safe(
        sequential_pairs,
        user_id=user_id,
        user_name=user_name,
        agent_name=agent_name,
        conversation_started_at=conversation_started_at,
    )

    # ── Phase 3: Write — short-lived session for DB updates ──
    with factory() as db:
        episode = _build_episode_from_parsed(
            db,
            parsed=parsed,
            user_id=user_id,
            thread_id=thread_id,
            pairs=sequential_pairs,
            today=today,
            conversation_started_at=conversation_started_at,
            pair_started_at=sequential_pair_times,
        )
        db.commit()
        return episode


def _create_fallback_episode(
    db: Session,
    *,
    user_id: int,
    thread_id: int | None,
    pairs: list[tuple[str, str]],
    today: str,
    conversation_started_at: datetime | None = None,
) -> MemoryEpisode:
    """Create a basic episode without LLM when generation fails."""
    user_msgs = [pair[0] for pair in pairs if pair[0]]
    preview = user_msgs[0][:80] if user_msgs else "Conversation"

    episode = MemoryEpisode(
        user_id=user_id,
        thread_id=thread_id,
        date=today,
        summary=ef(user_id, f"Session: {preview}...", table="memory_episodes", field="summary"),
        topics_json=None,
        emotional_arc=None,
        significance_score=2,
        turn_count=len(pairs),
    )
    db.add(episode)
    db.flush()
    return episode


def _merge_episodes(
    db: Session,
    *,
    new_episode: MemoryEpisode,
    user_id: int,
) -> MemoryEpisode:
    """Try to merge *new_episode* into a recent episode with overlapping topics.

    If the new episode's topics overlap significantly with a recent episode
    from the same day, merge them and return the merged episode.
    Otherwise return the new episode unchanged.
    """
    from sqlalchemy import select

    # Get recent episodes from the same day, excluding the new episode itself
    query = (
        select(MemoryEpisode)
        .where(
            MemoryEpisode.user_id == user_id,
            MemoryEpisode.date == new_episode.date,
        )
        .order_by(MemoryEpisode.created_at.desc())
        .limit(3)
    )
    if new_episode.id is not None:
        query = query.where(MemoryEpisode.id != new_episode.id)
    recent = list(db.scalars(query).all())

    if not recent:
        return new_episode

    new_topics = set(new_episode.topics_json or [])
    if not new_topics:
        return new_episode

    for prev in recent:
        prev_topics = set(prev.topics_json or [])
        if not prev_topics:
            continue

        # Check for topic overlap
        overlap = new_topics & prev_topics
        if len(overlap) >= min(2, len(new_topics), len(prev_topics)):
            # Merge: update previous episode
            prev_summary = df(user_id, prev.summary, table="memory_episodes", field="summary")
            new_summary = df(user_id, new_episode.summary, table="memory_episodes", field="summary")

            merged_summary = f"{prev_summary} Later: {new_summary}"
            prev.summary = ef(
                user_id,
                merged_summary,
                table="memory_episodes",
                field="summary",
            )

            # Merge topics
            merged_topics = list(prev_topics | new_topics)[:5]
            prev.topics_json = merged_topics

            # Update significance to max of both
            prev.significance_score = max(
                prev.significance_score or 2,
                new_episode.significance_score or 2,
            )

            # Update turn count
            prev.turn_count = (prev.turn_count or 0) + (new_episode.turn_count or 0)

            # Delete the new episode since we merged into previous
            db.delete(new_episode)
            db.flush()
            return prev

    return new_episode


def _build_episode_from_parsed(
    db: Session,
    *,
    parsed: dict[str, Any] | None,
    user_id: int,
    thread_id: int | None,
    pairs: list[tuple[str, str]],
    today: str,
    conversation_started_at: datetime | None = None,
    pair_started_at: list[datetime | None] | None = None,
) -> MemoryEpisode:
    """Create a MemoryEpisode from pre-parsed LLM output (or fallback)."""
    if parsed is None:
        return _create_fallback_episode(
            db,
            user_id=user_id,
            thread_id=thread_id,
            pairs=pairs,
            today=today,
        )

    summary = parsed.get("summary", "")
    if not isinstance(summary, str) or not summary.strip():
        return _create_fallback_episode(
            db,
            user_id=user_id,
            thread_id=thread_id,
            pairs=pairs,
            today=today,
        )
    summary = summary.strip()
    topics = parsed.get("topics", [])
    if not isinstance(topics, list):
        topics = []
    topics = [str(t) for t in topics if isinstance(t, str) and t.strip()][:5]
    emotional_arc = parsed.get("emotional_arc")
    if not isinstance(emotional_arc, str):
        emotional_arc = None
    significance = parsed.get("significance", 3)
    try:
        significance = int(significance)
        if not 1 <= significance <= 5:
            significance = 3
    except (ValueError, TypeError):
        significance = 3

    summary = _ensure_summary_preserves_concrete_details(
        summary,
        salient_details=_ground_salient_user_details(
            parsed.get("salient_user_details"),
            pairs,
        ),
    )
    summary = _ensure_relative_dates_have_absolute_dates(
        summary,
        pairs=pairs,
        pair_started_at=pair_started_at or [],
        conversation_started_at=conversation_started_at,
    )

    episode = MemoryEpisode(
        user_id=user_id,
        thread_id=thread_id,
        date=today,
        summary=ef(user_id, summary, table="memory_episodes", field="summary"),
        topics_json=topics if topics else None,
        emotional_arc=ef(user_id, emotional_arc, table="memory_episodes", field="emotional_arc"),
        significance_score=significance,
        turn_count=len(pairs),
    )
    db.add(episode)
    db.flush()

    # Attempt merge with recent episode
    return _merge_episodes(db, new_episode=episode, user_id=user_id)


async def _call_llm_for_episode(
    pairs: list[tuple[str, str]],
    *,
    user_id: int = 0,
    user_name: str = "the user",
    agent_name: str = "Anima",
    conversation_started_at: datetime | None = None,
) -> dict[str, Any]:
    from anima_server.services.agent.llm_json import call_llm_for_json
    from anima_server.services.agent.prompt_loader import PromptLoader

    # Create a prompt loader with the agent name (no db access needed)
    prompt_loader = PromptLoader(agent_name=agent_name)

    turns_text = "\n".join(
        f"{user_name}: {user_msg}\n{agent_name}: {assistant_msg}"
        for user_msg, assistant_msg in pairs
    )

    # Use templated prompt
    prompt = prompt_loader.episode_generation(
        turns=turns_text,
        user_name=user_name,
        conversation_timestamp=_format_conversation_timestamp(conversation_started_at),
    )

    parsed = await call_llm_for_json(
        "You generate episode summaries. Respond only with JSON.",
        prompt,
    )
    return parsed if isinstance(parsed, dict) else {}


async def _call_llm_for_episode_safe(
    pairs: list[tuple[str, str]],
    *,
    user_id: int = 0,
    user_name: str = "the user",
    agent_name: str = "Anima",
    conversation_started_at: datetime | None = None,
) -> dict[str, Any] | None:
    """Call LLM for episode generation, returning None on failure."""
    try:
        return await _call_llm_for_episode(
            pairs,
            user_id=user_id,
            user_name=user_name,
            agent_name=agent_name,
            conversation_started_at=conversation_started_at,
        )
    except Exception:
        logger.exception("LLM episode generation failed, using fallback")
        return None


def _first_pair_timestamp(values: list[datetime | None]) -> datetime | None:
    for value in values:
        if value is not None:
            if value.tzinfo is None:
                return value.replace(tzinfo=UTC)
            return value
    return None


def _format_conversation_timestamp(value: datetime | None) -> str:
    if value is None:
        return "unknown"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _clean_detail_excerpt(value: str, *, max_chars: int) -> str | None:
    cleaned = " ".join(value.strip().split())
    if not cleaned:
        return None
    if len(cleaned) <= max_chars:
        return cleaned
    return f"{cleaned[:max_chars].rstrip()}..."


def _ground_salient_user_details(
    value: object,
    pairs: list[tuple[str, str]],
    *,
    limit: int = 8,
    max_chars: int = _CONCRETE_DETAIL_EXCERPT_MAX_CHARS,
) -> list[str] | None:
    if not isinstance(value, list):
        return None

    user_messages = [
        " ".join(user_message.strip().split())
        for user_message, _assistant_message in pairs
        if user_message.strip()
    ]
    folded_messages = [message.casefold() for message in user_messages]
    details: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        cleaned_detail = " ".join(item.strip().split())
        if not cleaned_detail:
            continue
        folded_detail = cleaned_detail.casefold()
        if folded_detail in seen:
            continue
        if not any(folded_detail in message for message in folded_messages):
            continue
        detail = _clean_detail_excerpt(cleaned_detail, max_chars=max_chars)
        if detail is None:
            continue
        seen.add(folded_detail)
        details.append(detail)
        if len(details) >= limit:
            return details
    return details


def _ensure_summary_preserves_concrete_details(
    summary: str,
    *,
    salient_details: list[str] | None = None,
) -> str:
    details = salient_details if salient_details is not None else []
    if not details:
        return summary

    lowered_summary = summary.casefold()
    missing = [detail for detail in details if detail.casefold() not in lowered_summary]
    if not missing:
        return summary

    return f"{summary.rstrip()} Key details from user: {'; '.join(missing)}."


def _ensure_relative_dates_have_absolute_dates(
    summary: str,
    *,
    pairs: list[tuple[str, str]],
    pair_started_at: list[datetime | None],
    conversation_started_at: datetime | None,
) -> str:
    lowered_summary = summary.casefold()
    contexts: list[str] = []
    seen: set[str] = set()

    for match in _RELATIVE_DAY_RE.finditer(summary):
        phrase = match.group(1).casefold()
        base_date = _base_date_for_relative_phrase(
            phrase,
            pairs=pairs,
            pair_started_at=pair_started_at,
            conversation_started_at=conversation_started_at,
        )
        if base_date is None:
            continue
        resolved = base_date + timedelta(days=_RELATIVE_DAY_OFFSETS[phrase])
        _append_relative_date_context(contexts, seen, phrase, resolved, lowered_summary)

    for match in _RELATIVE_WEEK_RE.finditer(summary):
        phrase = f"{match.group(1).casefold()} week"
        base_date = _base_date_for_relative_phrase(
            phrase,
            pairs=pairs,
            pair_started_at=pair_started_at,
            conversation_started_at=conversation_started_at,
        )
        if base_date is None:
            continue
        start, end = _relative_week_range(base_date, phrase)
        if start.isoformat() in lowered_summary and end.isoformat() in lowered_summary:
            continue
        if phrase in seen:
            continue
        seen.add(phrase)
        contexts.append(f"{phrase}={start.isoformat()} to {end.isoformat()}")

    if not contexts:
        return summary
    return f"{summary.rstrip()} Relative date context: {'; '.join(contexts)}."


def _base_date_for_relative_phrase(
    phrase: str,
    *,
    pairs: list[tuple[str, str]],
    pair_started_at: list[datetime | None],
    conversation_started_at: datetime | None,
) -> date | None:
    folded_phrase = phrase.casefold()
    matching_dates: list[date] = []
    for index, (user_message, assistant_message) in enumerate(pairs):
        if index >= len(pair_started_at):
            continue
        pair_date = _datetime_date(pair_started_at[index])
        if pair_date is None:
            continue
        turn_text = f"{user_message} {assistant_message}".casefold()
        if folded_phrase in turn_text:
            matching_dates.append(pair_date)

    unique_matching_dates = _unique_dates(matching_dates)
    if len(unique_matching_dates) == 1:
        return unique_matching_dates[0]
    if len(unique_matching_dates) > 1:
        return None

    all_pair_dates = _unique_dates(
        pair_date
        for pair_time in pair_started_at
        if (pair_date := _datetime_date(pair_time)) is not None
    )
    if len(all_pair_dates) == 1:
        return all_pair_dates[0]
    if len(all_pair_dates) > 1:
        return None
    return _datetime_date(conversation_started_at)


def _datetime_date(value: datetime | None) -> date | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.date()


def _unique_dates(values: Iterable[date]) -> list[date]:
    result: list[date] = []
    seen: set[date] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _append_relative_date_context(
    contexts: list[str],
    seen: set[str],
    phrase: str,
    resolved: date,
    lowered_summary: str,
) -> None:
    if phrase in seen:
        return
    iso_value = resolved.isoformat()
    if iso_value in lowered_summary:
        return
    seen.add(phrase)
    contexts.append(f"{phrase}={iso_value}")


def _relative_week_range(base_date: date, phrase: str) -> tuple[date, date]:
    week_start = base_date - timedelta(days=base_date.weekday())
    if phrase == "last week":
        week_start -= timedelta(days=7)
    elif phrase == "next week":
        week_start += timedelta(days=7)
    return week_start, week_start + timedelta(days=6)
