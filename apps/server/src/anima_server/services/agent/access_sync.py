"""Access metadata sync: PG memory_access_log -> SQLCipher memory_items."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from anima_server.models.runtime_memory import MemoryAccessLog

logger = logging.getLogger(__name__)


async def sync_access_metadata(
    *,
    user_id: int,
    runtime_db: Session,
    soul_db: Session | None,
    dry_run: bool = False,
) -> dict:
    """Aggregate PG access log -> SQLCipher memory_items.

    Crash-idempotent: snapshot unsynced, mark synced, apply delta, delete.
    """
    # 1. Aggregate unsynced rows
    rows = runtime_db.execute(
        select(
            MemoryAccessLog.memory_item_id,
            func.count(MemoryAccessLog.id).label("cnt"),
            func.max(MemoryAccessLog.accessed_at).label("last_access"),
        )
        .where(
            MemoryAccessLog.user_id == user_id,
            MemoryAccessLog.synced.is_(False),
        )
        .group_by(MemoryAccessLog.memory_item_id)
    ).all()

    if not rows:
        return {"items_synced": 0, "access_counts": {}}

    access_counts = {row.memory_item_id: row.cnt for row in rows}
    last_access = {row.memory_item_id: row.last_access for row in rows}

    # 2. Mark as synced (idempotent)
    runtime_db.execute(
        update(MemoryAccessLog)
        .where(
            MemoryAccessLog.user_id == user_id,
            MemoryAccessLog.synced.is_(False),
        )
        .values(synced=True)
    )
    runtime_db.flush()

    if dry_run or soul_db is None:
        return {"items_synced": len(access_counts), "access_counts": access_counts}

    # 3. Apply to SQLCipher
    from anima_server.models import MemoryItem

    for item_id, count in access_counts.items():
        item = soul_db.get(MemoryItem, item_id)
        if item is None:
            continue
        item.reference_count = (item.reference_count or 0) + count
        item.last_referenced_at = last_access[item_id]
    soul_db.flush()

    try:
        from anima_server.services.agent.heat_scoring import update_heat_on_access

        items = [soul_db.get(MemoryItem, iid) for iid in access_counts if soul_db.get(MemoryItem, iid)]
        if items:
            update_heat_on_access(soul_db, items)
    except Exception:
        pass

    soul_db.commit()

    # 4. Delete synced rows
    runtime_db.execute(
        delete(MemoryAccessLog).where(
            MemoryAccessLog.user_id == user_id,
            MemoryAccessLog.synced.is_(True),
        )
    )
    runtime_db.commit()

    return {"items_synced": len(access_counts), "access_counts": access_counts}
