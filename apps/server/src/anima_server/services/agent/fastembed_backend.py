"""Bundled in-process embedding backend (fastembed / ONNX Runtime).

This is the provider that makes dense retrieval work with zero setup and no
external services: models are ONNX, run on CPU, and cache under the app data
dir. Failure never raises to callers — a load or inference error logs once
and starts a retry-after-TTL cooldown, yielding ``None`` vectors for that
window instead of hammering a broken load on every call. Model loads are
heavier than HTTP probes, so the cooldown is 10x the HTTP provider cooldown
(30s): a laptop that starts offline recovers dense retrieval within 5
minutes of connectivity instead of requiring a process restart.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from anima_server.config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_model: Any | None = None
_model_name_loaded: str | None = None
_failed_at: float | None = None

_RETRY_TTL_SECONDS = 300.0


def _create_model(model_name: str) -> Any:
    from fastembed import TextEmbedding

    cache_dir = settings.data_dir / "models" / "fastembed"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return TextEmbedding(model_name=model_name, cache_dir=str(cache_dir))


def embed_texts(texts: list[str], *, model_name: str) -> list[list[float] | None]:
    model = _load_model(model_name)
    if model is None:
        return [None] * len(texts)
    try:
        return [list(map(float, vector)) for vector in model.embed(texts)]
    except Exception:
        logger.warning("fastembed inference failed; degrading to no dense arm", exc_info=True)
        return [None] * len(texts)


def _load_model(model_name: str) -> Any | None:
    global _model, _model_name_loaded, _failed_at
    if _model is not None and _model_name_loaded == model_name:
        return _model
    if _failed_at is not None and time.monotonic() - _failed_at < _RETRY_TTL_SECONDS:
        return None
    with _lock:
        if _model is not None and _model_name_loaded == model_name:
            return _model
        if (
            _failed_at is not None
            and time.monotonic() - _failed_at < _RETRY_TTL_SECONDS
        ):
            return None
        try:
            _model = _create_model(model_name)
            _model_name_loaded = model_name
            _failed_at = None
        except Exception:
            _failed_at = time.monotonic()
            logger.warning(
                "Failed to load fastembed model %r; dense retrieval will be "
                "unavailable until the model can be loaded.",
                model_name,
                exc_info=True,
            )
            return None
    return _model


def warm_up_retrieval_models() -> None:
    """Load the bundled retrieval models off the request/chat path.

    Called once from the app lifespan (via ``asyncio.to_thread``) so the
    first chat or document query after startup does not pay for an ONNX
    model load (and, on a cold cache, a model download). Never raises —
    failures here just mean the latch above starts its retry-after-TTL
    cooldown, same as if the load had been triggered lazily.
    """
    from anima_server.services.agent.embeddings import _resolve_embedding_model

    try:
        _load_model(_resolve_embedding_model())
    except Exception:
        logger.warning("Embedding model warm-up failed", exc_info=True)

    if settings.retrieval_reranker == "local":
        try:
            from anima_server.services.documents.reranker import (
                _load_model as _load_reranker_model,
            )

            _load_reranker_model()
        except Exception:
            logger.warning("Reranker model warm-up failed", exc_info=True)


def _reset_backend_for_tests() -> None:
    global _model, _model_name_loaded, _failed_at
    with _lock:
        _model = None
        _model_name_loaded = None
        _failed_at = None


__all__ = ["embed_texts", "warm_up_retrieval_models"]
