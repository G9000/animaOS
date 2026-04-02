"""Consciousness API: view and edit the AI's self-model, emotional state, and intentions."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_user
from anima_server.db import get_db
from anima_server.services.data_crypto import df

router = APIRouter(prefix="/api/consciousness", tags=["consciousness"])


def _get_optional_runtime_db():
    """Yield a runtime DB session, or None if runtime is not initialized."""
    try:
        from anima_server.db.runtime import get_runtime_session_factory

        factory = get_runtime_session_factory()
    except RuntimeError:
        yield None
        return

    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


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


def _section_dict(
    *,
    content: str,
    version: int,
    updated_by: str,
    updated_at,
) -> dict[str, object]:
    return {
        "content": content,
        "version": version,
        "updatedBy": updated_by,
        "updatedAt": updated_at.isoformat() if updated_at else None,
    }


def _section_response(
    *,
    section: str,
    content: str,
    version: int,
    updated_by: str,
    updated_at,
) -> SelfModelSectionResponse:
    return SelfModelSectionResponse(
        section=section,
        content=content,
        version=version,
        updatedBy=updated_by,
        updatedAt=updated_at.isoformat() if updated_at else None,
    )


# --- Self-Model Endpoints ---


@router.get("/{user_id}/self-model")
async def get_full_self_model(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    runtime_db: Session = Depends(_get_optional_runtime_db),
) -> dict[str, object]:
    """Get the complete self-model for this user across soul and runtime stores."""
    require_unlocked_user(request, user_id)

    from anima_server.services.agent.self_model import (
        ensure_self_model_exists,
        get_active_intentions,
        get_all_self_model_blocks,
        get_growth_log_entries,
        get_growth_log_text,
        get_identity_block,
        get_working_context,
        render_self_model_section,
    )

    ensure_self_model_exists(db, user_id=user_id)
    blocks = get_all_self_model_blocks(db, user_id=user_id)

    sections: dict[str, object] = {}

    for section_name, block in blocks.items():
        if section_name in {"identity", "growth_log", "inner_state", "working_memory", "intentions"}:
            continue
        sections[section_name] = _section_dict(
            content=render_self_model_section(block, user_id=user_id),
            version=block.version,
            updated_by=block.updated_by,
            updated_at=block.updated_at,
        )

    identity_block = get_identity_block(db, user_id=user_id)
    if identity_block is not None:
        sections["identity"] = _section_dict(
            content=render_self_model_section(identity_block, user_id=user_id),
            version=identity_block.version,
            updated_by=identity_block.updated_by,
            updated_at=identity_block.updated_at,
        )

    growth_entries = get_growth_log_entries(db, user_id=user_id)
    if growth_entries:
        latest = growth_entries[0]
        sections["growth_log"] = _section_dict(
            content=get_growth_log_text(db, user_id=user_id),
            version=len(growth_entries),
            updated_by=latest.source,
            updated_at=latest.created_at,
        )
    else:
        sections["growth_log"] = _section_dict(
            content="",
            version=1,
            updated_by="system",
            updated_at=None,
        )

    working_context = get_working_context(runtime_db or db, user_id=user_id)
    for section_name in ("inner_state", "working_memory"):
        block = working_context.get(section_name)
        if block is None:
            continue
        sections[section_name] = _section_dict(
            content=render_self_model_section(block, user_id=user_id),
            version=block.version,
            updated_by=block.updated_by,
            updated_at=block.updated_at,
        )

    intentions_block = get_active_intentions(runtime_db or db, user_id=user_id)
    if intentions_block is not None:
        sections["intentions"] = _section_dict(
            content=render_self_model_section(
                intentions_block, user_id=user_id),
            version=intentions_block.version,
            updated_by=intentions_block.updated_by,
            updated_at=intentions_block.updated_at,
        )

    return {"userId": user_id, "sections": sections}


@router.get("/{user_id}/self-model/{section}")
async def get_self_model_section(
    user_id: int,
    section: str,
    request: Request,
    db: Session = Depends(get_db),
    runtime_db: Session = Depends(_get_optional_runtime_db),
) -> SelfModelSectionResponse:
    """Get a single self-model section."""
    require_unlocked_user(request, user_id)

    from anima_server.services.agent.self_model import (
        ALL_SECTIONS,
        ensure_self_model_exists,
        get_active_intentions,
        get_growth_log_entries,
        get_growth_log_text,
        get_identity_block,
        get_self_model_block,
        get_working_context,
        render_self_model_section,
    )

    if section not in ALL_SECTIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid section: {section}. Valid: {', '.join(ALL_SECTIONS)}",
        )

    ensure_self_model_exists(db, user_id=user_id)

    if section == "identity":
        block = get_identity_block(db, user_id=user_id)
        if block is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Section not found")
        return _section_response(
            section=section,
            content=render_self_model_section(block, user_id=user_id),
            version=block.version,
            updated_by=block.updated_by,
            updated_at=block.updated_at,
        )

    if section == "growth_log":
        entries = get_growth_log_entries(db, user_id=user_id)
        if entries:
            latest = entries[0]
            return _section_response(
                section=section,
                content=get_growth_log_text(db, user_id=user_id),
                version=len(entries),
                updated_by=latest.source,
                updated_at=latest.created_at,
            )
        return _section_response(
            section=section,
            content="",
            version=1,
            updated_by="system",
            updated_at=None,
        )

    if section in {"inner_state", "working_memory"}:
        block = get_working_context(
            runtime_db or db, user_id=user_id).get(section)
        if block is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Section not found")
        return _section_response(
            section=section,
            content=render_self_model_section(block, user_id=user_id),
            version=block.version,
            updated_by=block.updated_by,
            updated_at=block.updated_at,
        )

    if section == "intentions":
        block = get_active_intentions(runtime_db or db, user_id=user_id)
        if block is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Section not found")
        return _section_response(
            section=section,
            content=render_self_model_section(block, user_id=user_id),
            version=block.version,
            updated_by=block.updated_by,
            updated_at=block.updated_at,
        )

    block = get_self_model_block(db, user_id=user_id, section=section)
    if block is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Section not found")

    return _section_response(
        section=block.section,
        content=render_self_model_section(block, user_id=user_id),
        version=block.version,
        updated_by=block.updated_by,
        updated_at=block.updated_at,
    )


@router.put("/{user_id}/self-model/{section}")
async def update_self_model_section(
    user_id: int,
    section: str,
    payload: SelfModelUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    runtime_db: Session = Depends(_get_optional_runtime_db),
) -> SelfModelSectionResponse:
    """User edits a self-model section. Treated as highest-confidence evidence."""
    require_unlocked_user(request, user_id)

    from anima_server.services.agent.self_model import (
        ALL_SECTIONS,
        SOUL_SECTIONS,
        append_growth_log_entry,
        ensure_self_model_exists,
        render_self_model_section,
        set_active_intentions,
        set_self_model_block,
        set_working_context,
    )
    from anima_server.services.agent.soul_blocks import set_soul_block

    if section not in ALL_SECTIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid section: {section}. Valid: {', '.join(ALL_SECTIONS)}",
        )

    ensure_self_model_exists(db, user_id=user_id)

    rt = runtime_db or db
    if section == "intentions":
        block = set_active_intentions(
            rt,
            user_id=user_id,
            content=payload.content,
            updated_by="user_edit",
        )
    elif section in {"inner_state", "working_memory"}:
        block = set_working_context(
            rt,
            user_id=user_id,
            section=section,
            content=payload.content,
            updated_by="user_edit",
        )
    elif section in SOUL_SECTIONS:
        block = set_soul_block(
            db,
            user_id=user_id,
            section=section,
            content=payload.content,
            updated_by="user_edit",
        )
    else:
        block = set_self_model_block(
            db,
            user_id=user_id,
            section=section,
            content=payload.content,
            updated_by="user_edit",
        )

    if section != "growth_log":
        append_growth_log_entry(
            db,
            user_id=user_id,
            entry=f"User manually edited the '{section}' section",
        )

    db.commit()
    if runtime_db is not None:
        runtime_db.commit()

    return _section_response(
        section=section,
        content=render_self_model_section(block, user_id=user_id),
        version=block.version,
        updated_by=block.updated_by,
        updated_at=block.updated_at,
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

    profile = db.query(AgentProfile).filter(
        AgentProfile.user_id == user_id).first()
    if profile is None:
        return {
            "agentName": "Anima",
            "relationship": "companion",
            "personaTemplate": "default",
            "agentType": "companion",
            "avatarUrl": None,
            "setupComplete": False,
        }
    return {
        "agentName": profile.agent_name,
        "relationship": profile.relationship,
        "personaTemplate": "default",
        "agentType": profile.agent_type,
        "avatarUrl": profile.avatar_url,
        "setupComplete": profile.setup_complete,
    }


@router.patch("/{user_id}/agent-profile")
async def update_agent_profile(
    user_id: int,
    payload: AgentProfileUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Update the agent's profile - name, relationship, persona template."""
    require_unlocked_user(request, user_id)

    from anima_server.models import AgentProfile
    from anima_server.services.agent.self_model import get_self_model_block, set_self_model_block
    from anima_server.services.agent.system_prompt import render_origin_block, render_persona_seed

    profile = db.query(AgentProfile).filter(
        AgentProfile.user_id == user_id).first()
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")

    name_changed = False
    if payload.agentName is not None:
        profile.agent_name = payload.agentName.strip() or "Anima"
        name_changed = True

    relationship_changed = False
    if payload.relationship is not None:
        profile.relationship = payload.relationship.strip()
        relationship_changed = True

    profile.setup_complete = True

    if name_changed:
        origin_content = render_origin_block(
            agent_name=profile.agent_name,
            creator_name=profile.creator_name,
            agent_type=profile.agent_type,
        )
        set_self_model_block(
            db,
            user_id=user_id,
            section="soul",
            content=origin_content,
            updated_by="agent_setup",
        )

    if relationship_changed:
        human_block = get_self_model_block(
            db, user_id=user_id, section="human")
        if human_block:
            content = df(user_id, human_block.content,
                         table="self_model_blocks", field="content")
            lines = content.split("\n")
            new_lines = [
                line for line in lines if not line.startswith("Relationship:")]
            if profile.relationship:
                new_lines.append(f"Relationship: {profile.relationship}")
            set_self_model_block(
                db,
                user_id=user_id,
                section="human",
                content="\n".join(new_lines),
                updated_by="agent_setup",
            )

    if payload.personaTemplate is not None:
        from anima_server.services.agent.system_prompt import PromptTemplateError

        try:
            persona_content = render_persona_seed(
                payload.personaTemplate,
                agent_name=profile.agent_name,
                creator_name=profile.creator_name,
            )
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
        "agentType": profile.agent_type,
        "avatarUrl": profile.avatar_url,
        "setupComplete": True,
    }


