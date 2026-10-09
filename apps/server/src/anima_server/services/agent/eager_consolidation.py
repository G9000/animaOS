"""Eager consolidation and disposable Runtime retention helpers."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.db.helpers import session_scope
from anima_server.models.runtime import RuntimeMessage, RuntimeThread
from anima_server.services.corefs.sealed_runtime import delete_sealed_runtime_records

logger = logging.getLogger(__name__)

degraded_logger = logging.getLogger("anima.runtime.degraded")


def _get_runtime_db_factory() -> Callable[..., Session]:
    from anima_server.db.runtime import get_runtime_session_factory

    return get_runtime_session_factory()


def _get_soul_db_factory(user_id: int) -> Callable[..., object]:
    from anima_server.db.session import get_user_session_factory

    return get_user_session_factory(user_id)


async def on_thread_close(
    *,
    thread_id: int,
    user_id: int,
    runtime_db_factory: Callable[..., Session] | None = None,
    soul_db_factory: Callable[..., object] | None = None,
) -> None:
    """Canonical messages are durable at append; consolidation uses Runtime candidates."""
    from anima_server.services.corefs.conversation_authority import (
        active_conversation_authority_session,
    )

    if active_conversation_authority_session(user_id) is None:
        raise RuntimeError("Thread closure requires authenticated Core conversation authority")


async def inactivity_sweep(
    *,
    runtime_db_factory: Callable[..., Session] | None = None,
    soul_db_factory: Callable[..., object] | None = None,
    inactivity_minutes: int = 5,
) -> int:
    """Close inactive conversations through current native authority."""
    from anima_server.services.corefs.conversation_authority import list_canonical_threads
    from anima_server.services.corefs.conversation_mutations import close_canonical_thread
    from anima_server.services.sessions import all_active_unlock_sessions

    cutoff = datetime.now(UTC) - timedelta(minutes=inactivity_minutes)
    count = 0
    seen = set()
    for session in all_active_unlock_sessions():
        if session.user_id in seen or session.corefs_keys is None:
            continue
        seen.add(session.user_id)
        try:
            for view in list_canonical_threads(session=session):
                stamp = view.document.last_message_at
                if view.document.status != "active" or stamp is None:
                    continue
                if datetime.fromisoformat(stamp.replace("Z", "+00:00")) < cutoff:
                    _, changed = close_canonical_thread(
                        session=session, thread_id=view.document.thread_id
                    )
                    count += int(changed)
        except Exception:
            logger.exception("Canonical inactivity sweep failed for owner %s", session.user_id)
    return count


async def prune_expired_messages(
    *,
    runtime_db_factory: Callable[..., Session] | None = None,
) -> int:
    """Delete old messages from archived threads only."""
    if settings.message_ttl_days <= 0:
        return 0

    resolved_runtime_db_factory = runtime_db_factory or _get_runtime_db_factory()
    cutoff = datetime.now(UTC) - timedelta(days=settings.message_ttl_days)

    try:
        with session_scope(resolved_runtime_db_factory) as db:
            archived_thread_ids = db.scalars(
                select(RuntimeThread.id).where(RuntimeThread.is_archived.is_(True))
            ).all()
            if not archived_thread_ids:
                return 0

            expired_messages = db.execute(
                select(RuntimeMessage.id, RuntimeMessage.user_id).where(
                    RuntimeMessage.created_at < cutoff,
                    RuntimeMessage.thread_id.in_(archived_thread_ids),
                )
            ).all()
            if not expired_messages:
                return 0
            ids_by_owner: dict[int, list[int]] = {}
            for message_id, user_id in expired_messages:
                ids_by_owner.setdefault(int(user_id), []).append(int(message_id))
            for owner_id, message_ids in ids_by_owner.items():
                delete_sealed_runtime_records(
                    db,
                    row_type="runtime_message",
                    row_ids=message_ids,
                    owner_id=owner_id,
                )
            result = db.execute(
                delete(RuntimeMessage).where(
                    RuntimeMessage.id.in_([int(message_id) for message_id, _ in expired_messages])
                )
            )
            deleted = int(result.rowcount or 0)
        if deleted:
            logger.info("Pruned %d expired archived runtime messages", deleted)
        return deleted
    except Exception:
        logger.exception("Message pruning failed")
        return 0


async def prune_old_background_task_runs(
    *,
    runtime_db_factory: Callable[..., Session] | None = None,
) -> int:
    """Delete finished background task-run rows past the retention window.

    ``RuntimeBackgroundTaskRun`` grows ~6-10 rows every third turn and nothing
    pruned it.  Now that the consolidation cursor lives in its own table
    (``runtime_consolidation_cursors``) rather than in these rows'
    ``result_json``, old completed/failed runs can be dropped safely without
    losing the restart cursor.  In-flight rows (pending/running) are kept.
    """
    from anima_server.models.runtime import RuntimeBackgroundTaskRun

    if settings.background_task_run_retention_days <= 0:
        return 0

    resolved_runtime_db_factory = runtime_db_factory or _get_runtime_db_factory()
    cutoff = datetime.now(UTC) - timedelta(days=settings.background_task_run_retention_days)
    try:
        with session_scope(resolved_runtime_db_factory) as db:
            expired_runs = db.execute(
                select(
                    RuntimeBackgroundTaskRun.id,
                    RuntimeBackgroundTaskRun.user_id,
                ).where(
                    RuntimeBackgroundTaskRun.status.in_(["completed", "failed"]),
                    RuntimeBackgroundTaskRun.created_at < cutoff,
                )
            ).all()
            if not expired_runs:
                return 0
            ids_by_owner: dict[int, list[int]] = {}
            for run_id, user_id in expired_runs:
                ids_by_owner.setdefault(int(user_id), []).append(int(run_id))
            for owner_id, run_ids in ids_by_owner.items():
                delete_sealed_runtime_records(
                    db,
                    row_type="runtime_background_task_run",
                    row_ids=run_ids,
                    owner_id=owner_id,
                )
            result = db.execute(
                delete(RuntimeBackgroundTaskRun).where(
                    RuntimeBackgroundTaskRun.id.in_([int(run_id) for run_id, _ in expired_runs])
                )
            )
            deleted = int(result.rowcount or 0)
        if deleted:
            logger.info("Pruned %d expired background task-run rows", deleted)
        return deleted
    except Exception:
        logger.exception("Background task-run pruning failed")
        return 0


async def prune_expired_transcripts() -> int:
    """Conversation retention is explicit native mutation; sidecar archives are unsupported."""
    return 0
