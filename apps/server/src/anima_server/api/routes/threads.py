"""Thread management endpoints."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_session
from anima_server.db import get_runtime_db
from anima_server.models.runtime import RuntimeThread
from anima_server.services.agent.eager_consolidation import on_thread_close
from anima_server.services.agent.persistence import close_thread

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/threads", tags=["threads"])


@router.post("/{thread_id}/close")
async def close_thread_endpoint(
    thread_id: int,
    request: Request,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, object]:
    """Close a thread and trigger background consolidation."""
    unlock_session = require_unlocked_session(request)
    thread = runtime_db.get(RuntimeThread, thread_id)
    if thread is None or thread.user_id != unlock_session.user_id:
        raise HTTPException(status_code=404, detail="Thread not found")

    if thread.status == "closed":
        return {"status": "already_closed", "thread_id": thread_id}

    changed = close_thread(runtime_db, thread_id=thread_id)
    runtime_db.commit()

    if changed:
        asyncio.get_running_loop().create_task(
            on_thread_close(
                thread_id=thread_id,
                user_id=thread.user_id,
            )
        )

    return {"status": "closed", "thread_id": thread_id}
