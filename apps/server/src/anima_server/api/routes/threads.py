"""Thread management endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_session_async
from anima_server.config import settings
from anima_server.db import get_db, get_runtime_db
from anima_server.db.session import build_session_factory_for_db
from anima_server.models.runtime import RuntimeMessage, RuntimeThread
from anima_server.services.agent.compaction import estimate_message_tokens
from anima_server.services.agent.eager_consolidation import on_thread_close
from anima_server.services.agent.persistence import close_thread, create_thread, list_threads
from anima_server.services.agent.service import _track_background_task
from anima_server.services.agent.thread_manager import get_thread_messages_for_display
from anima_server.services.images.deletion import delete_thread_with_image_cleanup
from anima_server.services.sessions import get_active_dek_async

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/threads", tags=["threads"])


def _thread_to_dict(thread: RuntimeThread, first_role: str | None = None) -> dict[str, object]:
    initiated_by: str | None = None
    if first_role == "user":
        initiated_by = "user"
    elif first_role is not None:
        initiated_by = "agent"
    return {
        "id": thread.id,
        "userId": thread.user_id,
        "status": thread.status,
        "title": thread.title,
        "createdAt": thread.created_at.isoformat() if thread.created_at else None,
        "lastMessageAt": thread.last_message_at.isoformat() if thread.last_message_at else None,
        "closedAt": thread.closed_at.isoformat() if thread.closed_at else None,
        "isArchived": thread.is_archived,
        "initiatedBy": initiated_by,
    }


def _create_thread_response(thread: RuntimeThread) -> dict[str, object]:
    return {
        "threadId": thread.id,
        "status": thread.status,
        "thread": _thread_to_dict(thread),
    }


def _thread_has_messages(runtime_db: Session, thread_id: int) -> bool:
    return runtime_db.scalar(
        select(RuntimeMessage.id)
        .where(RuntimeMessage.thread_id == thread_id)
        .limit(1)
    ) is not None


@router.get("")
async def list_user_threads(
    request: Request,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, object]:
    """List all threads for the authenticated user, newest first."""
    unlock_session = await require_unlocked_session_async(request)
    rows = list_threads(runtime_db, user_id=unlock_session.user_id)
    return {
        "threads": [_thread_to_dict(t, first_role) for t, first_role in rows]
    }


@router.post("", status_code=201)
async def create_new_thread(
    request: Request,
    runtime_db: Session = Depends(get_runtime_db),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Create a new conversation thread, closing the existing active one."""
    unlock_session = await require_unlocked_session_async(request)
    user_id = unlock_session.user_id

    # Reuse the current active thread when it's still completely empty.
    active_thread = runtime_db.scalar(
        select(RuntimeThread).where(
            RuntimeThread.user_id == user_id,
            RuntimeThread.status == "active",
        )
    )
    if active_thread is not None and not _thread_has_messages(runtime_db, active_thread.id):
        return _create_thread_response(active_thread)

    # Identify the active thread (if any) so we can fire consolidation after closing it.
    old_thread_id: int | None = active_thread.id if active_thread is not None else None

    new_thread = create_thread(runtime_db, user_id)
    runtime_db.commit()

    if old_thread_id is not None:
        soul_db_factory = build_session_factory_for_db(db)
        # Strong-ref via the tracked set: the loop keeps only weak task
        # references, so a bare create_task can be GC'd mid-flight and
        # silently never consolidate.
        _track_background_task(
            on_thread_close(
                thread_id=old_thread_id,
                user_id=user_id,
                soul_db_factory=soul_db_factory,
            )
        )

    return _create_thread_response(new_thread)


@router.get("/{thread_id}/messages")
async def get_thread_messages(
    thread_id: int,
    request: Request,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, object]:
    """Return all messages for a thread (active from PG, archived from JSONL)."""
    unlock_session = await require_unlocked_session_async(request)
    thread = runtime_db.get(RuntimeThread, thread_id)
    if thread is None or thread.user_id != unlock_session.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")

    dek = await get_active_dek_async(unlock_session.user_id, "conversations")
    messages = get_thread_messages_for_display(
        runtime_db,
        thread=thread,
        user_id=unlock_session.user_id,
        transcripts_dir=settings.data_dir / "transcripts",
        dek=dek,
    )
    return {"threadId": thread_id, "messages": messages}


@router.post("/{thread_id}/close")
async def close_thread_endpoint(
    thread_id: int,
    request: Request,
    db: Session = Depends(get_db),
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, object]:
    """Close a thread and trigger background consolidation."""
    unlock_session = await require_unlocked_session_async(request)
    thread = runtime_db.get(RuntimeThread, thread_id)
    if thread is None or thread.user_id != unlock_session.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")

    if thread.status == "closed":
        return {"status": "already_closed", "threadId": thread_id}

    changed = close_thread(runtime_db, thread_id=thread_id)
    runtime_db.commit()

    if changed:
        soul_db_factory = build_session_factory_for_db(db)
        _track_background_task(
            on_thread_close(
                thread_id=thread_id,
                user_id=thread.user_id,
                soul_db_factory=soul_db_factory,
            )
        )

    return {"status": "closed", "threadId": thread_id}


@router.get("/{thread_id}/context-stats")
async def get_thread_context_stats(
    thread_id: int,
    request: Request,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, object]:
    """Return context window usage stats for a thread."""
    unlock_session = await require_unlocked_session_async(request)
    thread = runtime_db.get(RuntimeThread, thread_id)
    if thread is None or thread.user_id != unlock_session.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")

    rows = runtime_db.scalars(
        select(RuntimeMessage).where(
            RuntimeMessage.thread_id == thread_id,
            RuntimeMessage.is_in_context.is_(True),
            RuntimeMessage.is_archived_history.is_(False),
        )
    ).all()

    used_tokens = sum(
        row.token_estimate if row.token_estimate is not None
        else estimate_message_tokens(
            content_text=row.content_text,
            content_json=row.content_json,
            tool_name=row.tool_name,
        )
        for row in rows
    )

    compaction_count = runtime_db.scalar(
        select(func.count()).select_from(RuntimeMessage).where(
            RuntimeMessage.thread_id == thread_id,
            RuntimeMessage.role == "summary",
        )
    ) or 0

    budget = settings.agent_context_window_tokens
    pct = round(used_tokens / budget * 100, 1) if budget else None
    trigger_at = round(budget * settings.agent_compaction_trigger_ratio) if budget else None

    return {
        "threadId": thread_id,
        "usedTokens": used_tokens,
        "budgetTokens": budget,
        "triggerAtTokens": trigger_at,
        "pct": pct,
        "compactionCount": compaction_count,
        "messageCount": len(rows),
    }


@router.delete("/{thread_id}")
async def delete_thread_endpoint(
    thread_id: int,
    request: Request,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, object]:
    """Permanently delete a thread and all its messages."""
    unlock_session = await require_unlocked_session_async(request)
    thread = runtime_db.get(RuntimeThread, thread_id)
    if thread is None or thread.user_id != unlock_session.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")

    cleanup = delete_thread_with_image_cleanup(
        runtime_db,
        user_id=unlock_session.user_id,
        thread_id=thread_id,
    )
    runtime_db.commit()

    return {
        "status": "deleted",
        "threadId": thread_id,
        "assetsDeleted": cleanup.assets_deleted,
        "filesDeleted": cleanup.files_deleted,
    }
