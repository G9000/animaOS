from __future__ import annotations

import logging

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
from anima_server.services.corefs.logical import CoreFsMutationUnavailable
from anima_server.services.corefs.preferences import (
    PortablePreferenceError,
    portable_preference_corefs_authority_active,
    portable_preference_lock,
    read_canonical_presence_values,
    update_canonical_presence_preferences,
)
from anima_server.services.corefs.writing_source import prepare_writing_source_catalog
from anima_server.services.presence_config import (
    PresenceConfigValues,
    apply_presence_config_updates,
    get_presence_config_values,
    presence_consent_lock,
    update_presence_config,
)

router = APIRouter(prefix="/api/presence", tags=["presence"])
logger = logging.getLogger(__name__)


@router.get("/{user_id}", response_model=PresenceConfigResponse)
def get_config(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> PresenceConfigResponse:
    session = require_unlocked_user(request, user_id)
    try:
        values = (
            read_canonical_presence_values(session=session)
            if portable_preference_corefs_authority_active(session)
            else get_presence_config_values(db, user_id)
        )
    except PortablePreferenceError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _serialize(values)


@router.put("/{user_id}", response_model=PresenceConfigResponse)
def put_config(
    user_id: int,
    payload: PresenceConfigUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> PresenceConfigResponse:
    session = require_unlocked_user(request, user_id)
    # Hold the per-user consent lock through the COMMIT: initiative delivery
    # holds the same lock from its consent check through its delivered side
    # effect, so an opt-out can never commit inside a poll's decision window
    # (PR #123 review, P1 — see presence_consent_lock).
    with portable_preference_lock(user_id), presence_consent_lock(user_id):
        if portable_preference_corefs_authority_active(session):
            try:
                current = read_canonical_presence_values(session=session)
                values = update_canonical_presence_preferences(
                    session=session,
                    values=apply_presence_config_updates(
                        current,
                        payload.model_dump(exclude_unset=True),
                    ),
                )
            except (CoreFsMutationUnavailable, PortablePreferenceError, ValueError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": str(exc)},
                ) from exc
            return _serialize(values)
        values = update_presence_config(
            db,
            user_id,
            payload.model_dump(exclude_unset=True),
        )
        db.commit()
        try:
            prepare_writing_source_catalog(session=session, db=db)
        except Exception as exc:
            logger.exception("Encrypted presence preference shadow validation failed")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "corefs_preference_shadow_validation_failed",
                    "message": "Presence was saved, but its encrypted preference shadow needs retry.",
                },
            ) from exc
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
        dreamSharing=values.dream_sharing,
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
    db: Session = Depends(get_db),
) -> PendingInitiativesResponse:
    """Fetch every not-yet-acknowledged IL3 initiative for this user (the
    default pollable delivery channel — see ``inner_life/delivery.py``). The
    poll is the first proof of delivery, so it also best-effort reconciles the
    soul-store ``InitiativeLog.delivered`` flag (see the two-phase-commit note
    in ``tick_initiative_for_user``)."""
    require_unlocked_user(request, user_id)
    rows = list_and_mark_delivered(runtime_db, user_id=user_id, soul_db=db)
    db.commit()
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
    # Build the response before committing so an expire-on-commit session can't
    # invalidate `row`'s attributes out from under us.
    response = _pending_initiative_response(row)
    # Two-store ordering: commit the RUNTIME ack (the durable proof the user
    # acknowledged) BEFORE the soul reconciliation. `acknowledge_pending_initiative`
    # only flushed the runtime update; `get_runtime_db` would otherwise commit it
    # at dependency teardown, i.e. AFTER this handler's soul `db.commit()`. If
    # that late runtime commit then failed, the soul log would durably claim
    # `answered`/`delivered` while `PendingInitiative.acknowledged` rolled back —
    # over-claiming an ack and dropping the unanswered-backoff for a row still
    # being served. Committing runtime first makes a soul-side failure the safe
    # direction (soul under-claims; the pending row is already acked).
    runtime_db.commit()
    db.commit()
    return response
