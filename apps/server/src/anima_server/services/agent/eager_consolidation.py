"""Eager consolidation and archive lifecycle helpers."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.db.helpers import session_scope
from anima_server.models.agent_runtime import MemoryEpisode
from anima_server.models.runtime import RuntimeMessage, RuntimeThread
from anima_server.services import anima_core_retrieval
from anima_server.services.agent.episodes import maybe_generate_episode
from anima_server.services.agent.persistence import list_transcript_messages
from anima_server.services.agent.soul_writer import run_soul_writer
from anima_server.services.agent.transcript_archive import (
    export_transcript,
    load_transcript_sidecar,
    messages_to_transcript_dicts,
    resolve_transcript_path,
)
from anima_server.services.corefs.sealed_runtime import delete_sealed_runtime_records
from anima_server.services.data_crypto import df
from anima_server.services.sessions import get_active_dek_async

logger = logging.getLogger(__name__)

# Terminal archival give-ups log here so permanently-unarchived threads
# stay greppable instead of retrying every sweep forever.
degraded_logger = logging.getLogger("anima.runtime.degraded")

# Archival retry policy: exponential backoff (1m, 2m, 4m, ... capped at
# 60m) and a terminal archive_failed state after this many attempts.
_ARCHIVE_MAX_RETRIES = 8
_ARCHIVE_BACKOFF_BASE_MINUTES = 1
_ARCHIVE_BACKOFF_CAP_MINUTES = 60


def _get_transcripts_dir() -> Path:
    return settings.data_dir / "transcripts"


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
    """Run consolidation and archival after a thread is closed."""
    resolved_runtime_db_factory = runtime_db_factory or _get_runtime_db_factory()
    resolved_soul_db_factory = soul_db_factory or _get_soul_db_factory(user_id)

    try:
        await run_soul_writer(user_id)
    except Exception:
        logger.warning(
            "Pending ops consolidation failed for thread %d",
            thread_id,
            exc_info=True,
        )

    episode: MemoryEpisode | None = None
    try:
        episode = await maybe_generate_episode(
            user_id=user_id,
            thread_id=thread_id,
            db_factory=resolved_soul_db_factory,
        )
    except Exception:
        logger.warning(
            "Episode generation failed for thread %d",
            thread_id,
            exc_info=True,
        )

    try:
        with session_scope(resolved_runtime_db_factory) as db:
            messages = list_transcript_messages(db, thread_id=thread_id)
            dek = await get_active_dek_async(user_id, "conversations")
            episode_ids = _episode_ids(episode)
            sidecar_summary = _episode_summary(episode, user_id=user_id)

            if messages:
                export_result = export_transcript(
                    messages=messages_to_transcript_dicts(messages),
                    thread_id=thread_id,
                    user_id=user_id,
                    dek=dek,
                    transcripts_dir=_get_transcripts_dir(),
                    episode_ids=episode_ids,
                    summary=sidecar_summary,
                )
                if episode is not None and episode.id is not None:
                    _link_episode_to_transcript(
                        episode_id=episode.id,
                        transcript_ref=export_result.enc_path.name,
                        soul_db_factory=resolved_soul_db_factory,
                    )
                if dek is None:
                    logger.warning(
                        "Exported plaintext transcript for thread %d because no conversations DEK is active",
                        thread_id,
                    )
                else:
                    logger.info(
                        "Exported transcript for thread %d (%d messages)",
                        thread_id,
                        len(messages),
                    )

            thread = db.get(RuntimeThread, thread_id)
            if thread is not None:
                thread.is_archived = True
    except Exception:
        logger.exception("Thread close archival failed for thread %d", thread_id)


def _episode_ids(episode: MemoryEpisode | None) -> list[str]:
    if episode is None or episode.id is None:
        return []
    return [str(episode.id)]


def _episode_summary(episode: MemoryEpisode | None, *, user_id: int) -> str | None:
    if episode is None or not episode.summary:
        return None
    summary = df(user_id, episode.summary, table="memory_episodes", field="summary").strip()
    return summary or None


def _link_episode_to_transcript(
    *,
    episode_id: int,
    transcript_ref: str,
    soul_db_factory: Callable[..., object],
) -> None:
    with session_scope(soul_db_factory) as db:
        if not isinstance(db, Session):
            raise TypeError("Expected SQLAlchemy Session from soul_db_factory")
        episode = db.get(MemoryEpisode, episode_id)
        if episode is None:
            return
        episode.transcript_ref = transcript_ref


async def inactivity_sweep(
    *,
    runtime_db_factory: Callable[..., Session] | None = None,
    soul_db_factory: Callable[..., object] | None = None,
    inactivity_minutes: int = 5,
) -> int:
    """Close stale active threads and trigger archival."""
    resolved_runtime_db_factory = runtime_db_factory or _get_runtime_db_factory()
    cutoff = datetime.now(UTC) - timedelta(minutes=inactivity_minutes)

    stale_threads: list[tuple[int, int]] = []
    retry_threads: list[tuple[int, int]] = []
    try:
        with session_scope(resolved_runtime_db_factory) as db:
            stale_threads = [
                (int(thread_id), int(user_id))
                for thread_id, user_id in db.execute(
                    select(RuntimeThread.id, RuntimeThread.user_id).where(
                        RuntimeThread.status == "active",
                        RuntimeThread.last_message_at.isnot(None),
                        RuntimeThread.last_message_at < cutoff,
                    )
                ).all()
            ]
            now = datetime.now(UTC)
            retry_threads = [
                (int(thread_id), int(user_id))
                for thread_id, user_id in db.execute(
                    select(RuntimeThread.id, RuntimeThread.user_id).where(
                        RuntimeThread.status == "closed",
                        RuntimeThread.is_archived.is_(False),
                        RuntimeThread.archive_failed.is_(False),
                        or_(
                            RuntimeThread.archive_next_retry_at.is_(None),
                            RuntimeThread.archive_next_retry_at <= now,
                        ),
                    )
                ).all()
            ]

            closed_at = datetime.now(UTC)
            for thread_id, _user_id in stale_threads:
                thread = db.get(RuntimeThread, thread_id)
                if thread is None:
                    continue
                thread.status = "closed"
                thread.closed_at = closed_at
    except Exception:
        logger.exception("Inactivity sweep failed")
        return 0

    for thread_id, user_id in stale_threads + retry_threads:
        try:
            await on_thread_close(
                thread_id=thread_id,
                user_id=user_id,
                runtime_db_factory=resolved_runtime_db_factory,
                soul_db_factory=soul_db_factory or _get_soul_db_factory(user_id),
            )
        except Exception:
            logger.warning(
                "Failed to consolidate closed thread %d",
                thread_id,
                exc_info=True,
            )
        _update_archival_retry_state(resolved_runtime_db_factory, thread_id=thread_id)

    if stale_threads:
        logger.info("Inactivity sweep closed %d threads", len(stale_threads))
    if retry_threads:
        logger.info("Inactivity sweep retried archival for %d closed threads", len(retry_threads))
    return len(stale_threads)


def _update_archival_retry_state(
    runtime_db_factory: Callable[..., Session],
    *,
    thread_id: int,
) -> None:
    """Record the outcome of an archival attempt.

    Success (thread archived) clears the retry state; failure schedules the
    next attempt with exponential backoff and, at the cap, marks the thread
    terminally `archive_failed` so the sweep stops retrying it every minute.
    """
    try:
        with session_scope(runtime_db_factory) as db:
            thread = db.get(RuntimeThread, thread_id)
            if thread is None:
                return
            if thread.is_archived or thread.status != "closed":
                if thread.archive_retry_count or thread.archive_next_retry_at:
                    thread.archive_retry_count = 0
                    thread.archive_next_retry_at = None
                return

            thread.archive_retry_count = (thread.archive_retry_count or 0) + 1
            if thread.archive_retry_count >= _ARCHIVE_MAX_RETRIES:
                thread.archive_failed = True
                thread.archive_next_retry_at = None
                degraded_logger.warning(
                    "Thread %d archival permanently failed after %d attempts; "
                    "giving up (clear archive_failed to retry manually)",
                    thread_id,
                    thread.archive_retry_count,
                )
            else:
                delay_minutes = min(
                    _ARCHIVE_BACKOFF_BASE_MINUTES * 2 ** (thread.archive_retry_count - 1),
                    _ARCHIVE_BACKOFF_CAP_MINUTES,
                )
                thread.archive_next_retry_at = datetime.now(UTC) + timedelta(minutes=delay_minutes)
    except Exception:
        logger.exception("Failed to update archival retry state for thread %d", thread_id)


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
                    RuntimeBackgroundTaskRun.id.in_(
                        [int(run_id) for run_id, _ in expired_runs]
                    )
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
    """Delete transcript artifacts older than the configured retention window."""
    if settings.transcript_retention_days < 0:
        return 0

    transcripts_dir = _get_transcripts_dir()
    if not transcripts_dir.exists():
        return 0

    cutoff = datetime.now(UTC) - timedelta(days=settings.transcript_retention_days)
    deleted = 0

    for meta_path in list(transcripts_dir.glob("*.meta.json")):
        try:
            meta = load_transcript_sidecar(meta_path)
            if meta is None:
                continue
            archived_at = datetime.fromisoformat(
                str(meta.get("archived_at", "")).replace("Z", "+00:00")
            )
        except ValueError:
            continue

        if archived_at >= cutoff:
            continue

        transcript_path = resolve_transcript_path(meta_path)
        try:
            if transcript_path is not None and transcript_path.exists():
                transcript_path.unlink()
            meta_path.unlink()
            try:
                anima_core_retrieval.transcript_index_delete(
                    root=anima_core_retrieval.get_retrieval_root(),
                    thread_id=int(meta.get("thread_id", 0)),
                    user_id=int(meta.get("user_id", 0)),
                )
            except RuntimeError:
                logger.debug(
                    "Rust transcript index delete is unavailable during transcript pruning"
                )
            except Exception:
                logger.warning(
                    "Failed to delete transcript %s from the Rust retrieval index during pruning",
                    meta_path.name,
                    exc_info=True,
                )
                try:
                    anima_core_retrieval.mark_retrieval_index_dirty(
                        root=anima_core_retrieval.get_retrieval_root(),
                        family="transcript",
                    )
                except Exception:
                    logger.debug(
                        "Failed to mark transcript index dirty during pruning", exc_info=True
                    )
            deleted += 1
        except OSError:
            logger.warning(
                "Failed to delete expired transcript artifact %s", meta_path.name, exc_info=True
            )

    if deleted:
        logger.info("Pruned %d expired transcripts", deleted)
    return deleted
