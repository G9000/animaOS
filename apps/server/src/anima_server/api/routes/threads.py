"""Thread management endpoints."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_session
from anima_server.config import settings
from anima_server.db import get_db, get_runtime_db
from anima_server.db.session import build_session_factory_for_db
from anima_server.models.runtime import RuntimeThread
from anima_server.services.agent.eager_consolidation import on_thread_close
from anima_server.services.agent.persistence import close_thread, create_thread, list_threads
from anima_server.services.agent.thread_manager import get_thread_messages_for_display
from anima_server.services.data_crypto import get_active_dek

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/threads", tags=["threads"])


@router.get("")
async def list_user_threads(
    request: Request,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, object]:
    """List all threads for the authenticated user, newest first."""
    unlock_session = require_unlocked_session(request)
    threads = list_threads(runtime_db, user_id=unlock_session.user_id)
    return {
        "threads": [
            {
                "id": t.id,
                "title": t.title,
                "status": t.status,
                "isArchived": t.is_archived,
                "lastMessageAt": t.last_message_at.isoformat() if t.last_message_at else None,
                "createdAt": t.created_at.isoformat() if t.created_at else None,
            }
            for t in threads
        ]
    }


@router.post("")
async def create_new_thread(
    request: Request,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, object]:
    """Create a new conversation thread."""
    unlock_session = require_unlocked_session(request)
    thread = create_thread(runtime_db, user_id=unlock_session.user_id)
    runtime_db.commit()
    return {"threadId": thread.id, "status": thread.status}


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
        raise HTTPException(status_code=404, detail="Thread not found")

    dek = get_active_dek(unlock_session.user_id, "conversations")
    transcripts_dir: Path = settings.data_dir / "transcripts"

    messages = get_thread_messages_for_display(
        runtime_db,
        thread=thread,
        user_id=unlock_session.user_id,
        transcripts_dir=transcripts_dir,
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
        raise HTTPException(status_code=404, detail="Thread not found")

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
