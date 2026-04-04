"""Thread management endpoints."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_session
from anima_server.config import settings
from anima_server.db import get_db, get_runtime_db
from anima_server.db.session import build_session_factory_for_db
from anima_server.models.runtime import RuntimeThread
from anima_server.models.runtime import RuntimeMessage
from anima_server.services.agent.eager_consolidation import on_thread_close
from anima_server.services.agent.persistence import close_thread, create_thread, list_threads
from anima_server.services.agent.thread_manager import get_thread_messages_for_display
from anima_server.services.sessions import get_active_dek

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/threads", tags=["threads"])


def _thread_to_dict(thread: RuntimeThread) -> dict[str, object]:
    return {
        "id": thread.id,
        "userId": thread.user_id,
        "status": thread.status,
        "title": thread.title,
        "createdAt": thread.created_at.isoformat() if thread.created_at else None,
        "lastMessageAt": thread.last_message_at.isoformat() if thread.last_message_at else None,
        "closedAt": thread.closed_at.isoformat() if thread.closed_at else None,
        "isArchived": thread.is_archived,
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
    unlock_session = require_unlocked_session(request)
    threads = list_threads(runtime_db, user_id=unlock_session.user_id)
    return {
        "threads": [_thread_to_dict(t) for t in threads]
    }


@router.post("", status_code=201)
async def create_new_thread(
    request: Request,
    runtime_db: Session = Depends(get_runtime_db),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Create a new conversation thread, closing the existing active one."""
    unlock_session = require_unlocked_session(request)
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
        asyncio.get_running_loop().create_task(
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
    unlock_session = require_unlocked_session(request)
    thread = runtime_db.get(RuntimeThread, thread_id)
    if thread is None or thread.user_id != unlock_session.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")

    dek = get_active_dek(unlock_session.user_id, "conversations")
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
    unlock_session = require_unlocked_session(request)
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
        asyncio.get_running_loop().create_task(
            on_thread_close(
                thread_id=thread_id,
                user_id=thread.user_id,
                soul_db_factory=soul_db_factory,
            )
        )

    return {"status": "closed", "threadId": thread_id}
