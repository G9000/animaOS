from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
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
        return episode


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
        text = df(user_id, log.user_message)[:80]
        summaries.append(text)
    summary = "Conversation covering: " + "; ".join(summaries)
    if len(summary) > 500:
        summary = summary[:497] + "..."

    episode = MemoryEpisode(
        user_id=user_id,
        thread_id=thread_id,
        date=today,
        time=datetime.now(UTC).strftime("%H:%M:%S"),
        summary=ef(user_id, summary),
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
        summary=ef(user_id, summary),
        topics_json=topics if topics else None,
        emotional_arc=ef(user_id, emotional_arc),
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
        f"User: {df(user_id, log.user_message)}\nAssistant: {df(user_id, log.assistant_response)}"
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


def _parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    try:
        parsed = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed
