from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_user
from anima_server.db import get_db
from anima_server.schemas.proactivity import (
    ProactivityConfigResponse,
    ProactivityConfigUpdateRequest,
)
from anima_server.services.proactivity_config import (
    ProactivityConfigValues,
    get_proactivity_config_values,
    update_proactivity_config,
)

router = APIRouter(prefix="/api/proactivity", tags=["proactivity"])


@router.get("/{user_id}", response_model=ProactivityConfigResponse)
def get_config(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> ProactivityConfigResponse:
    require_unlocked_user(request, user_id)
    return _serialize(get_proactivity_config_values(db, user_id))


@router.put("/{user_id}", response_model=ProactivityConfigResponse)
def put_config(
    user_id: int,
    payload: ProactivityConfigUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> ProactivityConfigResponse:
    require_unlocked_user(request, user_id)
    values = update_proactivity_config(
        db,
        user_id,
        payload.model_dump(exclude_unset=True),
    )
    db.commit()
    return _serialize(values)


def _serialize(values: ProactivityConfigValues) -> ProactivityConfigResponse:
    return ProactivityConfigResponse(
        userId=values.user_id,
        enabled=values.enabled,
        mainChatEnabled=values.main_chat_enabled,
        homeGreetingContextEnabled=values.home_greeting_context_enabled,
        taskNudgesEnabled=values.task_nudges_enabled,
        memoryNudgesEnabled=values.memory_nudges_enabled,
        checkInNudgesEnabled=values.checkin_nudges_enabled,
        customInstruction=values.custom_instruction,
    )
