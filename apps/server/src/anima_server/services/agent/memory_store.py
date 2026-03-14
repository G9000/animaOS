from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models import MemoryDailyLog, MemoryItem


def add_memory_item(
    db: Session,
    *,
    user_id: int,
    content: str,
    category: str,
    importance: int = 3,
    source: str = "extraction",
) -> MemoryItem | None:
    existing = get_memory_items(db, user_id=user_id, category=category)
    for item in existing:
        if _is_duplicate(item.content, content):
            return None

    memory_item = MemoryItem(
        user_id=user_id,
        content=content,
        category=category,
        importance=importance,
        source=source,
    )
    db.add(memory_item)
    db.flush()
    return memory_item


def get_memory_items(
    db: Session,
    *,
    user_id: int,
    category: str | None = None,
    limit: int = 50,
    active_only: bool = True,
) -> list[MemoryItem]:
    query = select(MemoryItem).where(MemoryItem.user_id == user_id)
    if category is not None:
        query = query.where(MemoryItem.category == category)
    if active_only:
        query = query.where(MemoryItem.superseded_by.is_(None))
    query = query.order_by(MemoryItem.created_at.desc()).limit(limit)
    return list(db.scalars(query).all())


def supersede_memory_item(
    db: Session,
    *,
    old_item_id: int,
    new_content: str,
    importance: int | None = None,
) -> MemoryItem:
    old_item = db.get(MemoryItem, old_item_id)
    if old_item is None:
        raise ValueError(f"Memory item {old_item_id} not found")

    new_item = MemoryItem(
        user_id=old_item.user_id,
        content=new_content,
        category=old_item.category,
        importance=importance if importance is not None else old_item.importance,
        source=old_item.source,
    )
    db.add(new_item)
    db.flush()

    old_item.superseded_by = new_item.id
    old_item.updated_at = datetime.now(UTC)
    db.add(old_item)
    db.flush()
    return new_item


def add_daily_log(
    db: Session,
    *,
    user_id: int,
    user_message: str,
    assistant_response: str,
) -> MemoryDailyLog:
    log = MemoryDailyLog(
        user_id=user_id,
        date=datetime.now(UTC).date().isoformat(),
        user_message=user_message,
        assistant_response=assistant_response,
    )
    db.add(log)
    db.flush()
    return log


def get_current_focus(db: Session, *, user_id: int) -> str | None:
    item = db.scalar(
        select(MemoryItem)
        .where(
            MemoryItem.user_id == user_id,
            MemoryItem.category == "focus",
            MemoryItem.superseded_by.is_(None),
        )
        .order_by(MemoryItem.created_at.desc())
        .limit(1)
    )
    if item is None:
        return None
    return item.content


def set_current_focus(
    db: Session,
    *,
    user_id: int,
    focus: str,
) -> MemoryItem:
    existing = db.scalar(
        select(MemoryItem)
        .where(
            MemoryItem.user_id == user_id,
            MemoryItem.category == "focus",
            MemoryItem.superseded_by.is_(None),
        )
        .order_by(MemoryItem.created_at.desc())
        .limit(1)
    )
    if existing is not None:
        return supersede_memory_item(
            db,
            old_item_id=existing.id,
            new_content=focus,
        )

    item = MemoryItem(
        user_id=user_id,
        content=focus,
        category="focus",
        importance=4,
        source="user",
    )
    db.add(item)
    db.flush()
    return item


def _is_duplicate(existing_content: str, new_content: str) -> bool:
    return existing_content.strip().lower() == new_content.strip().lower()
