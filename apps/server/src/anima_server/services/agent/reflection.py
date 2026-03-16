from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from anima_server.config import settings

logger = logging.getLogger(__name__)

REFLECTION_DELAY_SECONDS = 300  # 5 minutes of inactivity

_reflection_lock = Lock()
_pending_reflections: dict[int, asyncio.Task[None]] = {}
_last_activities: dict[int, datetime] = {}


def schedule_reflection(
    *,
    user_id: int,
    thread_id: int | None = None,
    db_factory: Callable[..., object] | None = None,
) -> None:
    """Schedule a reflection task after a period of inactivity.

    Each new call resets the timer for this user. Other users' timers are unaffected.
    """
    if not settings.agent_background_memory_enabled:
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    with _reflection_lock:
        now = datetime.now(UTC)
        _last_activities[user_id] = now

        existing = _pending_reflections.get(user_id)
        if existing is not None and not existing.done():
            existing.cancel()

        _pending_reflections[user_id] = loop.create_task(
            _delayed_reflection(
                user_id=user_id,
                thread_id=thread_id,
                scheduled_at=now,
                db_factory=db_factory,
            )
        )


async def _delayed_reflection(
    *,
    user_id: int,
    thread_id: int | None,
    scheduled_at: datetime,
    db_factory: Callable[..., object] | None,
) -> None:
    """Wait for the inactivity period, then run reflection if no new activity occurred."""
    try:
        await asyncio.sleep(REFLECTION_DELAY_SECONDS)
    except asyncio.CancelledError:
        return

    with _reflection_lock:
        last = _last_activities.get(user_id)
        if last is not None and last > scheduled_at:
            return

    await run_reflection(user_id=user_id, thread_id=thread_id, db_factory=db_factory)


async def run_reflection(
    *,
    user_id: int,
    thread_id: int | None = None,
    db_factory: Callable[..., object] | None = None,
) -> None:
    """Run sleep-time tasks + quick inner monologue reflection."""
    # 0. Expire working memory items
    try:
        from anima_server.db.session import SessionLocal
        from anima_server.services.agent.self_model import expire_working_memory_items

        factory = db_factory or SessionLocal
        with factory() as db:
            removed = expire_working_memory_items(db, user_id=user_id)
            if removed:
                db.commit()
                logger.info(
                    "Expired %d working memory items for user %s", removed, user_id)
    except Exception:  # noqa: BLE001
        logger.exception("Working memory expiry failed for user %s", user_id)

    # 1. Quick inner monologue (post-conversation reflection)
    try:
        from anima_server.services.agent.inner_monologue import run_quick_reflection

        reflection = await run_quick_reflection(
            user_id=user_id,
            thread_id=thread_id,
            db_factory=db_factory,
        )
        if reflection.inner_state_updated or reflection.emotional_signal_recorded:
            logger.info(
                "Quick reflection for user %s: inner_state=%s, emotional=%s, take=%s",
                user_id,
                reflection.inner_state_updated,
                reflection.emotional_signal_recorded,
                reflection.quick_take[:80] if reflection.quick_take else "",
            )
    except Exception:  # noqa: BLE001
        logger.exception("Quick reflection failed for user %s", user_id)

    # 2. Sleep-time maintenance tasks
    try:
        from anima_server.services.agent.sleep_tasks import run_sleep_tasks

        result = await run_sleep_tasks(user_id=user_id, db_factory=db_factory)
        if result.contradictions_resolved or result.items_merged or result.episodes_generated:
            logger.info(
                "Sleep tasks for user %s: %d contradictions resolved, %d items merged, "
                "%d episodes generated, %d embeddings backfilled",
                user_id,
                result.contradictions_resolved,
                result.items_merged,
                result.episodes_generated,
                result.embeddings_backfilled,
            )
    except Exception:  # noqa: BLE001
        logger.exception("Reflection failed for user %s", user_id)

    # Invalidate companion memory cache so the next turn picks up changes
    # made by working-memory expiry, inner monologue, and sleep tasks.
    from anima_server.services.agent.companion import get_companion
    companion = get_companion()
    if companion is not None and companion.user_id == user_id:
        companion.invalidate_memory()


async def cancel_pending_reflection(*, user_id: int | None = None) -> None:
    """Cancel pending reflection tasks. If user_id given, cancel only that user's."""
    tasks_to_await: list[asyncio.Task[None]] = []
    with _reflection_lock:
        if user_id is not None:
            task = _pending_reflections.pop(user_id, None)
            if task is not None and not task.done():
                task.cancel()
                tasks_to_await.append(task)
            _last_activities.pop(user_id, None)
        else:
            for task in list(_pending_reflections.values()):
                if not task.done():
                    task.cancel()
                    tasks_to_await.append(task)
            _pending_reflections.clear()
            _last_activities.clear()
    if tasks_to_await:
        await asyncio.gather(*tasks_to_await, return_exceptions=True)
