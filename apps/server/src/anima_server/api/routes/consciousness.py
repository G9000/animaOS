"""Consciousness API: view and edit the AI's self-model, emotional state, and intentions."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_user_async
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


class PendingMemoryOpResponse(BaseModel):
    id: int
    opType: str
    targetBlock: str
    content: str
    oldContent: str | None = None
    createdAt: str | None = None


class PendingMemoryOpsResponse(BaseModel):
    userId: int
    pendingOps: list[PendingMemoryOpResponse]


class PendingMemoryConsolidationResponse(BaseModel):
    userId: int
    status: str
    opsProcessed: int
    opsSkipped: int
    opsFailed: int
    remainingPendingOps: int


class UserProfileEvidenceResponse(BaseModel):
    id: int
    sourceKind: str
    sourceMemoryId: int | None = None
    sourceEvidenceId: int | None = None
    sourceClaimEvidenceId: int | None = None
    runtimeThreadId: int | None = None
    runtimeMessageId: int | None = None
    evidenceText: str
    observedAt: str | None = None
    createdAt: str | None = None


class UserProfileFieldResponse(BaseModel):
    id: int
    category: str
    key: str
    value: str
    confidence: float
    status: str
    sourceKind: str
    sourceMemoryId: int | None = None
    sourceEvidenceId: int | None = None
    sourceClaimEvidenceId: int | None = None
    supersededById: int | None = None
    firstObservedAt: str | None = None
    lastObservedAt: str | None = None
    updatedAt: str | None = None
    evidence: list[UserProfileEvidenceResponse]


class UserProfileResponse(BaseModel):
    userId: int
    fields: list[UserProfileFieldResponse]


class UserProfileCorrectionRequest(BaseModel):
    value: str
    confidence: float = 1.0
    evidenceText: str = "user correction"


class SelfModelUpdateRequest(BaseModel):
    content: str
    allowIdentityOverride: bool = False


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
    valence: float | None = None
    arousal: float | None = None


class AgentStateContextMessageResponse(BaseModel):
    role: str
    content: str
    source: str


class AgentStateResponse(BaseModel):
    userId: int
    dominantEmotion: str | None = None
    thought: str
    thoughtSource: str
    chatPrompt: str
    contextMessages: list[AgentStateContextMessageResponse]
    affectHint: str | None = None


class AgentBiographyPreviewSectionResponse(BaseModel):
    id: str
    title: str
    content: str
    source: str


class AgentBiographyPreviewResponse(BaseModel):
    userId: int
    agentName: str
    relationship: str
    agentType: str
    avatarUrl: str | None = None
    agentBirthday: str | None = None
    birthday: str | None = None
    dominantEmotion: str | None = None
    identityDraft: str
    personaDraft: str
    biography: str
    contextLine: str
    sections: list[AgentBiographyPreviewSectionResponse]
    promptBlockLabels: list[str]


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


def _iso_seconds(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(microsecond=0).isoformat()


def _effective_agent_birthday(profile) -> str | None:
    return _iso_seconds(profile.agent_birthday or profile.created_at)


def _parse_agent_birthday(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="agentBirthday must be an ISO datetime",
        ) from exc


def _pending_op_dict(*, op) -> dict[str, object]:
    return {
        "id": op.id,
        "opType": op.op_type,
        "targetBlock": op.target_block,
        "content": op.content,
        "oldContent": op.old_content,
        "createdAt": op.created_at.isoformat() if op.created_at else None,
    }


def _list_pending_ops(runtime_db: Session | None, *, user_id: int) -> list[dict[str, object]]:
    if runtime_db is None:
        return []

    from anima_server.services.agent.pending_ops import get_pending_ops

    return [
        _pending_op_dict(op=op)
        for op in get_pending_ops(runtime_db, user_id=user_id)
    ]


def _profile_field_response(*, user_id: int, field) -> UserProfileFieldResponse:
    return UserProfileFieldResponse(
        id=field.id,
        category=field.category,
        key=field.key,
        value=df(
            user_id,
            field.value_text,
            table="user_profile_fields",
            field="value_text",
        ),
        confidence=field.confidence,
        status=field.status,
        sourceKind=field.source_kind,
        sourceMemoryId=field.source_memory_id,
        sourceEvidenceId=field.source_evidence_id,
        sourceClaimEvidenceId=field.source_claim_evidence_id,
        supersededById=field.superseded_by_id,
        firstObservedAt=_iso_seconds(field.first_observed_at),
        lastObservedAt=_iso_seconds(field.last_observed_at),
        updatedAt=_iso_seconds(field.updated_at),
        evidence=[
            UserProfileEvidenceResponse(
                id=evidence.id,
                sourceKind=evidence.source_kind,
                sourceMemoryId=evidence.source_memory_id,
                sourceEvidenceId=evidence.source_evidence_id,
                sourceClaimEvidenceId=evidence.source_claim_evidence_id,
                runtimeThreadId=evidence.runtime_thread_id,
                runtimeMessageId=evidence.runtime_message_id,
                evidenceText=df(
                    user_id,
                    evidence.evidence_text,
                    table="user_profile_field_evidence",
                    field="evidence_text",
                ),
                observedAt=_iso_seconds(evidence.observed_at),
                createdAt=_iso_seconds(evidence.created_at),
            )
            for evidence in field.evidence
        ],
    )


def _invalidate_companion_memory(user_id: int) -> None:
    try:
        from anima_server.services.agent.companion import get_companion

        companion = get_companion(user_id)
        if companion is not None:
            companion.invalidate_memory()
    except Exception:
        pass


# --- Self-Model Endpoints ---


@router.get("/{user_id}/self-model")
async def get_full_self_model(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    runtime_db: Session = Depends(_get_optional_runtime_db),
) -> dict[str, object]:
    """Get the complete self-model for this user across soul and runtime stores."""
    await require_unlocked_user_async(request, user_id)

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
        content = render_self_model_section(block, user_id=user_id)
        # Overlay unconsolidated pending ops for writable core-memory blocks
        # so the UI reflects what the agent actually sees.
        if runtime_db is not None and section_name in ("human", "persona"):
            from anima_server.services.agent.memory_blocks import build_merged_block_content

            content = build_merged_block_content(
                db, runtime_db, user_id=user_id, section=section_name,
            )
        sections[section_name] = _section_dict(
            content=content,
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

    return {
        "userId": user_id,
        "sections": sections,
        "pendingOps": _list_pending_ops(runtime_db, user_id=user_id),
    }


@router.get("/{user_id}/pending-ops")
async def get_pending_memory_ops(
    user_id: int,
    request: Request,
    runtime_db: Session = Depends(_get_optional_runtime_db),
) -> PendingMemoryOpsResponse:
    """Get unconsolidated core-memory writes waiting for Soul Writer."""
    await require_unlocked_user_async(request, user_id)

    return PendingMemoryOpsResponse(
        userId=user_id,
        pendingOps=[
            PendingMemoryOpResponse(**op)
            for op in _list_pending_ops(runtime_db, user_id=user_id)
        ],
    )


@router.post("/{user_id}/pending-ops/consolidate")
async def consolidate_pending_memory_ops(
    user_id: int,
    request: Request,
    runtime_db: Session = Depends(_get_optional_runtime_db),
) -> PendingMemoryConsolidationResponse:
    """Run Soul Writer immediately for this user's pending memory ops."""
    await require_unlocked_user_async(request, user_id)

    if runtime_db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Runtime database is not available.",
        )

    from anima_server.services.agent.soul_writer import run_soul_writer

    result = await run_soul_writer(user_id)
    runtime_db.expire_all()
    remaining = len(_list_pending_ops(runtime_db, user_id=user_id))

    return PendingMemoryConsolidationResponse(
        userId=user_id,
        status="ok",
        opsProcessed=result.ops_processed,
        opsSkipped=result.ops_skipped,
        opsFailed=result.ops_failed,
        remainingPendingOps=remaining,
    )