ALLOWED_AVATAR_TYPES = {"image/png", "image/jpeg",
                        "image/gif", "image/webp", "image/svg+xml"}
MAX_AVATAR_SIZE = 2 * 1024 * 1024  # 2 MB


@router.post("/{user_id}/agent-profile/avatar")
async def upload_agent_avatar(
    user_id: int,
    file: UploadFile,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Upload a custom avatar image for the agent."""
    require_unlocked_user(request, user_id)

    if file.content_type not in ALLOWED_AVATAR_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported image type: {file.content_type}. "
            f"Allowed: {', '.join(sorted(ALLOWED_AVATAR_TYPES))}",
        )

    data = await file.read()
    if len(data) > MAX_AVATAR_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Avatar must be under {MAX_AVATAR_SIZE // 1024 // 1024} MB.",
        )

    from anima_server.models import AgentProfile
    from anima_server.services.storage import get_user_data_dir

    profile = db.query(AgentProfile).filter(
        AgentProfile.user_id == user_id).first()
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")

    ext_map = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
    }
    ext = ext_map.get(file.content_type, ".png")
    avatar_dir = get_user_data_dir(user_id) / "avatars"
    avatar_dir.mkdir(parents=True, exist_ok=True)
    avatar_path = avatar_dir / f"agent{ext}"

    # Remove old avatar files with different extensions
    for old in avatar_dir.glob("agent.*"):
        if old != avatar_path:
            old.unlink(missing_ok=True)

    avatar_path.write_bytes(data)

    avatar_url = f"/consciousness/{user_id}/agent-profile/avatar"
    profile.avatar_url = avatar_url
    db.commit()

    return {"avatarUrl": avatar_url}


@router.get("/{user_id}/agent-profile/avatar")
async def get_agent_avatar(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> FileResponse:
    """Serve the agent's avatar image."""
    require_unlocked_user(request, user_id)

    from anima_server.services.storage import get_user_data_dir

    avatar_dir = get_user_data_dir(user_id) / "avatars"
    if avatar_dir.is_dir():
        for candidate in avatar_dir.glob("agent.*"):
            if candidate.is_file():
                media_map = {
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".gif": "image/gif",
                    ".webp": "image/webp",
                    ".svg": "image/svg+xml",
                }
                media_type = media_map.get(
                    candidate.suffix.lower(), "application/octet-stream")
                return FileResponse(candidate, media_type=media_type)

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                        detail="No avatar found")


