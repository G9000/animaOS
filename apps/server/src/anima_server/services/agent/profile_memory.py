from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from anima_server.models import MemoryItem
from anima_server.services.agent.memory_store import (
    add_memory_item,
    add_tags_to_item,
    get_items_by_tags,
    supersede_memory_item,
)

AGENT_NAME_MEMORY_TAG = "agent_profile:name"
AGENT_RELATIONSHIP_MEMORY_TAG = "agent_profile:relationship"
AGENT_PROFILE_MEMORY_TAG = "agent_profile"


def _record_profile_memory(
    db: Session,
    *,
    user_id: int,
    old_value: str | None,
    new_value: str,
    fallback_value: str,
    content_prefix: str,
    category: str,
    importance: int,
    field_name: str,
    field_tag: str,
) -> MemoryItem | None:
    clean_old_value = (old_value or "").strip()
    clean_new_value = new_value.strip() or fallback_value
    if clean_old_value == clean_new_value:
        return None

    content = f"{content_prefix} {clean_new_value}"
    tags = [AGENT_PROFILE_MEMORY_TAG, field_tag]
    active_profile_items = get_items_by_tags(
        db,
        user_id=user_id,
        tags=[field_tag],
        match_mode="all",
        limit=20,
    )

    if active_profile_items:
        primary_old_item = active_profile_items[0]
        new_item = supersede_memory_item(
            db,
            old_item_id=primary_old_item.id,
            new_content=content,
            importance=importance,
            evidence_text=content,
            evidence_source_kind="explicit_update",
            evidence_metadata={
                "memory_source": "agent_profile",
                "field": field_name,
            },
        )
        new_item.category = category
        new_item.source = "user"
        add_tags_to_item(db, item_id=new_item.id, user_id=user_id, tags=tags)

        for old_item in active_profile_items[1:]:
            old_item.superseded_by = new_item.id
            old_item.updated_at = datetime.now(UTC)
            db.add(old_item)
        db.flush()
        return new_item

    return add_memory_item(
        db,
        user_id=user_id,
        content=content,
        category=category,
        importance=importance,
        source="user",
        tags=tags,
    )


def record_agent_name_memory(
    db: Session,
    *,
    user_id: int,
    old_name: str | None,
    new_name: str,
) -> MemoryItem | None:
    """Record the agent's current name as a superseding profile memory."""
    return _record_profile_memory(
        db,
        user_id=user_id,
        old_value=old_name,
        new_value=new_name,
        fallback_value="Anima",
        content_prefix="Agent name is",
        category="fact",
        importance=5,
        field_name="agent_name",
        field_tag=AGENT_NAME_MEMORY_TAG,
    )


def record_agent_relationship_memory(
    db: Session,
    *,
    user_id: int,
    old_relationship: str | None,
    new_relationship: str,
) -> MemoryItem | None:
    """Record the agent's current relationship as a superseding profile memory."""
    return _record_profile_memory(
        db,
        user_id=user_id,
        old_value=old_relationship,
        new_value=new_relationship,
        fallback_value="not set",
        content_prefix="Agent relationship is",
        category="relationship",
        importance=4,
        field_name="relationship",
        field_tag=AGENT_RELATIONSHIP_MEMORY_TAG,
    )
