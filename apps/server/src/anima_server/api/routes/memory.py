from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_user
from anima_server.db import get_db
from anima_server.models import MemoryEpisode, MemoryItem
from anima_server.schemas.memory import (
    MemoryEpisodeResponse,
    MemoryItemCreateRequest,
    MemoryItemResponse,
    MemoryItemUpdateRequest,
    MemoryOverview,
)
from anima_server.services.agent.memory_store import (
    get_current_focus,
    get_memory_items,
    supersede_memory_item,
)
from anima_server.services.data_crypto import df

router = APIRouter(prefix="/api/memory", tags=["memory"])


def _item_to_response(item: MemoryItem, user_id: int = 0) -> MemoryItemResponse:
    return MemoryItemResponse(
        id=item.id,
        content=df(user_id, item.content),
        category=item.category,
        importance=item.importance,
        source=item.source,
        isSuperseded=item.superseded_by is not None,
        createdAt=item.created_at,
        updatedAt=item.updated_at,
    )


@router.get("/{user_id}", response_model=MemoryOverview)
async def get_memory_overview(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> MemoryOverview:
    require_unlocked_user(request, user_id)

    counts: dict[str, int] = {}
    for category in ("fact", "preference", "goal", "relationship"):
        count = db.scalar(
            select(func.count(MemoryItem.id)).where(
                MemoryItem.user_id == user_id,
                MemoryItem.category == category,
                MemoryItem.superseded_by.is_(None),
            )
        ) or 0
        counts[category] = count

    total = sum(counts.values())
    focus = get_current_focus(db, user_id=user_id)
    episode_count = db.scalar(
        select(func.count(MemoryEpisode.id)).where(
            MemoryEpisode.user_id == user_id,
        )
    ) or 0

    return MemoryOverview(
        totalItems=total,
        factCount=counts["fact"],
        preferenceCount=counts["preference"],
        goalCount=counts["goal"],
        relationshipCount=counts["relationship"],
        currentFocus=focus,
        episodeCount=episode_count,
    )


@router.get("/{user_id}/items", response_model=list[MemoryItemResponse])
async def list_memory_items(
    user_id: int,
    request: Request,
    category: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[MemoryItemResponse]:
    require_unlocked_user(request, user_id)
    items = get_memory_items(
        db,
        user_id=user_id,
        category=category,
        limit=limit,
    )
    return [_item_to_response(item, user_id) for item in items]


@router.post(
    "/{user_id}/items",
    response_model=MemoryItemResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_memory_item(
    user_id: int,
    payload: MemoryItemCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> MemoryItemResponse:
    require_unlocked_user(request, user_id)

    if payload.category not in ("fact", "preference", "goal", "relationship"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid category: {payload.category}",
        )

    from anima_server.services.agent.memory_store import add_memory_item

    item = add_memory_item(
        db,
        user_id=user_id,
        content=payload.content,
        category=payload.category,
        importance=payload.importance,
        source="user",
    )
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Duplicate memory item",
        )
    db.commit()
    return _item_to_response(item, user_id)


@router.put("/{user_id}/items/{item_id}", response_model=MemoryItemResponse)
async def update_memory_item(
    user_id: int,
    item_id: int,
    payload: MemoryItemUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> MemoryItemResponse:
    require_unlocked_user(request, user_id)

    existing = db.get(MemoryItem, item_id)
    if existing is None or existing.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Memory item not found",
        )
    if existing.superseded_by is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot edit superseded item",
        )

    if payload.content is not None and payload.content != existing.content:
        new_item = supersede_memory_item(
            db,
            old_item_id=existing.id,
            new_content=payload.content,
            importance=payload.importance if payload.importance is not None else existing.importance,
        )
        db.commit()
        return _item_to_response(new_item, user_id)

    if payload.importance is not None:
        existing.importance = payload.importance
    if payload.category is not None:
        existing.category = payload.category
    db.commit()
    return _item_to_response(existing, user_id)


@router.delete(
    "/{user_id}/items/{item_id}",
    status_code=status.HTTP_200_OK,
)
async def delete_memory_item(
    user_id: int,
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    require_unlocked_user(request, user_id)

    existing = db.get(MemoryItem, item_id)
    if existing is None or existing.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Memory item not found",
        )
    db.delete(existing)
    db.commit()
    _remove_from_vector_store(user_id, item_id)
    return {"deleted": True}


def _remove_from_vector_store(user_id: int, item_id: int) -> None:
    """Best-effort removal from ChromaDB. Silently skips if vector store is not initialized."""
    try:
        import anima_server.services.agent.vector_store as vs

        if vs._client is not None:
            vs.delete_memory(user_id, item_id=item_id)
    except Exception:  # noqa: BLE001
        pass


@router.get("/{user_id}/search")
async def search_memory(
    user_id: int,
    request: Request,
    q: str = Query(min_length=1),
    mode: str = Query(default="auto"),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    require_unlocked_user(request, user_id)

    # Try semantic search first if mode allows
    semantic_results: list[dict[str, object]] = []
    if mode in ("auto", "semantic"):
        try:
            from anima_server.services.agent.embeddings import semantic_search

            matches = await semantic_search(
                db, user_id=user_id, query=q, limit=20,
            )
            semantic_results = [
                {
                    "type": "item",
                    "id": item.id,
                    "content": df(user_id, item.content),
                    "category": item.category,
                    "importance": item.importance,
                    "similarity": round(score, 3),
                }
                for item, score in matches
            ]
        except Exception:  # noqa: BLE001
            pass

    # Keyword search — decrypt content in Python since encrypted fields
    # cannot be matched with SQL ILIKE.
    q_lower = q.lower()
    all_items = list(
        db.scalars(
            select(MemoryItem)
            .where(
                MemoryItem.user_id == user_id,
                MemoryItem.superseded_by.is_(None),
            )
            .order_by(MemoryItem.importance.desc(), MemoryItem.created_at.desc())
        ).all()
    )
    items = [
        item for item in all_items
        if q_lower in df(user_id, item.content).lower()
    ][:20]

    all_episodes = list(
        db.scalars(
            select(MemoryEpisode)
            .where(MemoryEpisode.user_id == user_id)
            .order_by(MemoryEpisode.created_at.desc())
        ).all()
    )
    episodes = [
        ep for ep in all_episodes
        if q_lower in df(user_id, ep.summary).lower()
    ][:10]

    keyword_results: list[dict[str, object]] = [
        {
            "type": "item",
            "id": item.id,
            "content": df(user_id, item.content),
            "category": item.category,
            "importance": item.importance,
        }
        for item in items
    ] + [
        {
            "type": "episode",
            "id": ep.id,
            "content": df(user_id, ep.summary),
            "category": "episode",
            "importance": ep.significance_score,
        }
        for ep in episodes
    ]

    # Merge: semantic first, then keyword results not already included
    if semantic_results:
        seen_ids = {(r["type"], r["id"]) for r in semantic_results}
        merged = list(semantic_results)
        for kr in keyword_results:
            if (kr["type"], kr["id"]) not in seen_ids:
                merged.append(kr)
        results = merged
    else:
        results = keyword_results

    return {"count": len(results), "results": results}


@router.get("/{user_id}/episodes", response_model=list[MemoryEpisodeResponse])
async def list_episodes(
    user_id: int,
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[MemoryEpisodeResponse]:
    require_unlocked_user(request, user_id)
    episodes = list(
        db.scalars(
            select(MemoryEpisode)
            .where(MemoryEpisode.user_id == user_id)
            .order_by(MemoryEpisode.created_at.desc())
            .limit(limit)
        ).all()
    )
    return [
        MemoryEpisodeResponse(
            id=ep.id,
            date=ep.date,
            time=ep.time,
            summary=df(user_id, ep.summary),
            topics=ep.topics_json or [],
            emotionalArc=df(user_id, ep.emotional_arc) if ep.emotional_arc else None,
            significanceScore=ep.significance_score,
            turnCount=ep.turn_count,
            createdAt=ep.created_at,
        )
        for ep in episodes
    ]