@router.delete("/{user_id}/agent-profile/avatar")
async def delete_agent_avatar(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Delete the agent's custom avatar, reverting to default."""
    require_unlocked_user(request, user_id)

    from anima_server.models import AgentProfile
    from anima_server.services.storage import get_user_data_dir

    profile = db.query(AgentProfile).filter(
        AgentProfile.user_id == user_id).first()
    if profile is not None:
        profile.avatar_url = None
        db.commit()

    avatar_dir = get_user_data_dir(user_id) / "avatars"
    if avatar_dir.is_dir():
        for old in avatar_dir.glob("agent.*"):
            old.unlink(missing_ok=True)

    return {"avatarUrl": None}


# --- Emotional State Endpoints ---


@router.get("/{user_id}/emotions")
async def get_emotional_state(
    user_id: int,
    request: Request,
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
    runtime_db: Session = Depends(_get_optional_runtime_db),
) -> EmotionalContextResponse:
    """Get the AI's current emotional read of the user."""
    require_unlocked_user(request, user_id)

    from anima_server.services.agent.emotional_intelligence import (
        get_recent_signals,
        synthesize_emotional_context,
    )

    emotion_db = runtime_db or db
    signals = get_recent_signals(emotion_db, user_id=user_id, limit=limit)
    context = synthesize_emotional_context(emotion_db, user_id=user_id)

    dominant = None
    if signals:
        emotion_scores: dict[str, float] = {}
        for signal in signals[:5]:
            emotion_scores[signal.emotion] = emotion_scores.get(
                signal.emotion, 0) + signal.confidence
        if emotion_scores:
            dominant = max(emotion_scores, key=emotion_scores.get)

    from anima_server.models import EmotionalSignal

    def _signal_text(signal, field: str) -> str:
        """Read evidence/topic, decrypting legacy EmotionalSignal rows."""
        value = getattr(signal, field, "") or ""
        if isinstance(signal, EmotionalSignal) and value:
            return df(user_id, value, table="emotional_signals", field=field)
        return str(value)

    return EmotionalContextResponse(
        dominantEmotion=dominant,
        recentSignals=[
            EmotionalSignalResponse(
                emotion=signal.emotion,
                confidence=signal.confidence,
                trajectory=signal.trajectory,
                evidenceType=signal.evidence_type,
                evidence=_signal_text(signal, "evidence"),
                topic=_signal_text(signal, "topic"),
                createdAt=signal.created_at.isoformat() if signal.created_at else None,
            )
            for signal in signals
        ],
        synthesizedContext=context,
    )


# --- Intentions Endpoints ---


@router.get("/{user_id}/intentions")
async def get_intentions(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    runtime_db: Session = Depends(_get_optional_runtime_db),
) -> dict[str, str]:
    """Get the AI's current intentions and behavioral rules."""
    require_unlocked_user(request, user_id)

    from anima_server.services.agent.intentions import get_intentions_text

    content = get_intentions_text(runtime_db or db, user_id=user_id)
    return {"content": content}
