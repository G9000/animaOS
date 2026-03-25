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

    from anima_server.services.agent.self_model import (
        ensure_self_model_exists,
        get_all_self_model_blocks,
    )

    ensure_self_model_exists(db, user_id=user_id)
    blocks = get_all_self_model_blocks(db, user_id=user_id)

    sections = {}
    for section_name, block in blocks.items():
        sections[section_name] = {
            "content": df(user_id, block.content, table="self_model_blocks", field="content"),
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

    from anima_server.services.agent.self_model import (
        ALL_SECTIONS,
        ensure_self_model_exists,
        get_self_model_block,
    )

    if section not in ALL_SECTIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid section: {section}. Valid: {', '.join(ALL_SECTIONS)}",
        )

    ensure_self_model_exists(db, user_id=user_id)
    block = get_self_model_block(db, user_id=user_id, section=section)
    if block is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Section not found")

    return SelfModelSectionResponse(
        section=block.section,
        content=df(user_id, block.content, table="self_model_blocks", field="content"),
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
        ALL_SECTIONS,
        append_growth_log_entry,
        ensure_self_model_exists,
        set_self_model_block,
    )

    if section not in ALL_SECTIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid section: {section}. Valid: {', '.join(ALL_SECTIONS)}",
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
        content=df(user_id, block.content, table="self_model_blocks", field="content"),
        version=block.version,
        updatedBy=block.updated_by,
        updatedAt=block.updated_at.isoformat() if block.updated_at else None,
    )


# --- Agent Profile Endpoints ---


class AgentProfileUpdateRequest(BaseModel):
    agentName: str | None = None
    relationship: str | None = None
    personaTemplate: str | None = None


@router.get("/{user_id}/agent-profile")
async def get_agent_profile(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Get the agent profile for this user."""
    require_unlocked_user(request, user_id)

    from anima_server.models import AgentProfile

    profile = db.query(AgentProfile).filter(AgentProfile.user_id == user_id).first()
    if profile is None:
        return {
            "agentName": "Anima",
            "relationship": "companion",
            "personaTemplate": "default",
            "setupComplete": False,
        }
    return {
        "agentName": profile.agent_name,
        "relationship": profile.relationship,
        "personaTemplate": "default",
        "setupComplete": profile.setup_complete,
    }


@router.patch("/{user_id}/agent-profile")
async def update_agent_profile(
    user_id: int,
    payload: AgentProfileUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Update the agent's profile — name, relationship, persona template."""
    require_unlocked_user(request, user_id)

    from anima_server.models import AgentProfile
    from anima_server.services.agent.self_model import get_self_model_block, set_self_model_block
    from anima_server.services.agent.system_prompt import render_origin_block, render_persona_seed

    profile = db.query(AgentProfile).filter(AgentProfile.user_id == user_id).first()
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")

    name_changed = False
    if payload.agentName is not None:
        profile.agent_name = payload.agentName.strip() or "Anima"
        name_changed = True

    relationship_changed = False
    if payload.relationship is not None:
        profile.relationship = payload.relationship.strip()
        relationship_changed = True

    profile.setup_complete = True

    # Regenerate soul/origin block if agent name changed
    if name_changed:
        origin_content = render_origin_block(
            agent_name=profile.agent_name,
            creator_name=profile.creator_name,
        )
        set_self_model_block(
            db,
            user_id=user_id,
            section="soul",
            content=origin_content,
            updated_by="agent_setup",
        )

    # Update human block with relationship
    if relationship_changed:
        human_block = get_self_model_block(db, user_id=user_id, section="human")
        if human_block:
            content = df(user_id, human_block.content, table="self_model_blocks", field="content")
            lines = content.split("\n")
            new_lines = [l for l in lines if not l.startswith("Relationship:")]
            if profile.relationship:
                new_lines.append(f"Relationship: {profile.relationship}")
            set_self_model_block(
                db,
                user_id=user_id,
                section="human",
                content="\n".join(new_lines),
                updated_by="agent_setup",
            )

    # Update persona if provided
    if payload.personaTemplate is not None:
        from anima_server.services.agent.system_prompt import PromptTemplateError

        try:
            persona_content = render_persona_seed(payload.personaTemplate, agent_name=profile.agent_name)
        except PromptTemplateError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        set_self_model_block(
            db,
            user_id=user_id,
            section="persona",
            content=persona_content,
            updated_by="agent_setup",
        )

    db.commit()

    return {
        "agentName": profile.agent_name,
        "relationship": profile.relationship,
        "setupComplete": True,
    }


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
            emotion_scores[s.emotion] = emotion_scores.get(s.emotion, 0) + s.confidence
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
                evidence=df(user_id, s.evidence, table="emotional_signals", field="evidence"),
                topic=df(user_id, s.topic, table="emotional_signals", field="topic"),
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
