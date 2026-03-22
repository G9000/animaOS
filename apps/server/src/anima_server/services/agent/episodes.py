from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models import MemoryDailyLog, MemoryEpisode
from anima_server.services.data_crypto import ef, df

logger = logging.getLogger(__name__)

EPISODE_MIN_TURNS = 3

EPISODE_GENERATION_PROMPT = """You are a memory system for a personal AI companion.
Given a set of conversation turns from a single session, generate a concise episode summary.

Return a JSON object with:
- "summary": 1-2 sentence summary of the conversation (what happened, what was discussed)
- "topics": array of 1-5 short topic labels (e.g. ["work", "health", "python"])
- "emotional_arc": brief description of the emotional flow (e.g. "curious -> satisfied", "frustrated -> relieved")
- "significance": 1-5 integer (5 = life-changing moment, 1 = casual small talk)

Rules:
- Focus on what matters for long-term memory
- Be concise but capture the essence
- Return valid JSON only

Conversation turns:
{turns}"""


async def maybe_generate_episode(
    *,
    user_id: int,
    thread_id: int | None = None,
    db_factory: Callable[..., object] | None = None,
) -> MemoryEpisode | None:
    """Check if there are enough un-episoded turns today and generate an episode if so."""
    from anima_server.db.session import SessionLocal

    factory = db_factory or SessionLocal

    with factory() as db:
        today = datetime.now(UTC).date().isoformat()

        # Sum actual turn_count from existing episodes to avoid offset overlap
        consumed_turns = db.scalar(
            select(func.coalesce(func.sum(MemoryEpisode.turn_count), 0)).where(
                MemoryEpisode.user_id == user_id,
                MemoryEpisode.date == today,
            )
        ) or 0

        logs = list(
            db.scalars(
                select(MemoryDailyLog)
                .where(
                    MemoryDailyLog.user_id == user_id,
                    MemoryDailyLog.date == today,
                )
                .order_by(MemoryDailyLog.created_at.asc())
            ).all()
        )

        remaining_logs = logs[consumed_turns:]

        if len(remaining_logs) < EPISODE_MIN_TURNS:
            return None

        # --- Batch segmentation path ---
        from anima_server.services.agent.batch_segmenter import (
            generate_episodes_from_segments,
            indices_to_0based,
            segment_messages_batch,
            should_batch_segment,
            validate_indices,
        )
        from anima_server.services.data_crypto import df as _df

        if should_batch_segment(len(remaining_logs)):
            try:
                messages = [
                    (
                        _df(user_id, log.user_message, table="memory_daily_logs", field="user_message"),
                        _df(user_id, log.assistant_response, table="memory_daily_logs", field="assistant_response"),
                    )
                    for log in remaining_logs
                ]
                groups = await segment_messages_batch(messages, user_id=user_id)

                if validate_indices(groups, len(remaining_logs)):
                    segments_0 = indices_to_0based(groups)
                    episodes = await generate_episodes_from_segments(
                        db,
                        user_id=user_id,
                        thread_id=thread_id,
                        logs=remaining_logs,
                        segments=segments_0,
                        today=today,
                    )
                    db.commit()
                    # Try merging each new episode with a recent one
                    result_episode = episodes[0] if episodes else None
                    for ep in episodes:
                        merged_into = _try_merge_episode(db, user_id, ep)
                        if merged_into is not None and ep is result_episode:
                            result_episode = merged_into
                    db.commit()
                    return result_episode
            except Exception:  # noqa: BLE001
                logger.exception("Batch segmentation failed, falling back to sequential")

        # --- Sequential path (original behavior) ---
        episode_logs = remaining_logs[:EPISODE_MIN_TURNS * 2]

        if settings.agent_provider == "scaffold":
            episode = _create_fallback_episode(
                db,
                user_id=user_id,
                thread_id=thread_id,
                logs=episode_logs,
                today=today,
            )
        else:
            episode = await _generate_episode_via_llm(
                db,
                user_id=user_id,
                thread_id=thread_id,
                logs=episode_logs,
                today=today,
            )

        db.commit()
        # Try merging with a recent episode on the same topic
        merged_into = _try_merge_episode(db, user_id, episode)
        if merged_into is not None:
            episode = merged_into
        db.commit()
        return episode


