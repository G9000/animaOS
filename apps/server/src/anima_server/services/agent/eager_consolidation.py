"""Eager consolidation and archive lifecycle helpers."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models.agent_runtime import MemoryEpisode
from anima_server.models.runtime import RuntimeMessage, RuntimeThread
from anima_server.services.agent.episodes import maybe_generate_episode
from anima_server.services.agent.persistence import list_transcript_messages
from anima_server.services.agent.soul_writer import run_soul_writer
from anima_server.services.agent.transcript_archive import (
    export_transcript,
    messages_to_transcript_dicts,
)
from anima_server.services.data_crypto import df, get_active_dek

logger = logging.getLogger(__name__)


def _get_transcripts_dir() -> Path:
    return settings.data_dir / "transcripts"


def _get_runtime_db_factory() -> Callable[..., Session]:
    from anima_server.db.runtime import get_runtime_session_factory

    return get_runtime_session_factory()


def _get_soul_db_factory() -> Callable[..., object]:
    from anima_server.db.session import SessionLocal

    return SessionLocal


async def on_thread_close(
    *,
    thread_id: int,
    user_id: int,
    runtime_db_factory: Callable[..., Session] | None = None,
    soul_db_factory: Callable[..., object] | None = None,
) -> None:
    """Run consolidation and archival after a thread is closed."""
    resolved_runtime_db_factory = runtime_db_factory or _get_runtime_db_factory()
    resolved_soul_db_factory = soul_db_factory or _get_soul_db_factory()

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

    db = resolved_runtime_db_factory()
    try:
        messages = list_transcript_messages(db, thread_id=thread_id)
        dek = get_active_dek(user_id, "conversations")
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
            db.commit()
    except Exception:
        logger.exception("Thread close archival failed for thread %d", thread_id)
        db.rollback()
    finally:
        db.close()


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
    with soul_db_factory() as db:
        if not isinstance(db, Session):
            raise TypeError("Expected SQLAlchemy Session from soul_db_factory")
        episode = db.get(MemoryEpisode, episode_id)
        if episode is None:
            return
        episode.transcript_ref = transcript_ref
        db.commit()


async def inactivity_sweep(
    *,
    runtime_db_factory: Callable[..., Session] | None = None,
    soul_db_factory: Callable[..., object] | None = None,
    inactivity_minutes: int = 5,
) -> int:
    """Close stale active threads and trigger archival."""
    resolved_runtime_db_factory = runtime_db_factory or _get_runtime_db_factory()
    resolved_soul_db_factory = soul_db_factory or _get_soul_db_factory()
    cutoff = datetime.now(UTC) - timedelta(minutes=inactivity_minutes)

    stale_threads: list[tuple[int, int]] = []
    retry_threads: list[tuple[int, int]] = []
    db = resolved_runtime_db_factory()
    try:
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
        retry_threads = [
            (int(thread_id), int(user_id))
            for thread_id, user_id in db.execute(
                select(RuntimeThread.id, RuntimeThread.user_id).where(
                    RuntimeThread.status == "closed",
                    RuntimeThread.is_archived.is_(False),
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

        db.commit()
    except Exception:
        logger.exception("Inactivity sweep failed")
        db.rollback()
        return 0
    finally:
        db.close()

    for thread_id, user_id in stale_threads + retry_threads:
        try:
            await on_thread_close(
                thread_id=thread_id,
                user_id=user_id,
                runtime_db_factory=resolved_runtime_db_factory,
                soul_db_factory=resolved_soul_db_factory,
            )
        except Exception:
            logger.warning(
                "Failed to consolidate closed thread %d",
                thread_id,
                exc_info=True,
            )

    if stale_threads:
        logger.info("Inactivity sweep closed %d threads", len(stale_threads))
    if retry_threads:
        logger.info("Inactivity sweep retried archival for %d closed threads", len(retry_threads))
    return len(stale_threads)


async def prune_expired_messages(
    *,
    runtime_db_factory: Callable[..., Session] | None = None,
) -> int:
    """Delete old messages from archived threads only."""
    if settings.message_ttl_days <= 0:
        return 0

    resolved_runtime_db_factory = runtime_db_factory or _get_runtime_db_factory()
    cutoff = datetime.now(UTC) - timedelta(days=settings.message_ttl_days)
    db = resolved_runtime_db_factory()

    try:
        archived_thread_ids = db.scalars(
            select(RuntimeThread.id).where(RuntimeThread.is_archived.is_(True))
        ).all()
        if not archived_thread_ids:
            return 0

        result = db.execute(
            delete(RuntimeMessage).where(
                RuntimeMessage.created_at < cutoff,
                RuntimeMessage.thread_id.in_(archived_thread_ids),
            )
        )
        db.commit()
        deleted = int(result.rowcount or 0)
        if deleted:
            logger.info("Pruned %d expired archived runtime messages", deleted)
        return deleted
    except Exception:
        logger.exception("Message pruning failed")
        db.rollback()
        return 0
    finally:
        db.close()


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
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            archived_at = datetime.fromisoformat(str(meta.get("archived_at", "")).replace("Z", "+00:00"))
        except (OSError, json.JSONDecodeError, ValueError):
            continue

        if archived_at >= cutoff:
            continue

        transcript_path = meta_path.parent / meta_path.name.replace(".meta.json", ".jsonl.enc")
        if not transcript_path.exists():
            transcript_path = meta_path.parent / meta_path.name.replace(".meta.json", ".jsonl")
        try:
            if transcript_path.exists():
                transcript_path.unlink()
            meta_path.unlink()
            deleted += 1
        except OSError:
            logger.warning("Failed to delete expired transcript artifact %s", meta_path.name, exc_info=True)

    if deleted:
        logger.info("Pruned %d expired transcripts", deleted)
    return deleted
