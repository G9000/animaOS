"""Aggregates retrieval-stack capability/state for the trust-visibility surface.

A single read-only snapshot shared by the ``GET /api/capabilities`` route and
the ``retrieval_capabilities`` health check, so both surfaces agree on what
"healthy" means for the bundled document/retrieval stack. Collecting this
snapshot never triggers a model load, download, or network call — it only
reads latches and settings that other code paths already maintain.
"""

from __future__ import annotations

from typing import Any

from anima_server.config import resolve_embedding_dim, settings
from anima_server.services.agent.embeddings import (
    _resolve_embedding_model,
    _resolve_embedding_provider,
)
from anima_server.services.agent.fastembed_backend import (
    backend_status as fastembed_backend_status,
)
from anima_server.services.agent.llm import (
    LLMConfigError,
    resolve_background_chat_targets,
    validate_provider_configuration,
)
from anima_server.services.documents.parsing_pack import pack_status
from anima_server.services.documents.reranker import (
    backend_status as reranker_backend_status,
)


def _llm_configured() -> bool:
    """True iff a chat provider+model resolves to something usable.

    Reuses ``resolve_background_chat_targets`` — the same target-selection
    logic run before an actual background LLM call (extraction target first,
    falling back to the primary chat target; "scaffold"/"fastembed" and
    blank provider/model are already filtered out there). The
    highest-priority target's provider/API-key configuration is then
    validated with ``validate_provider_configuration``, the same config-shape
    check the LLM client itself runs before dispatching a request. This is a
    local, synchronous check — no network call is made, so "configured" means
    "this provider/model/key combination is well-formed enough to attempt a
    call," not "the provider is currently reachable."
    """
    targets = resolve_background_chat_targets()
    if not targets:
        return False
    try:
        validate_provider_configuration(targets[0].provider)
    except LLMConfigError:
        return False
    return True


def collect_capabilities() -> dict[str, Any]:
    """Snapshot the current state of the document/retrieval capability stack."""
    pack = pack_status()

    return {
        "parsingPack": {
            "state": pack.state,
            "progress": pack.progress,
            "error": pack.error,
        },
        "embeddings": {
            "provider": _resolve_embedding_provider(),
            "model": _resolve_embedding_model(),
            "dim": resolve_embedding_dim(),
            "backend": fastembed_backend_status(),
        },
        "reranker": {
            "enabled": settings.retrieval_reranker == "local",
            "model": settings.retrieval_reranker_model,
            "backend": reranker_backend_status(),
        },
        "llm": {"configured": _llm_configured()},
        "contextualChunks": settings.contextual_chunks == "on",
        "fullDocumentContext": settings.document_full_context == "auto",
    }


__all__ = ["collect_capabilities"]