def _try_merge_episode(
    db: Session,
    user_id: int,
    new_episode: MemoryEpisode,
) -> bool:
    """Try to merge *new_episode* into a recent episode with overlapping topics.

    Uses Jaccard similarity on the topic lists.  If overlap > 50 %, the older
    episode absorbs the new one (summaries concatenated, topics unioned, higher
    significance kept, turn counts summed) and the new episode is deleted.

    Returns the merged-into episode if a merge happened, None otherwise.
    """
    new_topics: list[str] = new_episode.topics_json or []
    if not new_topics:
        return False

    new_set = {t.lower().strip() for t in new_topics}
    if not new_set:
        return False

    # Look back 7 days for merge candidates
    cutoff = (datetime.now(UTC).date() - timedelta(days=7)).isoformat()

    candidates = list(
        db.scalars(
            select(MemoryEpisode)
            .where(
                MemoryEpisode.user_id == user_id,
                MemoryEpisode.id != new_episode.id,
                MemoryEpisode.date >= cutoff,
            )
            .order_by(MemoryEpisode.id.desc())
        ).all()
    )

    for candidate in candidates:
        cand_topics: list[str] = candidate.topics_json or []
        if not cand_topics:
            continue
        cand_set = {t.lower().strip() for t in cand_topics}
        if not cand_set:
            continue

        intersection = new_set & cand_set
        union = new_set | cand_set
        jaccard = len(intersection) / len(union) if union else 0.0

        if jaccard <= 0.5:
            continue

        # --- Merge into *candidate* (the older episode) ---
        # Combine summaries
        existing_summary = df(
            user_id, candidate.summary,
            table="memory_episodes", field="summary",
        )
        new_summary = df(
            user_id, new_episode.summary,
            table="memory_episodes", field="summary",
        )
        merged_summary = f"{existing_summary}\n\n{new_summary}"
        candidate.summary = ef(
            user_id, merged_summary,
            table="memory_episodes", field="summary",
        )

        # Union topics (preserve original casing from both sides, deduplicate)
        seen_lower: set[str] = set()
        merged_topics: list[str] = []
        for t in cand_topics + new_topics:
            key = t.lower().strip()
            if key not in seen_lower:
                seen_lower.add(key)
                merged_topics.append(t)
        candidate.topics_json = merged_topics[:10]  # cap at 10

        # Keep higher significance
        new_sig = new_episode.significance_score or 3
        cand_sig = candidate.significance_score or 3
        candidate.significance_score = max(new_sig, cand_sig)

        # Sum turn counts
        cand_turns = candidate.turn_count or 0
        new_turns = new_episode.turn_count or 0
        candidate.turn_count = cand_turns + new_turns

        # Delete the new (duplicate) episode
        db.delete(new_episode)
        db.flush()

        logger.info(
            "Merged episode %d into episode %d (jaccard=%.2f, topics=%s)",
            new_episode.id, candidate.id, jaccard, merged_topics,
        )
        return candidate

    return None


def _create_fallback_episode(
    db: Session,
    *,
    user_id: int,
    thread_id: int | None,
    logs: list[MemoryDailyLog],
    today: str,
) -> MemoryEpisode:
    summaries = []
    for log in logs:
        text = df(user_id, log.user_message, table="memory_daily_logs", field="user_message")[:80]
        summaries.append(text)
    summary = "Conversation covering: " + "; ".join(summaries)
    if len(summary) > 500:
        summary = summary[:497] + "..."

    episode = MemoryEpisode(
        user_id=user_id,
        thread_id=thread_id,
        date=today,
        time=datetime.now(UTC).strftime("%H:%M:%S"),
        summary=ef(user_id, summary, table="memory_episodes", field="summary"),
        topics_json=["conversation"],
        significance_score=2,
        turn_count=len(logs),
    )
    db.add(episode)
    db.flush()
    return episode


async def _generate_episode_via_llm(
    db: Session,
    *,
    user_id: int,
    thread_id: int | None,
    logs: list[MemoryDailyLog],
    today: str,
) -> MemoryEpisode:
    try:
        parsed = await _call_llm_for_episode(logs, user_id=user_id)
    except Exception:  # noqa: BLE001
        logger.exception("LLM episode generation failed, using fallback")
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
    if not isinstance(significance, int) or not 1 <= significance <= 5:
        significance = 3

    episode = MemoryEpisode(
        user_id=user_id,
        thread_id=thread_id,
        date=today,
        time=datetime.now(UTC).strftime("%H:%M:%S"),
        summary=ef(user_id, summary, table="memory_episodes", field="summary"),
        topics_json=topics if topics else None,
        emotional_arc=ef(user_id, emotional_arc, table="memory_episodes", field="emotional_arc"),
        significance_score=significance,
        turn_count=len(logs),
    )
    db.add(episode)
    db.flush()
    return episode


async def _call_llm_for_episode(logs: list[MemoryDailyLog], *, user_id: int = 0) -> dict[str, Any]:
    from anima_server.services.agent.llm import create_llm
    from anima_server.services.agent.messages import HumanMessage, SystemMessage

    turns_text = "\n".join(
        f"User: {df(user_id, log.user_message, table='memory_daily_logs', field='user_message')}\nAssistant: {df(user_id, log.assistant_response, table='memory_daily_logs', field='assistant_response')}"
        for log in logs
    )
    prompt = EPISODE_GENERATION_PROMPT.format(turns=turns_text)

    llm = create_llm()
    response = await llm.ainvoke([
        SystemMessage(
            content="You generate episode summaries. Respond only with JSON."),
        HumanMessage(content=prompt),
    ])
    content = getattr(response, "content", "")
    if not isinstance(content, str):
        content = str(content)
    return _parse_json_object(content)


from anima_server.services.agent.json_utils import parse_json_object  # noqa: E302


def _parse_json_object(text: str) -> dict[str, Any]:
    return parse_json_object(text) or {}
