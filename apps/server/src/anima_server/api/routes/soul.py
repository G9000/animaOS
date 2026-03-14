"""Soul API: user-authored personality directive for the AI.

The soul content is the user's description of who their AI is and how it should
behave. Stored in self_model_blocks with section="soul". On first read, migrates
any existing file-backed soul.md into the database transparently.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_user
from anima_server.db import get_db
from anima_server.models import SelfModelBlock

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/soul", tags=["soul"])

SOUL_SECTION = "soul"
SOUL_FILENAME = "soul.md"


class SoulResponse(BaseModel):
    content: str
    source: str = "database"


class SoulUpdateRequest(BaseModel):
    content: str = Field(min_length=1)


def _get_soul_block(db: Session, user_id: int) -> SelfModelBlock | None:
    from sqlalchemy import select

    return db.scalar(
        select(SelfModelBlock).where(
            SelfModelBlock.user_id == user_id,
            SelfModelBlock.section == SOUL_SECTION,
        )
    )


def _set_soul_block(db: Session, user_id: int, content: str) -> SelfModelBlock:
    from datetime import UTC, datetime

    existing = _get_soul_block(db, user_id)
    if existing is not None:
        existing.content = content
        existing.version += 1
        existing.updated_by = "user_edit"
        existing.updated_at = datetime.now(UTC)
        db.flush()
        return existing

    block = SelfModelBlock(
        user_id=user_id,
        section=SOUL_SECTION,
        content=content,
        version=1,
        updated_by="user_edit",
    )
    db.add(block)
    db.flush()
    return block


def _try_migrate_file(db: Session, user_id: int) -> str | None:
    """Attempt to migrate a file-backed soul.md into the database.

    Returns the migrated content, or None if no file exists.
    """
    try:
        from anima_server.services.storage import get_user_data_dir
        from anima_server.services.crypto import decrypt_text_with_dek
        from anima_server.services.data_crypto import require_dek_for_user

        path = get_user_data_dir(user_id) / SOUL_FILENAME
        if not path.exists():
            return None

        dek = require_dek_for_user(user_id)
        raw_content = path.read_text(encoding="utf-8")
        content = decrypt_text_with_dek(raw_content, dek)

        if content.strip():
            _set_soul_block(db, user_id, content)
            db.commit()
            # Remove the file after successful migration
            path.unlink(missing_ok=True)
            logger.info("Migrated soul.md to database for user %d", user_id)

        return content
    except Exception:  # noqa: BLE001
        logger.debug("Could not migrate soul.md for user %d", user_id)
        return None


@router.get("/{user_id}", response_model=SoulResponse)
async def get_soul(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> SoulResponse:
    require_unlocked_user(request, user_id)

    block = _get_soul_block(db, user_id)
    if block is not None:
        return SoulResponse(content=block.content, source="database")

    # Try migrating from file
    migrated = _try_migrate_file(db, user_id)
    if migrated is not None:
        return SoulResponse(content=migrated, source="migrated")

    return SoulResponse(content="", source="database")


@router.put("/{user_id}")
async def update_soul(
    user_id: int,
    payload: SoulUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    require_unlocked_user(request, user_id)
    _set_soul_block(db, user_id, payload.content)
    db.commit()
    return {"status": "updated"}
