"""Knowledge Graph API routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_user
from anima_server.db import get_db
from anima_server.models import KGEntity, KGRelation
from anima_server.services.agent.knowledge_graph import (
    graph_context_for_query,
    normalize_entity_name,
    search_graph,
)
from anima_server.services.data_crypto import df

router = APIRouter(prefix="/api/graph", tags=["graph"])


@router.get("/{user_id}/overview")
async def get_graph_overview(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Get knowledge graph statistics."""
    require_unlocked_user(request, user_id)

    entity_count = db.scalar(
        select(func.count(KGEntity.id)).where(KGEntity.user_id == user_id)
    ) or 0

    relation_count = db.scalar(
        select(func.count(KGRelation.id)).where(KGRelation.user_id == user_id)
    ) or 0

    # Get entity type distribution
    type_counts = {}
    for row in db.execute(
        select(KGEntity.entity_type, func.count(KGEntity.id))
        .where(KGEntity.user_id == user_id)
        .group_by(KGEntity.entity_type)
    ):
        type_counts[row[0]] = row[1]

    # Get most mentioned entities
    top_entities = [
        {
            "id": e.id,
            "name": e.name,
            "type": e.entity_type,
            "mentions": e.mentions,
        }
        for e in db.scalars(
            select(KGEntity)
            .where(KGEntity.user_id == user_id)
            .order_by(KGEntity.mentions.desc())
            .limit(10)
        )
    ]

    # Get relation type distribution
    relation_types = {}
    for row in db.execute(
        select(KGRelation.relation_type, func.count(KGRelation.id))
        .where(KGRelation.user_id == user_id)
        .group_by(KGRelation.relation_type)
    ):
        relation_types[row[0]] = row[1]

    return {
        "entityCount": entity_count,
        "relationCount": relation_count,
        "typeDistribution": type_counts,
        "relationTypeDistribution": relation_types,
        "topEntities": top_entities,
    }


