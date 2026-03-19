"""API routes for intentional forgetting — F7."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_user
from anima_server.db import get_db
from anima_server.models import MemoryItem
from anima_server.services.agent.forgetting import forget_by_topic, forget_memory
from anima_server.services.data_crypto import df

router = APIRouter(prefix="/api/memories", tags=["forgetting"])


@router.delete(
    "/{user_id}/{memory_id}/forget",
    status_code=status.HTTP_200_OK,
)
async def forget_single_memory(
    user_id: int,
    memory_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Hard-delete a single memory with full cleanup."""
    require_unlocked_user(request, user_id)

    item = db.get(MemoryItem, memory_id)
    if item is None or item.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Memory item not found",
        )

    result = forget_memory(db, memory_id=memory_id, user_id=user_id)
    db.commit()

    return {
        "forgotten": True,
        "items_forgotten": result.items_forgotten,
        "derived_refs_affected": result.derived_refs_affected,
    }


@router.delete(
    "/{user_id}/forget",
    status_code=status.HTTP_200_OK,
)
async def forget_by_topic_endpoint(
    user_id: int,
    request: Request,
    topic: str = Query(min_length=1),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Find memories matching a topic and return them as candidates for confirmation.

    Does NOT delete. Returns candidate list for the user to review and confirm.
    """
    require_unlocked_user(request, user_id)

    candidates = forget_by_topic(db, topic=topic, user_id=user_id)

    return {
        "topic": topic,
        "candidate_count": len(candidates),
        "candidates": [
            {
                "id": item.id,
                "content": df(user_id, item.content, table="memory_items", field="content"),
                "category": item.category,
                "importance": item.importance,
            }
            for item in candidates
        ],
    }
