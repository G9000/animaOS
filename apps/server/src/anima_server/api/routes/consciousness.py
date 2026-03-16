"""Consciousness API: view and edit the AI's self-model, emotional state, and intentions."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_user
from anima_server.db import get_db
from anima_server.services.data_crypto import df

router = APIRouter(prefix="/api/consciousness", tags=["consciousness"])


class SelfModelSectionResponse(BaseModel):
    section: str
    content: str
    version: int
    updatedBy: str
    updatedAt: str | None = None


class SelfModelUpdateRequest(BaseModel):
    content: str


class EmotionalSignalResponse(BaseModel):
    emotion: str
    confidence: float
    trajectory: str
    evidenceType: str
    evidence: str
    topic: str
    createdAt: str | None = None


class EmotionalContextResponse(BaseModel):
    dominantEmotion: str | None = None
    recentSignals: list[EmotionalSignalResponse]
    synthesizedContext: str


# --- Self-Model Endpoints ---


@router.get("/{user_id}/self-model")
async def get_full_self_model(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Get the complete self-model for this user — all sections."""
    require_unlocked_user(request, user_id)

    from anima_server.services.agent.self_model import ensure_self_model_exists, get_all_self_model_blocks

    ensure_self_model_exists(db, user_id=user_id)
    blocks = get_all_self_model_blocks(db, user_id=user_id)

    sections = {}
    for section_name, block in blocks.items():
        sections[section_name] = {
            "content": df(user_id, block.content),
            "version": block.version,
            "updatedBy": block.updated_by,
            "updatedAt": block.updated_at.isoformat() if block.updated_at else None,
        }

    return {"userId": user_id, "sections": sections}


@router.get("/{user_id}/self-model/{section}")
async def get_self_model_section(
    user_id: int,
    section: str,
    request: Request,
    db: Session = Depends(get_db),
) -> SelfModelSectionResponse:
    """Get a single self-model section."""
    require_unlocked_user(request, user_id)

    from anima_server.services.agent.self_model import SECTIONS, ensure_self_model_exists, get_self_model_block

    if section not in SECTIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid section: {section}. Valid: {', '.join(SECTIONS)}",
        )

    ensure_self_model_exists(db, user_id=user_id)
    block = get_self_model_block(db, user_id=user_id, section=section)
    if block is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Section not found")

    return SelfModelSectionResponse(
        section=block.section,
        content=df(user_id, block.content),
        version=block.version,
        updatedBy=block.updated_by,
        updatedAt=block.updated_at.isoformat() if block.updated_at else None,
    )


@router.put("/{user_id}/self-model/{section}")
async def update_self_model_section(
    user_id: int,
    section: str,
    payload: SelfModelUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> SelfModelSectionResponse:
    """User edits a self-model section. Treated as highest-confidence evidence."""
    require_unlocked_user(request, user_id)

    from anima_server.services.agent.self_model import (
        SECTIONS,
        append_growth_log_entry,
        ensure_self_model_exists,
        set_self_model_block,
    )

    if section not in SECTIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid section: {section}. Valid: {', '.join(SECTIONS)}",
        )

    ensure_self_model_exists(db, user_id=user_id)
    block = set_self_model_block(
        db,
        user_id=user_id,
        section=section,
        content=payload.content,
        updated_by="user_edit",
    )

    # Log the user edit in the growth log
    if section != "growth_log":
        append_growth_log_entry(
            db,
            user_id=user_id,
            entry=f"User manually edited the '{section}' section",
        )

    db.commit()

    return SelfModelSectionResponse(
        section=block.section,
        content=df(user_id, block.content),
        version=block.version,
        updatedBy=block.updated_by,
        updatedAt=block.updated_at.isoformat() if block.updated_at else None,
    )


# --- Emotional State Endpoints ---


@router.get("/{user_id}/emotions")
async def get_emotional_state(
    user_id: int,
    request: Request,
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> EmotionalContextResponse:
    """Get the AI's current emotional read of the user."""
    require_unlocked_user(request, user_id)

    from anima_server.services.agent.emotional_intelligence import (
        get_recent_signals,
        synthesize_emotional_context,
    )

    signals = get_recent_signals(db, user_id=user_id, limit=limit)
    context = synthesize_emotional_context(db, user_id=user_id)

    # Determine dominant
    dominant = None
    if signals:
        emotion_scores: dict[str, float] = {}
        for s in signals[:5]:
            emotion_scores[s.emotion] = emotion_scores.get(
                s.emotion, 0) + s.confidence
        if emotion_scores:
            dominant = max(emotion_scores, key=emotion_scores.get)

    return EmotionalContextResponse(
        dominantEmotion=dominant,
        recentSignals=[
            EmotionalSignalResponse(
                emotion=s.emotion,
                confidence=s.confidence,
                trajectory=s.trajectory,
                evidenceType=s.evidence_type,
                evidence=df(user_id, s.evidence),
                topic=df(user_id, s.topic),
                createdAt=s.created_at.isoformat() if s.created_at else None,
            )
            for s in signals
        ],
        synthesizedContext=context,
    )


# --- Intentions Endpoints ---


@router.get("/{user_id}/intentions")
async def get_intentions(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Get the AI's current intentions and behavioral rules."""
    require_unlocked_user(request, user_id)

    from anima_server.services.agent.intentions import get_intentions_text

    content = get_intentions_text(db, user_id=user_id)
    return {"content": content}