@router.get("/{user_id}/entities")
async def list_entities(
    user_id: int,
    request: Request,
    type: str | None = Query(default=None),
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """List entities with optional filtering."""
    require_unlocked_user(request, user_id)

    query = select(KGEntity).where(KGEntity.user_id == user_id)

    if type:
        query = query.where(KGEntity.entity_type == type)

    if search:
        normalized = normalize_entity_name(search)
        query = query.where(
            KGEntity.name_normalized.contains(normalized)
            | KGEntity.name.ilike(f"%{search}%")
        )

    total = db.scalar(
        select(func.count(KGEntity.id)).where(KGEntity.user_id == user_id)
    ) or 0

    entities = list(
        db.scalars(
            query.order_by(KGEntity.mentions.desc(), KGEntity.name).limit(limit).offset(offset)
        ).all()
    )

    return {
        "total": total,
        "entities": [
            {
                "id": e.id,
                "name": e.name,
                "normalized": e.name_normalized,
                "type": e.entity_type,
                "description": df(user_id, e.description, table="kg_entities", field="description") if e.description else None,
                "mentions": e.mentions,
                "createdAt": e.created_at,
                "updatedAt": e.updated_at,
            }
            for e in entities
        ],
    }


@router.get("/{user_id}/entities/{entity_id}")
async def get_entity(
    user_id: int,
    entity_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Get a single entity with its relations."""
    require_unlocked_user(request, user_id)

    entity = db.get(KGEntity, entity_id)
    if entity is None or entity.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Entity not found",
        )

    # Get all relations for this entity
    outgoing = list(
        db.scalars(
            select(KGRelation).where(
                KGRelation.user_id == user_id,
                KGRelation.source_id == entity_id,
            )
        ).all()
    )
    incoming = list(
        db.scalars(
            select(KGRelation).where(
                KGRelation.user_id == user_id,
                KGRelation.destination_id == entity_id,
            )
        ).all()
    )

    # Build entity cache for related entities
    related_ids = set()
    for r in outgoing:
        related_ids.add(r.destination_id)
    for r in incoming:
        related_ids.add(r.source_id)

    entity_cache = {}
    if related_ids:
        for e in db.scalars(select(KGEntity).where(KGEntity.id.in_(related_ids))):
            entity_cache[e.id] = e

    return {
        "id": entity.id,
        "name": entity.name,
        "normalized": entity.name_normalized,
        "type": entity.entity_type,
        "description": df(user_id, entity.description, table="kg_entities", field="description") if entity.description else None,
        "mentions": entity.mentions,
        "createdAt": entity.created_at,
        "updatedAt": entity.updated_at,
        "outgoingRelations": [
            {
                "id": r.id,
                "type": r.relation_type,
                "mentions": r.mentions,
                "target": {
                    "id": r.destination_id,
                    "name": entity_cache.get(r.destination_id, type("obj", (), {"name": "?"})()).name,
                    "type": entity_cache.get(r.destination_id, type("obj", (), {"entity_type": "unknown"})()).entity_type,
                },
            }
            for r in outgoing
        ],
        "incomingRelations": [
            {
                "id": r.id,
                "type": r.relation_type,
                "mentions": r.mentions,
                "source": {
                    "id": r.source_id,
                    "name": entity_cache.get(r.source_id, type("obj", (), {"name": "?"})()).name,
                    "type": entity_cache.get(r.source_id, type("obj", (), {"entity_type": "unknown"})()).entity_type,
                },
            }
            for r in incoming
        ],
    }


@router.get("/{user_id}/relations")
async def list_relations(
    user_id: int,
    request: Request,
    entity_id: int | None = Query(default=None),
    type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """List relations with optional filtering."""
    require_unlocked_user(request, user_id)

    query = select(KGRelation).where(KGRelation.user_id == user_id)

    if entity_id:
        query = query.where(
            (KGRelation.source_id == entity_id) | (KGRelation.destination_id == entity_id)
        )

    if type:
        query = query.where(KGRelation.relation_type == type)

    relations = list(db.scalars(query.order_by(KGRelation.mentions.desc()).limit(limit)).all())

    # Build entity cache
    entity_ids = set()
    for r in relations:
        entity_ids.add(r.source_id)
        entity_ids.add(r.destination_id)

    entity_cache = {}
    if entity_ids:
        for e in db.scalars(select(KGEntity).where(KGEntity.id.in_(entity_ids))):
            entity_cache[e.id] = e

    return {
        "relations": [
            {
                "id": r.id,
                "type": r.relation_type,
                "mentions": r.mentions,
                "source": {
                    "id": r.source_id,
                    "name": entity_cache.get(r.source_id, type("obj", (), {"name": "?"})()).name,
                    "type": entity_cache.get(r.source_id, type("obj", (), {"entity_type": "unknown"})()).entity_type,
                },
                "target": {
                    "id": r.destination_id,
                    "name": entity_cache.get(r.destination_id, type("obj", (), {"name": "?"})()).name,
                    "type": entity_cache.get(r.destination_id, type("obj", (), {"entity_type": "unknown"})()).entity_type,
                },
            }
            for r in relations
        ],
    }


@router.get("/{user_id}/search")
async def search_graph_endpoint(
    user_id: int,
    request: Request,
    q: str = Query(min_length=1),
    max_depth: int = Query(default=2, ge=1, le=3),
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Search the knowledge graph from entity names."""
    require_unlocked_user(request, user_id)

    # Find matching entities
    normalized = normalize_entity_name(q)
    matching = list(
        db.scalars(
            select(KGEntity).where(
                KGEntity.user_id == user_id,
                KGEntity.name_normalized.contains(normalized)
                | KGEntity.name.ilike(f"%{q}%"),
            )
        ).all()
    )

    if not matching:
        return {"entities": [], "paths": []}

    entity_names = [e.name for e in matching]

    # Traverse graph
    paths = search_graph(
        db,
        user_id=user_id,
        entity_names=entity_names,
        max_depth=max_depth,
        limit=limit,
    )

    return {
        "entities": [
            {
                "id": e.id,
                "name": e.name,
                "type": e.entity_type,
                "mentions": e.mentions,
            }
            for e in matching
        ],
        "paths": paths,
    }


@router.get("/{user_id}/context")
async def get_graph_context(
    user_id: int,
    request: Request,
    q: str = Query(min_length=1),
    limit: int = Query(default=10, ge=1, le=20),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Get formatted graph context for a query."""
    require_unlocked_user(request, user_id)

    context_lines = graph_context_for_query(
        db,
        user_id=user_id,
        query=q,
        limit=limit,
    )

    return {
        "query": q,
        "context": context_lines,
        "count": len(context_lines),
    }
