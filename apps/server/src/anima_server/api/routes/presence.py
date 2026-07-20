from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_user
from anima_server.db import get_db, get_runtime_db
from anima_server.models.runtime_consciousness import PendingInitiative
from anima_server.schemas.presence import (
    PendingInitiativeResponse,
    PendingInitiativesResponse,
    PresenceConfigResponse,
    PresenceConfigUpdateRequest,
)
from anima_server.services.agent.inner_life.delivery import (
    acknowledge_pending_initiative,
    list_and_mark_delivered,
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
        initiativeEnabled=values.initiative_enabled,
        quietHoursStart=values.quiet_hours_start,
        quietHoursEnd=values.quiet_hours_end,
    )


def _pending_initiative_response(row: PendingInitiative) -> PendingInitiativeResponse:
    return PendingInitiativeResponse(
        id=row.id,
        drive=row.drive,
        text=row.text,
        createdAt=row.created_at.isoformat() if row.created_at else "",
        delivered=row.delivered,
        acknowledged=row.acknowledged,
    )


@router.get("/{user_id}/initiatives", response_model=PendingInitiativesResponse)
def list_pending_initiatives(
    user_id: int,
    request: Request,
    runtime_db: Session = Depends(get_runtime_db),
) -> PendingInitiativesResponse:
    """Fetch every not-yet-acknowledged IL3 initiative for this user (the
    default pollable delivery channel — see ``inner_life/delivery.py``)."""
    require_unlocked_user(request, user_id)
    rows = list_and_mark_delivered(runtime_db, user_id=user_id)
    return PendingInitiativesResponse(
        userId=user_id,
        initiatives=[_pending_initiative_response(row) for row in rows],
    )


@router.post(
    "/{user_id}/initiatives/{initiative_id}/ack",
    response_model=PendingInitiativeResponse,
)
def ack_pending_initiative(
    user_id: int,
    initiative_id: int,
    request: Request,
    runtime_db: Session = Depends(get_runtime_db),
    db: Session = Depends(get_db),
) -> PendingInitiativeResponse:
    """Acknowledge one pending initiative — also marks the soul-store
    provenance row ``answered`` (feeds the gate chain's cooldown backoff)."""
    require_unlocked_user(request, user_id)
    row = acknowledge_pending_initiative(
        runtime_db, soul_db=db, user_id=user_id, pending_id=initiative_id
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pending initiative not found.",
        )
    db.commit()
    return _pending_initiative_response(row)
