"""Thread management endpoints."""

from __future__ import annotations

import logging
from typing import Never

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_session_async
from anima_server.config import settings
from anima_server.db import get_db, get_runtime_db
from anima_server.services.agent.compaction import estimate_message_tokens
from anima_server.services.corefs.conversation_authority import (
    CanonicalThreadView,
    canonical_messages_for_display,
    conversation_corefs_authority_active,
    get_canonical_thread,
    list_canonical_threads,
)
from anima_server.services.corefs.conversation_mutations import (
    ConversationMutationError,
    close_canonical_thread,
    create_canonical_thread,
    delete_canonical_thread,
)
from anima_server.services.corefs.logical import CoreFsMutationUnavailable
from anima_server.services.corefs.messages import ConversationFormatError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/threads", tags=["threads"])


def _canonical_thread_to_dict(
    view: CanonicalThreadView,
    *,
    user_id: int,
) -> dict[str, object]:
    document = view.document
    thread_id = document.legacy_thread_id or document.thread_id
    first_role = view.messages[0].role if view.messages else None
    initiated_by = "user" if first_role == "user" else ("agent" if first_role else None)
    return {
        "id": thread_id,
        "userId": user_id,
        "status": document.status,
        "title": document.title,
        "createdAt": document.created_at,
        "lastMessageAt": document.last_message_at,
        "closedAt": document.closed_at,
        "isArchived": document.is_archived,
        "initiatedBy": initiated_by,
        "corefsId": document.thread_id,
        "degradedRanges": [list(value) for value in view.degraded_ranges],
    }


def _raise_corefs_mutation_error(exc: Exception) -> Never:
    code = str(exc)
    status_code = (
        status.HTTP_503_SERVICE_UNAVAILABLE
        if code
        in {
            "corefs_native_mutation_unavailable",
            "corefs_native_mutation_result_invalid",
            "Native CoreFS conversation mutation result is invalid.",
        }
        else status.HTTP_409_CONFLICT
    )
    raise HTTPException(
        status_code=status_code,
        detail={"code": code or "corefs_conversation_mutation_failed"},
    ) from exc


def _create_canonical_thread_response(
    view: CanonicalThreadView,
    *,
    user_id: int,
) -> dict[str, object]:
    thread = _canonical_thread_to_dict(view, user_id=user_id)
    return {
        "threadId": thread["id"],
        "status": thread["status"],
        "thread": thread,
    }


@router.get("")
async def list_user_threads(
    request: Request,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, object]:
    """List all threads for the authenticated user, newest first."""
    unlock_session = await require_unlocked_session_async(request)
    if not conversation_corefs_authority_active(unlock_session):
        raise HTTPException(status_code=409, detail="Current CoreFS is unavailable.")
    return {
        "threads": [
            _canonical_thread_to_dict(view, user_id=unlock_session.user_id)
            for view in list_canonical_threads(session=unlock_session)
        ]
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
    if not conversation_corefs_authority_active(unlock_session):
        raise HTTPException(status_code=409, detail="Current CoreFS is unavailable.")
    try:
        thread = create_canonical_thread(session=unlock_session)
    except (
        ConversationFormatError,
        ConversationMutationError,
        CoreFsMutationUnavailable,
        PermissionError,
        ValueError,
    ) as exc:
        _raise_corefs_mutation_error(exc)
    return _create_canonical_thread_response(thread, user_id=user_id)


@router.get("/{thread_id}/messages")
async def get_thread_messages(
    thread_id: int,
    request: Request,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, object]:
    """Read authenticated canonical messages for the current owner."""
    unlock_session = await require_unlocked_session_async(request)
    if not conversation_corefs_authority_active(unlock_session):
        raise HTTPException(status_code=409, detail="Current CoreFS is unavailable.")
    view = get_canonical_thread(session=unlock_session, thread_id=thread_id)
    if view is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Thread not found",
        )
    return {
        "threadId": view.document.legacy_thread_id or view.document.thread_id,
        "messages": canonical_messages_for_display(view),
        "degradedRanges": [list(value) for value in view.degraded_ranges],
    }


@router.post("/{thread_id}/close")
async def close_thread_endpoint(
    thread_id: int,
    request: Request,
    db: Session = Depends(get_db),
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, object]:
    """Close a thread and trigger background consolidation."""
    unlock_session = await require_unlocked_session_async(request)
    if not conversation_corefs_authority_active(unlock_session):
        raise HTTPException(status_code=409, detail="Current CoreFS is unavailable.")
    try:
        thread, changed = close_canonical_thread(
            session=unlock_session,
            thread_id=thread_id,
        )
    except (
        ConversationFormatError,
        ConversationMutationError,
        CoreFsMutationUnavailable,
        PermissionError,
        ValueError,
    ) as exc:
        _raise_corefs_mutation_error(exc)
    if thread is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Thread not found",
        )
    return {
        "status": "closed" if changed else "already_closed",
        "threadId": thread.document.legacy_thread_id or thread.document.thread_id,
    }


@router.get("/{thread_id}/context-stats")
async def get_thread_context_stats(
    thread_id: int,
    request: Request,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, object]:
    """Return context window usage stats for a thread."""
    unlock_session = await require_unlocked_session_async(request)
    if not conversation_corefs_authority_active(unlock_session):
        raise HTTPException(status_code=409, detail="Current CoreFS is unavailable.")
    view = get_canonical_thread(session=unlock_session, thread_id=thread_id)
    if view is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Thread not found",
        )
    used_tokens = sum(
        estimate_message_tokens(
            content_text=message.content,
            content_json=None,
            tool_name=None,
        )
        for message in view.messages
    )
    budget = settings.agent_context_window_tokens
    return {
        "threadId": view.document.legacy_thread_id or view.document.thread_id,
        "usedTokens": used_tokens,
        "budgetTokens": budget,
        "triggerAtTokens": round(budget * settings.agent_compaction_trigger_ratio)
        if budget
        else None,
        "pct": round(used_tokens / budget * 100, 1) if budget else None,
        "compactionCount": 0,
        "messageCount": len(view.messages),
    }


@router.delete("/{thread_id}")
async def delete_thread_endpoint(
    thread_id: int,
    request: Request,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, object]:
    """Move a canonical thread into encrypted trash."""
    unlock_session = await require_unlocked_session_async(request)
    if not conversation_corefs_authority_active(unlock_session):
        raise HTTPException(status_code=409, detail="Current CoreFS is unavailable.")
    try:
        deleted = delete_canonical_thread(
            session=unlock_session,
            thread_id=thread_id,
        )
    except (
        ConversationFormatError,
        ConversationMutationError,
        CoreFsMutationUnavailable,
        PermissionError,
        ValueError,
    ) as exc:
        _raise_corefs_mutation_error(exc)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Thread not found",
        )
    return {
        "status": "deleted",
        "threadId": thread_id,
        "assetsDeleted": 0,
        "filesDeleted": 0,
    }
