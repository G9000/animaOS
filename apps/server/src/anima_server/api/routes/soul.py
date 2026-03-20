"""User Directive API: user-authored customisation instructions for the AI.

The user directive is the user's description of how they want their AI to
behave with them. Stored in self_model_blocks with section="user_directive".
The API path remains /api/soul for backward compatibility with existing clients.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_user
from anima_server.db import get_db
from anima_server.models import SelfModelBlock
from anima_server.services.data_crypto import df, ef
from anima_server.services.storage import get_user_data_dir

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/soul", tags=["user-directive"])

USER_DIRECTIVE_SECTION = "user_directive"


class UserDirectiveResponse(BaseModel):
    content: str
    source: str = "database"


class UserDirectiveUpdateRequest(BaseModel):
    content: str = Field(min_length=1)


def _get_user_directive_block(db: Session, user_id: int) -> SelfModelBlock | None:
    from sqlalchemy import select

    return db.scalar(
        select(SelfModelBlock).where(
            SelfModelBlock.user_id == user_id,
            SelfModelBlock.section == USER_DIRECTIVE_SECTION,
        )
    )


def _set_user_directive_block(db: Session, user_id: int, content: str) -> SelfModelBlock:
    from datetime import UTC, datetime

    existing = _get_user_directive_block(db, user_id)
    encrypted_content = ef(
        user_id, content, table="self_model_blocks", field="content"
    )
    if existing is not None:
        existing.content = encrypted_content
        existing.version += 1
        existing.updated_by = "user_edit"
        existing.updated_at = datetime.now(UTC)
        db.flush()
        return existing

    block = SelfModelBlock(
        user_id=user_id,
        section=USER_DIRECTIVE_SECTION,
        content=encrypted_content,
        version=1,
        updated_by="user_edit",
    )
    db.add(block)
    db.flush()
    return block


@router.get("/{user_id}", response_model=UserDirectiveResponse)
async def get_user_directive(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> UserDirectiveResponse:
    require_unlocked_user(request, user_id)

    block = _get_user_directive_block(db, user_id)
    if block is not None:
        content = df(user_id, block.content, table="self_model_blocks", field="content")
        return UserDirectiveResponse(content=content, source="database")

    legacy_path = get_user_data_dir(user_id) / "soul.md"
    if legacy_path.is_file():
        content = legacy_path.read_text(encoding="utf-8")
        _set_user_directive_block(db, user_id, content)
        db.commit()
        legacy_path.unlink(missing_ok=True)
        return UserDirectiveResponse(content=content, source="database")

    return UserDirectiveResponse(content="", source="database")


@router.put("/{user_id}")
async def update_user_directive(
    user_id: int,
    payload: UserDirectiveUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    require_unlocked_user(request, user_id)
    _set_user_directive_block(db, user_id, payload.content)
    db.commit()
    return {"status": "updated"}
