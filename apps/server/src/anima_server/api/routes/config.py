from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from anima_server.api.deps.db_mode import require_sqlite_mode
from anima_server.api.deps.unlock import require_unlocked_user
from anima_server.config import (
    get_provider_api_key,
    has_provider_api_keys,
    persist_runtime_settings,
    set_provider_api_key,
    settings,
)
from anima_server.db import get_db
from anima_server.services.agent.embedding_resolution import (
    DEFAULT_EMBEDDING_MODELS,
    has_embedding_piggyback_intent,
    resolve_embedding_model,
    resolve_embedding_provider,
)
from anima_server.services.agent.embeddings import (
    _embedding_skip_reason,
    _resolve_embedding_api_key,
)
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
    # Resolved (not raw-setting) embedding provider/model — reflects the
    # bundled fastembed default when the user hasn't configured anything.
    embeddingProvider: str = "fastembed"
    embeddingModel: str = ""
    # True when the user explicitly configured embeddings (either naming a
    # provider directly, or via the legacy piggyback signal — an embedding
    # model/base URL/API key set against the chat provider); False means
    # this is purely the bundled default with no user intent behind it.
    embeddingIsExplicit: bool = False
    hasEmbeddingApiKey: bool = False


class AgentConfigUpdateRequest(BaseModel):
    provider: str
    model: str
    extractionModel: str | None = None
    apiKey: str | None = None
    ollamaUrl: str | None = None
    systemPrompt: str | None = None
    # Embedding provider selection. Must be a member of SUPPORTED_PROVIDERS
    # (fastembed IS valid here — this is the embedding side, unlike the chat
    # `provider` field above) or "" to reset to the bundled default, which
    # clears the explicit provider/model/key settings below.
    embeddingProvider: str | None = None
    embeddingModel: str | None = None
    embeddingApiKey: str | None = None


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
    ProviderInfo(
        name="doubleword",
        defaultModel="Qwen/Qwen3.6-35B-A3B-FP8",
        requiresApiKey=True,
    ),
    ProviderInfo(name="moonshot", defaultModel="kimi-k2-5",
                 requiresApiKey=True),
    ProviderInfo(name="vllm", defaultModel="default", requiresApiKey=False),
    ProviderInfo(name="openai", defaultModel="gpt-4o", requiresApiKey=True),
    ProviderInfo(
        name="anthropic",
        defaultModel="claude-haiku-4-5-20251001",
        requiresApiKey=True,
    ),
]

# "fastembed" is embeddings-only (no chat completion) and must never be
# selectable as the chat provider via this endpoint.
VALID_PROVIDERS = ({"scaffold"} | set(SUPPORTED_PROVIDERS)) - {"fastembed"}

# Embedding providers the embedding implementation can actually call AND
# actually serve with a usable model. Two independent gates, both required:
#   1. `_embedding_skip_reason` — the same gate `generate_embedding` checks
#      at call time — excludes providers with no embeddings endpoint at all
#      (openrouter/anthropic; still valid CHAT providers, see VALID_PROVIDERS
#      above). Accepting them for embeddings would silently disable dense
#      retrieval, since the embedding call is a no-op skip for those
#      providers.
#   2. `provider in DEFAULT_EMBEDDING_MODELS` — a provider can have a real
#      embeddings endpoint yet no known default embedding model (e.g.
#      moonshot: it passes gate 1, but `resolve_embedding_model` has no
#      entry for it and would otherwise fall through to a wrong catch-all
#      model, e.g. an Ollama-only name POSTed to moonshot's API -> 404 while
#      the UI reports the provider as configured/healthy). Requiring BOTH
#      gates keeps the accepted set to "providers with an endpoint AND a
#      default model" — this must never drift from runtime behavior, hence
#      derived rather than hardcoded.
VALID_EMBEDDING_PROVIDERS = frozenset(
    provider for provider in SUPPORTED_PROVIDERS
    if _embedding_skip_reason(provider) is None and provider in DEFAULT_EMBEDDING_MODELS
) | {"fastembed", ""}


def _normalize_ollama_base_url(
    base_url: str | None,
    *,
    fallback_to_current: bool = True,
) -> str:
    configured = (base_url or "").strip()
    if not configured and fallback_to_current and settings.agent_provider == "ollama":
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


async def _validate_ollama_completion_model(base_url: str, model: str) -> None:
    """Reject Ollama models that cannot service chat/completion requests."""
    normalized_base_url = _normalize_ollama_base_url(base_url)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{normalized_base_url}/api/show",
                json={"model": model},
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Ollama could not inspect model {model!r}.",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to reach Ollama at {normalized_base_url}.",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Ollama returned invalid metadata for model {model!r}.",
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Ollama returned invalid metadata for model {model!r}.",
        )
    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, list) or "completion" not in capabilities:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Ollama model {model!r} does not support completion/chat. "
                "Choose a generative model instead of an embedding-only model."
            ),
        )


