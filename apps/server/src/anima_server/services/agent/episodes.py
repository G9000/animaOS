from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models import MemoryDailyLog, MemoryEpisode
from anima_server.services.agent.json_utils import parse_json_object
from anima_server.services.data_crypto import df, ef

logger = logging.getLogger(__name__)

EPISODE_MIN_TURNS = 3
EPISODE_SEQUENTIAL_MAX_TURNS = 6


async def maybe_generate_episode(
    *,
    user_id: int,
    thread_id: int | None = None,
    db_factory: Callable[..., object] | None = None,
) -> MemoryEpisode | None:
    """Check if there are enough un-episoded turns today and generate an episode if so."""
    from anima_server.db.session import SessionLocal

    factory = db_factory or SessionLocal

    # ── Phase 1: Read — gather logs then release the session ──────
    # Holding a session open during slow LLM calls causes SQLite
    # "database is locked" errors when other writers need access.
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

        # Fetch daily logs created today that haven't been consumed by episodes
        logs = list(
            db.scalars(
                select(MemoryDailyLog)
                .where(
                    MemoryDailyLog.user_id == user_id,
                    MemoryDailyLog.created_at >= datetime.now(UTC) - timedelta(hours=24),
                )
                .order_by(MemoryDailyLog.created_at)
            ).all()
        )

    # Calculate how many new logs we have since last episode
    available_logs = logs[consumed_turns:] if consumed_turns < len(logs) else []

    if len(available_logs) < EPISODE_MIN_TURNS:
        return None

    # ── Phase 2: LLM call — no session held open ────────────
    from anima_server.services.agent import batch_segmenter as _bs

    if _bs.should_batch_segment(len(available_logs)):
        # Batch path: LLM groups messages into topic-coherent segments.
        messages = [
            (
                df(user_id, log.user_message, table="memory_daily_logs", field="user_message")
                or "",
                df(
                    user_id,
                    log.assistant_response,
                    table="memory_daily_logs",
                    field="assistant_response",
                )
                or "",
            )
            for log in available_logs
        ]
        try:
            groups_1based = await _bs.segment_messages_batch(messages, user_id=user_id)
            segments_0based = _bs.indices_to_0based(groups_1based)
        except Exception:
            segments_0based = []

        if segments_0based:
            with factory() as db:
                episodes = await _bs.generate_episodes_from_segments(
                    db,
                    user_id=user_id,
                    thread_id=thread_id,
                    logs=available_logs,
                    segments=segments_0based,
                    today=today,
                )
                db.commit()
                return episodes[-1] if episodes else None

    # Sequential path (below BATCH_THRESHOLD or batch segmentation failed).
    sequential_logs = available_logs[:EPISODE_SEQUENTIAL_MAX_TURNS]
    parsed = await _call_llm_for_episode_safe(sequential_logs, user_id=user_id)

    # ── Phase 3: Write — short-lived session for DB updates ──
    with factory() as db:
        episode = _build_episode_from_parsed(
            db,
            parsed=parsed,
            user_id=user_id,
            thread_id=thread_id,
            logs=sequential_logs,
            today=today,
        )
        db.commit()
        return episode


def _create_fallback_episode(
    db: Session,
    *,
    user_id: int,
    thread_id: int | None,
    logs: list[MemoryDailyLog],
    today: str,
) -> MemoryEpisode:
    """Create a basic episode without LLM when generation fails."""
    user_msgs = [
        df(user_id, log.user_message, table="memory_daily_logs", field="user_message")
        for log in logs
        if log.user_message
    ]
    preview = user_msgs[0][:80] if user_msgs else "Conversation"

    episode = MemoryEpisode(
        user_id=user_id,
        thread_id=thread_id,
        date=today,
        summary=ef(user_id, f"Session: {preview}...", table="memory_episodes", field="summary"),
        topics_json=None,
        emotional_arc=None,
        significance_score=2,
        turn_count=len(logs),
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
    logs: list[MemoryDailyLog],
    today: str,
) -> MemoryEpisode:
    """Create a MemoryEpisode from pre-parsed LLM output (or fallback)."""
    if parsed is None:
        return _create_fallback_episode(
            db,
            user_id=user_id,
            thread_id=thread_id,
            logs=logs,
            today=today,
        )

    summary = parsed.get("summary", "")
    if not summary or not isinstance(summary, str):
        summary = "Conversation session"
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

    episode = MemoryEpisode(
        user_id=user_id,
        thread_id=thread_id,
        date=today,
        summary=ef(user_id, summary, table="memory_episodes", field="summary"),
        topics_json=topics if topics else None,
        emotional_arc=ef(user_id, emotional_arc, table="memory_episodes", field="emotional_arc"),
        significance_score=significance,
        turn_count=len(logs),
    )
    db.add(episode)
    db.flush()

    # Attempt merge with recent episode
    return _merge_episodes(db, new_episode=episode, user_id=user_id)


async def _call_llm_for_episode(
    logs: list[MemoryDailyLog], *, user_id: int = 0, agent_name: str = "Anima"
) -> dict[str, Any]:
    from anima_server.services.agent.llm import create_llm
    from anima_server.services.agent.messages import HumanMessage, SystemMessage
    from anima_server.services.agent.prompt_loader import PromptLoader

    # Create a prompt loader with the agent name (no db access needed)
    prompt_loader = PromptLoader(agent_name=agent_name)

    turns_text = "\n".join(
        f"User: {df(user_id, log.user_message, table='memory_daily_logs', field='user_message')}\nAssistant: {df(user_id, log.assistant_response, table='memory_daily_logs', field='assistant_response')}"
        for log in logs
    )

    # Use templated prompt
    prompt = prompt_loader.episode_generation(turns=turns_text)

    llm = create_llm()
    response = await llm.ainvoke(
        [
            SystemMessage(content="You generate episode summaries. Respond only with JSON."),
            HumanMessage(content=prompt),
        ]
    )
    content = getattr(response, "content", "")
    if not isinstance(content, str):
        content = str(content)
    return _parse_json_object(content)


def _parse_json_object(text: str) -> dict[str, Any]:
    return parse_json_object(text) or {}


async def _call_llm_for_episode_safe(
    logs: list[MemoryDailyLog], *, user_id: int = 0
) -> dict[str, Any] | None:
    """Call LLM for episode generation, returning None on failure."""
    try:
        # Try to get agent name from first log's user, but fallback to default
        agent_name = "Anima"
        return await _call_llm_for_episode(logs, user_id=user_id, agent_name=agent_name)
    except Exception:
        logger.exception("LLM episode generation failed, using fallback")
        return None
