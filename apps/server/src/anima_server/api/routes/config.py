from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from anima_server.api.deps.db_mode import require_sqlite_mode
from anima_server.api.deps.unlock import require_unlocked_user
from anima_server.config import persist_runtime_settings, settings
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
    extractionModel: str | None = None
    ollamaUrl: str | None = None
    hasApiKey: bool = False
    systemPrompt: str | None = None


class AgentConfigUpdateRequest(BaseModel):
    provider: str
    model: str
    extractionModel: str | None = None
    apiKey: str | None = None
    ollamaUrl: str | None = None
    systemPrompt: str | None = None


class OllamaModelDetails(BaseModel):
    format: str | None = None
    family: str | None = None
    families: list[str] | None = None
    parameterSize: str | None = None
    quantizationLevel: str | None = None


class OllamaModelInfo(BaseModel):
    name: str
    modifiedAt: str | None = None
    size: int | None = None
    digest: str | None = None
    details: OllamaModelDetails | None = None


class PersonaTemplateInfo(BaseModel):
    id: str
    name: str
    description: str
    defaultAvatarUrl: str | None = None


AVAILABLE_PROVIDERS: list[ProviderInfo] = [
    ProviderInfo(name="scaffold", defaultModel="scaffold",
                 requiresApiKey=False),
    ProviderInfo(
        name="ollama", defaultModel="vaultbox/qwen3.5-uncensored:35b", requiresApiKey=False
    ),
    ProviderInfo(name="openrouter",
                 defaultModel="google/gemma-3-27b-it", requiresApiKey=True),
    ProviderInfo(name="moonshot", defaultModel="kimi-k2-5",
                 requiresApiKey=True),
    ProviderInfo(name="vllm", defaultModel="default", requiresApiKey=False),
    ProviderInfo(name="openai", defaultModel="gpt-4o", requiresApiKey=True),
]

VALID_PROVIDERS = {"scaffold"} | set(SUPPORTED_PROVIDERS)


def _normalize_ollama_base_url(base_url: str | None) -> str:
    configured = (base_url or "").strip()
    if not configured and settings.agent_provider == "ollama":
        configured = settings.agent_base_url.strip()
    if not configured:
        configured = "http://127.0.0.1:11434"
    normalized = configured.rstrip("/")
    if normalized.endswith("/v1"):
        normalized = normalized[:-3]
    return normalized


def _parse_ollama_model(raw: Any) -> OllamaModelInfo | None:
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        return None

    details_raw = raw.get("details")
    details = None
    if isinstance(details_raw, dict):
        families = details_raw.get("families")
        details = OllamaModelDetails(
            format=details_raw.get("format") if isinstance(
                details_raw.get("format"), str) else None,
            family=details_raw.get("family") if isinstance(
                details_raw.get("family"), str) else None,
            families=[item for item in families if isinstance(
                item, str)] if isinstance(families, list) else None,
            parameterSize=(
                details_raw.get("parameter_size")
                if isinstance(details_raw.get("parameter_size"), str)
                else None
            ),
            quantizationLevel=(
                details_raw.get("quantization_level")
                if isinstance(details_raw.get("quantization_level"), str)
                else None
            ),
        )

    size = raw.get("size")
    return OllamaModelInfo(
        name=name,
        modifiedAt=raw.get("modified_at") if isinstance(
            raw.get("modified_at"), str) else None,
        size=size if isinstance(size, int) else None,
        digest=raw.get("digest") if isinstance(
            raw.get("digest"), str) else None,
        details=details,
    )


async def _list_ollama_models(base_url: str) -> list[OllamaModelInfo]:
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(f"{base_url}/api/tags")
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Ollama returned an invalid model list.")

    models_raw = payload.get("models")
    if not isinstance(models_raw, list):
        raise ValueError("Ollama returned an invalid model list.")

    models = [model for item in models_raw if (
        model := _parse_ollama_model(item)) is not None]
    return sorted(models, key=lambda item: item.name.lower())


@router.get("/providers", response_model=list[ProviderInfo])
async def get_providers() -> list[ProviderInfo]:
    return AVAILABLE_PROVIDERS


@router.get("/ollama-models", response_model=list[OllamaModelInfo])
async def get_ollama_models(baseUrl: str | None = None) -> list[OllamaModelInfo]:
    normalized_base_url = _normalize_ollama_base_url(baseUrl)
    try:
        return await _list_ollama_models(normalized_base_url)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to reach Ollama at {normalized_base_url}.",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc


@router.get("/persona-templates", response_model=list[PersonaTemplateInfo])
async def get_persona_templates() -> list[PersonaTemplateInfo]:
    """Return available persona templates for AI creation."""
    return [
        PersonaTemplateInfo(
            id="default",
            name="Default",
            description="A thoughtful, capable companion — neutral and adaptable.",
        ),
        PersonaTemplateInfo(
            id="companion",
            name="Companion",
            description="Warm, emotionally attuned, and deeply present — for meaningful connection.",
        ),
        PersonaTemplateInfo(
            id="mirror",
            name="Mirror",
            description="A cognitive mirror — reflects your voice, your thinking, your perspective back at you.",
        ),
        PersonaTemplateInfo(
            id="anima",
            name="Anima",
            description="A quiet, deliberate presence — speaks with intention, stays grounded.",
        ),
    ]


@router.get("/{user_id}", response_model=AgentConfigResponse)
async def get_config(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> AgentConfigResponse:
    """Return the active agent config.

    NOTE: Config is still process-global for the current single-user app,
    but updates are persisted in the local runtime config so restart does
    not silently revert to defaults.
    """
    require_unlocked_user(request, user_id)
    return AgentConfigResponse(
        provider=settings.agent_provider,
        model=settings.agent_model,
        extractionModel=settings.agent_extraction_model or None,
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
    """Update and persist the active agent config."""
    require_unlocked_user(request, user_id)

    if payload.provider not in VALID_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported provider: {payload.provider!r}. Valid: {', '.join(sorted(VALID_PROVIDERS))}",
        )

    settings.agent_provider = payload.provider
    settings.agent_model = payload.model
    settings.agent_extraction_model = (payload.extractionModel or "").strip()
    if payload.apiKey is not None:
        settings.agent_api_key = payload.apiKey
    # Only set base_url for ollama/vllm; clear for providers with fixed endpoints
    if (payload.provider == "ollama" and payload.ollamaUrl is not None) or (
        payload.provider == "vllm" and payload.ollamaUrl is not None
    ):
        settings.agent_base_url = payload.ollamaUrl
    else:
        # Clear base_url for providers with fixed endpoints (openrouter, moonshot)
        settings.agent_base_url = ""

    try:
        persist_runtime_settings()
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to persist AI settings: {exc}",
        ) from exc

    from anima_server.services.agent import invalidate_agent_runtime_cache
    from anima_server.services.agent.embeddings import clear_embedding_cache

    clear_embedding_cache()
    invalidate_agent_runtime_cache()

    return {"status": "updated"}