async def _validate_prospective_ollama_targets(
    payload: AgentConfigUpdateRequest,
) -> None:
    extraction_provider = (
        settings.agent_extraction_provider.strip() or payload.provider
    )
    candidates = (
        (payload.provider, payload.model.strip()),
        (extraction_provider, (payload.extractionModel or "").strip()),
    )
    base_url = _normalize_ollama_base_url(
        payload.ollamaUrl,
        fallback_to_current=False,
    )
    validated: set[str] = set()
    for provider, model in candidates:
        if provider != "ollama" or not model or model in validated:
            continue
        await _validate_ollama_completion_model(base_url, model)
        validated.add(model)


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
            description="Neutral and practical. Adapts through ordinary conversation.",
        ),
        PersonaTemplateInfo(
            id="companion",
            name="Companion",
            description="Warm and grounded. Useful support without dramatic intensity.",
        ),
        PersonaTemplateInfo(
            id="mirror",
            name="Mirror",
            description="A cognitive mirror — reflects your voice, your thinking, your perspective back at you.",
        ),
        PersonaTemplateInfo(
            id="anima",
            name="Anima",
            description="Quiet and deliberate. Precise, restrained, and grounded.",
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
    embedding_provider = resolve_embedding_provider()
    embedding_is_explicit = bool(settings.agent_embedding_provider.strip()) or has_embedding_piggyback_intent()

    # Legacy normalization (read-only): a pre-existing install may have
    # piggybacked embedding intent (agent_embedding_model/base_url/key set,
    # no explicit agent_embedding_provider) onto a chat provider that has no
    # usable embeddings endpoint for this API (e.g. openrouter/anthropic/
    # moonshot — see VALID_EMBEDDING_PROVIDERS above). resolve_embedding_
    # provider() still returns that chat provider for actual embedding calls
    # (unchanged run-time behavior), but surfacing it here would make the
    # desktop form echo an embeddingProvider value that PUT rejects on ANY
    # save (not just embedding changes) — a save lockout until the user
    # discovers they must clear the piggyback. Report the bundled default
    # instead so the UI shows/echoes something savable; this does NOT
    # mutate stored settings, only the response.
    embedding_normalized = False
    if embedding_provider not in (VALID_EMBEDDING_PROVIDERS - {""}):
        embedding_provider = "fastembed"
        embedding_is_explicit = False
        embedding_normalized = True

    # When the provider was normalized away from an unsupported legacy value,
    # report the normalized provider's OWN default model — not
    # resolve_embedding_model(), which honors the stale explicit
    # agent_embedding_model and would pair "fastembed" with, say, a leftover
    # "text-embedding-3-small". Echoing that stale pair back would let a save
    # pin an invalid model to fastembed and break dense-retrieval load.
    embedding_model = (
        DEFAULT_EMBEDDING_MODELS.get(embedding_provider, "")
        if embedding_normalized
        else resolve_embedding_model(embedding_provider)
    )

    return AgentConfigResponse(
        provider=settings.agent_provider,
        model=settings.agent_model,
        extractionModel=settings.agent_extraction_model or None,
        ollamaUrl=settings.agent_base_url or None,
        hasApiKey=bool(
            get_provider_api_key(settings.agent_provider)
            or (settings.agent_api_key.strip() if not has_provider_api_keys() else "")
        ),
        embeddingProvider=embedding_provider,
        embeddingModel=embedding_model,
        embeddingIsExplicit=embedding_is_explicit,
        # Reflects the key the embedding path will ACTUALLY use — the same
        # rich resolution `generate_embedding` consults (env DOUBLEWORD_API_
        # KEY, the per-provider store, and the legacy agent_api_key
        # piggyback when embedding_provider == chat provider) — not just the
        # raw agent_embedding_api_key setting. A bool only; never exposes
        # the key itself. Computed against the (possibly normalized)
        # embedding_provider above so it matches what is actually reported.
        hasEmbeddingApiKey=bool(_resolve_embedding_api_key(embedding_provider)),
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

    embedding_provider = (
        payload.embeddingProvider.strip() if payload.embeddingProvider is not None else None
    )
    if embedding_provider and embedding_provider not in VALID_EMBEDDING_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported embedding provider: {embedding_provider!r}. "
                f"Valid: {', '.join(sorted(p for p in VALID_EMBEDDING_PROVIDERS if p))} or ''"
            ),
        )

    # embeddingApiKey/embeddingModel require an explicit embeddingProvider
    # (either naming one, or "" to reset). Without this, secrets/models
    # supplied with the field OMITTED entirely still applied against
    # whatever embedding provider happened to already be configured — and if
    # NONE was configured yet, has_embedding_piggyback_intent() then forced
    # resolve_embedding_provider() onto the CHAT provider, so an embedding
    # key meant for one provider could get sent to a completely different
    # chat server (e.g. vllm) it was never meant for. The desktop UI always
    # sends embeddingProvider alongside model/key (see AiSettings.tsx
    # buildEmbeddingUpdate), so this is not a regression for it.
    if embedding_provider is None and (
        payload.embeddingModel is not None or payload.embeddingApiKey is not None
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "embeddingModel/embeddingApiKey require embeddingProvider to be "
                "set explicitly (or '' to reset to the bundled default)."
            ),
        )

    await _validate_prospective_ollama_targets(payload)

    settings.agent_provider = payload.provider
    settings.agent_model = payload.model
    settings.agent_extraction_model = (payload.extractionModel or "").strip()
    if payload.apiKey is not None:
        api_key = payload.apiKey.strip()
        set_provider_api_key(payload.provider, api_key)
        settings.agent_api_key = ""
    # Only set base_url for ollama/vllm; clear for providers with fixed endpoints.
    if (payload.provider == "ollama" and payload.ollamaUrl is not None) or (
        payload.provider == "vllm" and payload.ollamaUrl is not None
    ):
        settings.agent_base_url = payload.ollamaUrl
    else:
        # Clear base_url for providers with fixed endpoints.
        settings.agent_base_url = ""

    if embedding_provider == "":
        # Reset to bundled default: clear the explicit provider/model/key,
        # AND the embedding base URL. Without clearing the base URL too, a
        # previously-configured agent_embedding_base_url (env / persisted
        # runtime settings / legacy) still satisfies
        # has_embedding_piggyback_intent(), so resolve_embedding_provider()
        # would keep piggybacking on the chat provider instead of actually
        # returning to fastembed — this reset would be a no-op in that case.
        settings.agent_embedding_provider = ""
        settings.agent_embedding_model = ""
        settings.agent_embedding_api_key = ""
        settings.agent_embedding_base_url = ""
    else:
        # embeddingProvider may be omitted while model and/or key are sent —
        # those still apply, against the currently-configured embedding
        # provider. Only fields present in the payload are touched.
        if embedding_provider is not None:
            # Compare against the currently-RESOLVED provider, not the raw
            # stored agent_embedding_provider. For a piggyback config (raw ""
            # but resolving to the chat provider because a key/base-URL is
            # set), get_config echoes that resolved provider, and the desktop
            # replays it on ANY save. Comparing against the raw "" would read
            # that echo as a switch and wrongly clear the piggyback key/base-
            # URL/model below — an unrelated settings save could delete the
            # only embedding credential and break dense retrieval. Resolve
            # before mutating (settings.agent_embedding_provider still holds
            # the old value here).
            current_effective_provider = resolve_embedding_provider()
            provider_changed = embedding_provider != current_effective_provider
            settings.agent_embedding_provider = embedding_provider
            if provider_changed:
                # An embedding-provider switch without an explicit override
                # for a given field must reset that field to the NEW
                # provider's default rather than silently carrying over
                # state left over from the OLD provider.
                if payload.embeddingApiKey is None:
                    # Switching to a different cloud provider without
                    # supplying a fresh key must not let the OLD provider's
                    # stored key be silently reused against the NEW
                    # provider — that would send the wrong secret. Clear it;
                    # the user must re-enter a key for the new provider
                    # (unless the payload provides one below, in which case
                    # that overrides this clear).
                    settings.agent_embedding_api_key = ""
                if payload.embeddingModel is None:
                    # A stale model name left over from the OLD provider
                    # (e.g. "text-embedding-3-small" from openai) would
                    # otherwise be treated by resolve_embedding_model as an
                    # explicit override for the NEW provider — which
                    # usually can't serve it, silently disabling dense
                    # embeddings until manually corrected. Clear it so the
                    # new provider's own default model applies.
                    settings.agent_embedding_model = ""
                # There is no dedicated request field to supply a distinct
                # embedding base URL, so any provider change is inherently
                # "without an explicit override" for it. A base URL left
                # over from a previous (possibly local/custom) provider
                # must not be replayed against the new one:
                # _resolve_embedding_base_url returns agent_embedding_base_url
                # verbatim whenever it is set, regardless of which provider
                # is now active, so an un-cleared stale value would
                # silently misroute the new provider's requests.
                settings.agent_embedding_base_url = ""
        if payload.embeddingModel is not None:
            settings.agent_embedding_model = payload.embeddingModel.strip()
        if payload.embeddingApiKey is not None:
            settings.agent_embedding_api_key = payload.embeddingApiKey.strip()

    try:
        persist_runtime_settings()
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to persist AI settings: {exc}",
        ) from exc

    from anima_server.services.agent import invalidate_agent_runtime_cache
    from anima_server.services.agent.embeddings import clear_embedding_cache

    # A changed embedding provider/model/dim (including a reset back to the
    # bundled default) is picked up on the next embedding call purely via
    # clear_embedding_cache(): it re-arms the one-shot cold-start sync,
    # resets the embedding-contract cache, and clears the detected-dim latch
    # (see embeddings.clear_embedding_cache), so a dimension/model mismatch
    # is caught by the existing contract-migration machinery instead of
    # silently serving stale-model vectors. Nothing else to build here.
    clear_embedding_cache()
    invalidate_agent_runtime_cache()

    return {"status": "updated"}
