from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_user
from anima_server.api.deps.db_mode import require_sqlite_mode
from anima_server.config import settings
from anima_server.db import get_db
from anima_server.services.agent.llm import SUPPORTED_PROVIDERS

router = APIRouter(prefix="/api/config", tags=["config"])


class ProviderInfo(BaseModel):
    name: str
    defaultModel: str
    requiresApiKey: bool


class AgentConfigResponse(BaseModel):
    provider: str
    model: str
    ollamaUrl: str | None = None
    hasApiKey: bool = False
    systemPrompt: str | None = None


class AgentConfigUpdateRequest(BaseModel):
    provider: str
    model: str
    apiKey: str | None = None
    ollamaUrl: str | None = None
    systemPrompt: str | None = None


AVAILABLE_PROVIDERS: list[ProviderInfo] = [
    ProviderInfo(name="scaffold", defaultModel="scaffold", requiresApiKey=False),
    ProviderInfo(name="ollama", defaultModel="llama3.2", requiresApiKey=False),
    ProviderInfo(name="openrouter", defaultModel="google/gemma-3-27b-it", requiresApiKey=True),
    ProviderInfo(name="vllm", defaultModel="default", requiresApiKey=False),
]

VALID_PROVIDERS = {"scaffold"} | set(SUPPORTED_PROVIDERS)


@router.get("/providers", response_model=list[ProviderInfo])
async def get_providers() -> list[ProviderInfo]:
    return AVAILABLE_PROVIDERS


@router.get("/{user_id}", response_model=AgentConfigResponse)
async def get_config(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> AgentConfigResponse:
    """Return the active agent config.

    NOTE: Config is process-global, not per-user storage.  The ``user_id``
    path param exists only for auth gating (single-user local app).  If
    multi-tenant support is needed, migrate to a ``user_config`` DB table.
    """
    require_unlocked_user(request, user_id)
    return AgentConfigResponse(
        provider=settings.agent_provider,
        model=settings.agent_model,
        ollamaUrl=settings.agent_base_url or None,
        hasApiKey=bool(settings.agent_api_key),
    )


@router.put("/{user_id}")
async def update_config(
    user_id: int,
    payload: AgentConfigUpdateRequest,
    request: Request,
    _mode: None = Depends(require_sqlite_mode),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Update the active agent config (process-global — see GET docstring)."""
    require_unlocked_user(request, user_id)

    if payload.provider not in VALID_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported provider: {payload.provider!r}. Valid: {', '.join(sorted(VALID_PROVIDERS))}",
        )

    settings.agent_provider = payload.provider
    settings.agent_model = payload.model
    if payload.apiKey is not None:
        settings.agent_api_key = payload.apiKey
    if payload.ollamaUrl is not None:
        settings.agent_base_url = payload.ollamaUrl

    from anima_server.services.agent import invalidate_agent_runtime_cache

    invalidate_agent_runtime_cache()

    return {"status": "updated"}