@router.get("/{user_id}/user-profile", response_model=UserProfileResponse)
async def get_user_profile(
    user_id: int,
    request: Request,
    include_history: bool = Query(default=False, alias="includeHistory"),
    category: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> UserProfileResponse:
    """List structured user profile fields and their evidence."""
    await require_unlocked_user_async(request, user_id)

    from anima_server.services.agent.user_profile import list_profile_fields

    try:
        fields = list_profile_fields(
            db,
            user_id=user_id,
            include_history=include_history,
            category=category,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return UserProfileResponse(
        userId=user_id,
        fields=[
            _profile_field_response(user_id=user_id, field=field)
            for field in fields
        ],
    )


@router.patch(
    "/{user_id}/user-profile/{field_id}",
    response_model=UserProfileFieldResponse,
)
async def correct_user_profile_field(
    user_id: int,
    field_id: int,
    payload: UserProfileCorrectionRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> UserProfileFieldResponse:
    """Correct a structured user profile field while preserving history."""
    await require_unlocked_user_async(request, user_id)

    from anima_server.services.agent.user_profile import correct_profile_field

    try:
        field = correct_profile_field(
            db,
            user_id=user_id,
            field_id=field_id,
            value=payload.value,
            confidence=payload.confidence,
            evidence_text=payload.evidenceText,
        )
    except ValueError as exc:
        if str(exc) == "Profile value cannot be empty":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    db.commit()
    _invalidate_companion_memory(user_id)
    return _profile_field_response(user_id=user_id, field=field)


@router.delete(
    "/{user_id}/user-profile/{field_id}",
    response_model=UserProfileFieldResponse,
)
async def retract_user_profile_field(
    user_id: int,
    field_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> UserProfileFieldResponse:
    """Retract an active structured user profile field."""
    await require_unlocked_user_async(request, user_id)

    from anima_server.services.agent.user_profile import retract_profile_field

    try:
        field = retract_profile_field(db, user_id=user_id, field_id=field_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    db.commit()
    _invalidate_companion_memory(user_id)
    return _profile_field_response(user_id=user_id, field=field)


@router.get("/{user_id}/self-model/{section}")
async def get_self_model_section(
    user_id: int,
    section: str,
    request: Request,
    db: Session = Depends(get_db),
    runtime_db: Session = Depends(_get_optional_runtime_db),
) -> SelfModelSectionResponse:
    """Get a single self-model section."""
    await require_unlocked_user_async(request, user_id)

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

    content = render_self_model_section(block, user_id=user_id)
    if runtime_db is not None and section in ("human", "persona"):
        from anima_server.services.agent.memory_blocks import build_merged_block_content

        content = build_merged_block_content(
            db, runtime_db, user_id=user_id, section=section,
        )

    return _section_response(
        section=block.section,
        content=content,
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
    await require_unlocked_user_async(request, user_id)

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

    if section in {"identity", "soul", "user_directive", "intentions"}:
        from anima_server.models import AgentProfile

        profile = db.query(AgentProfile).filter(
            AgentProfile.user_id == user_id).first()
        if (
            profile is not None
            and profile.setup_complete
            and not payload.allowIdentityOverride
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Identity override required to change {section}",
            )

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
    agentBirthday: str | None = None
    thinkingMonologue: list[str] | None = None
    allowIdentityOverride: bool = False


@router.get("/{user_id}/agent-profile")
async def get_agent_profile(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Get the agent profile for this user."""
    await require_unlocked_user_async(request, user_id)

    from anima_server.models import AgentProfile
    from anima_server.services.agent.thinking_monologue import (
        DEFAULT_THINKING_MONOLOGUE,
        parse_thinking_monologue,
    )

    profile = db.query(AgentProfile).filter(
        AgentProfile.user_id == user_id).first()
    if profile is None:
        return {
            "agentName": "Anima",
            "relationship": "companion",
            "personaTemplate": "default",
            "agentType": "companion",
            "avatarUrl": None,
            "agentBirthday": None,
            "thinkingMonologue": list(DEFAULT_THINKING_MONOLOGUE),
            "setupComplete": False,
        }
    return {
        "agentName": profile.agent_name,
        "relationship": profile.relationship,
        "personaTemplate": "default",
        "agentType": profile.agent_type,
        "avatarUrl": profile.avatar_url,
        "agentBirthday": _effective_agent_birthday(profile),
        "thinkingMonologue": parse_thinking_monologue(profile.thinking_monologue_json),
        "setupComplete": profile.setup_complete,
    }


@router.get("/{user_id}/agent-biography-preview")
async def get_agent_biography_preview(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    runtime_db: Session = Depends(_get_optional_runtime_db),
) -> AgentBiographyPreviewResponse:
    """Get a compiled preview of the backend context shaping this agent."""
    await require_unlocked_user_async(request, user_id)

    from anima_server.services.agent.biography_preview import build_agent_biography_preview

    preview = build_agent_biography_preview(
        db,
        user_id=user_id,
        runtime_db=runtime_db,
    )
    return AgentBiographyPreviewResponse(
        **{
            **preview,
            "sections": [
                AgentBiographyPreviewSectionResponse(**section)
                for section in preview["sections"]
            ],
        },
    )


@router.patch("/{user_id}/agent-profile")
async def update_agent_profile(
    user_id: int,
    payload: AgentProfileUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Update the agent's profile - name, relationship, persona template."""
    await require_unlocked_user_async(request, user_id)

    from anima_server.models import AgentProfile
    from anima_server.services.agent.profile_memory import (
        record_agent_name_memory,
        record_agent_relationship_memory,
    )
    from anima_server.services.agent.self_model import get_self_model_block, set_self_model_block
    from anima_server.services.agent.system_prompt import render_origin_block, render_persona_seed
    from anima_server.services.agent.thinking_monologue import (
        parse_thinking_monologue,
        serialize_thinking_monologue,
    )

    profile = db.query(AgentProfile).filter(
        AgentProfile.user_id == user_id).first()
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")

    old_agent_name = profile.agent_name
    name_changed = False
    if payload.agentName is not None:
        next_agent_name = payload.agentName.strip() or "Anima"
        if (
            profile.setup_complete
            and next_agent_name != old_agent_name
            and not payload.allowIdentityOverride
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Identity override required to change agent name",
            )
        profile.agent_name = next_agent_name
        name_changed = next_agent_name != old_agent_name

    old_relationship = profile.relationship
    relationship_changed = False
    if payload.relationship is not None:
        next_relationship = payload.relationship.strip()
        if (
            profile.setup_complete
            and next_relationship != old_relationship
            and not payload.allowIdentityOverride
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Identity override required to change relationship",
            )
        profile.relationship = next_relationship
        relationship_changed = next_relationship != old_relationship

    if payload.agentBirthday is not None:
        next_agent_birthday = _parse_agent_birthday(payload.agentBirthday)
        next_agent_birthday_text = _iso_seconds(next_agent_birthday)
        if (
            profile.setup_complete
            and next_agent_birthday_text != _effective_agent_birthday(profile)
            and not payload.allowIdentityOverride
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Identity override required to change agent birthday",
            )
        profile.agent_birthday = next_agent_birthday

    if payload.thinkingMonologue is not None:
        profile.thinking_monologue_json = serialize_thinking_monologue(
            payload.thinkingMonologue,
        )

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

        record_agent_name_memory(
            db,
            user_id=user_id,
            old_name=old_agent_name,
            new_name=profile.agent_name,
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
        record_agent_relationship_memory(
            db,
            user_id=user_id,
            old_relationship=old_relationship,
            new_relationship=profile.relationship,
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
        "agentBirthday": _effective_agent_birthday(profile),
        "thinkingMonologue": parse_thinking_monologue(profile.thinking_monologue_json),
        "setupComplete": True,
    }


@router.post("/{user_id}/agent-profile/thinking-monologue/generate")
async def generate_agent_thinking_monologue(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Generate a draft Thinking Monologue without persisting it."""
    await require_unlocked_user_async(request, user_id)

    from anima_server.services.agent.thinking_monologue import generate_thinking_monologue

    return {
        "thinkingMonologue": await generate_thinking_monologue(db, user_id=user_id),
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
    await require_unlocked_user_async(request, user_id)
    from anima_server.services.corefs.asset_authority import (
        require_legacy_asset_mutation_allowed,
    )

    require_legacy_asset_mutation_allowed(user_id)

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
) -> Response:
    """Serve the agent's avatar image."""
    await require_unlocked_user_async(request, user_id)

    from anima_server.services.images.store import resolve_identity_avatar_byte_source
    from anima_server.services.storage import get_user_data_dir

    core_source = resolve_identity_avatar_byte_source(user_id=user_id)
    if core_source is not None:
        return StreamingResponse(
            core_source.iter_chunks(),
            media_type=core_source.content_type,
        )

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
    await require_unlocked_user_async(request, user_id)
    from anima_server.services.corefs.asset_authority import (
        require_legacy_asset_mutation_allowed,
    )

    require_legacy_asset_mutation_allowed(user_id)

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


@router.get("/{user_id}/agent-state")
async def get_agent_state(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    runtime_db: Session = Depends(_get_optional_runtime_db),
) -> AgentStateResponse:
    """Get a compact, backend-grounded companion state line for ambient UI."""
    await require_unlocked_user_async(request, user_id)

    from anima_server.services.agent.proactive import build_agent_state

    state = build_agent_state(db, user_id=user_id, runtime_db=runtime_db)
    return AgentStateResponse(
        userId=state.user_id,
        dominantEmotion=state.dominant_emotion,
        thought=state.thought,
        thoughtSource=state.thought_source,
        chatPrompt=state.chat_prompt,
        contextMessages=[
            AgentStateContextMessageResponse(**message)
            for message in state.context_messages
        ],
        affectHint=state.affect_hint,
    )


@router.get("/{user_id}/emotions")
async def get_emotional_state(
    user_id: int,
    request: Request,
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
    runtime_db: Session = Depends(_get_optional_runtime_db),
) -> EmotionalContextResponse:
    """Get the AI's current emotional read of the user."""
    await require_unlocked_user_async(request, user_id)

    from anima_server.services.agent.emotional_intelligence import (
        dominant_valence_arousal,
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

    va = dominant_valence_arousal(dominant)
    valence = va[0] if va else None
    arousal = va[1] if va else None

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
        valence=valence,
        arousal=arousal,
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
    await require_unlocked_user_async(request, user_id)

    from anima_server.services.agent.intentions import get_intentions_text

    content = get_intentions_text(runtime_db or db, user_id=user_id)
    return {"content": content}
