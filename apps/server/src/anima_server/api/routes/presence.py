from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_user
from anima_server.db import get_db
from anima_server.schemas.presence import (
    PresenceConfigResponse,
    PresenceConfigUpdateRequest,
)
from anima_server.services.presence_config import (
    PresenceConfigValues,
    get_presence_config_values,
    update_presence_config,
)

router = APIRouter(prefix="/api/presence", tags=["presence"])


@router.get("/{user_id}", response_model=PresenceConfigResponse)
def get_config(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> PresenceConfigResponse:
    require_unlocked_user(request, user_id)
    return _serialize(get_presence_config_values(db, user_id))


@router.put("/{user_id}", response_model=PresenceConfigResponse)
def put_config(
    user_id: int,
    payload: PresenceConfigUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> PresenceConfigResponse:
    require_unlocked_user(request, user_id)
    values = update_presence_config(
        db,
        user_id,
        payload.model_dump(exclude_unset=True),
    )
    db.commit()
    return _serialize(values)


def _serialize(values: PresenceConfigValues) -> PresenceConfigResponse:
    return PresenceConfigResponse(
        userId=values.user_id,
        enabled=values.enabled,
        mainChatEnabled=values.main_chat_enabled,
        homeGreetingContextEnabled=values.home_greeting_context_enabled,
        taskNudgesEnabled=values.task_nudges_enabled,
        memoryNudgesEnabled=values.memory_nudges_enabled,
        checkInNudgesEnabled=values.checkin_nudges_enabled,
        customInstruction=values.custom_instruction,
    )
