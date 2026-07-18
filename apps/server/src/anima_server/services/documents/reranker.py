"""Optional local cross-encoder rerank stage for document retrieval.

Gated by ``ANIMA_RETRIEVAL_RERANKER`` (default "local"). The model is a
bundled ONNX cross-encoder run through fastembed — no extra install, no
external service. Any unavailability — flag off, model load failure, or
scoring failure — degrades to the fused order by returning ``None``. Expect
roughly 30-80ms per query for ~50 candidates on CPU with the default
MiniLM-based reranker; the model loads lazily on first use (or during the
startup warm-up) and is cached for the process lifetime. A load failure
starts a retry-after-TTL cooldown — 300s, 10x the HTTP provider cooldown,
since model loads are heavier than HTTP probes — rather than latching
permanently for the process.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Sequence
from typing import Any

from anima_server.config import settings

logger = logging.getLogger(__name__)

_model_lock = threading.Lock()
_model: Any | None = None
_model_name_loaded: str | None = None
_failed_at: float | None = None

_RETRY_TTL_SECONDS = 300.0


def rerank_chunk_ids(
    query: str,
    candidates: Sequence[tuple[int, str]],
) -> list[int] | None:
    """Rerank ``(chunk_id, text)`` candidates for *query*.

    Returns chunk ids in reranked order, or ``None`` when reranking is off
    or unavailable (callers keep the fused order).
    """
    if settings.retrieval_reranker != "local" or len(candidates) < 2:
        return None
    model = _load_model()
    if model is None:
        return None
    try:
        scores = list(model.rerank(query, [text for _chunk_id, text in candidates]))
        ranked = sorted(
            zip(candidates, scores, strict=True),
            key=lambda pair: float(pair[1]),
            reverse=True,
        )
        return [chunk_id for (chunk_id, _text), _score in ranked]
    except Exception:
        logger.warning("Reranker scoring failed; using fused order", exc_info=True)
        return None


def _create_model() -> Any:
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    cache_dir = settings.data_dir / "models" / "fastembed"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return TextCrossEncoder(
        model_name=settings.retrieval_reranker_model, cache_dir=str(cache_dir)
    )


def _load_model() -> Any | None:
    global _model, _model_name_loaded, _failed_at
    model_name = settings.retrieval_reranker_model
    if _model is not None and _model_name_loaded == model_name:
        return _model
    if _failed_at is not None and time.monotonic() - _failed_at < _RETRY_TTL_SECONDS:
        return None
    with _model_lock:
        if _model is not None and _model_name_loaded == model_name:
            return _model
        if (
            _failed_at is not None
            and time.monotonic() - _failed_at < _RETRY_TTL_SECONDS
        ):
            return None
        try:
            _model = _create_model()
            _model_name_loaded = model_name
            _failed_at = None
        except Exception:
            _failed_at = time.monotonic()
            cache_dir = settings.data_dir / "models" / "fastembed"
            logger.warning(
                "Local reranker unavailable for %s (the model download to "
                "%s may not have completed); retrieval keeps the fused order",
                settings.retrieval_reranker_model,
                cache_dir,
                exc_info=True,
            )
            return None
    return _model


def backend_status() -> str:
    """Read-only snapshot of the local reranker model latch.

    Never triggers a load. Mirrors ``fastembed_backend.backend_status``:
    model-name-aware, not just "is *some* model loaded". A failed load for
    the currently-configured ``settings.retrieval_reranker_model`` reports
    ``"failed_retrying"`` (even if an older, differently-named model is
    still cached in ``_model``) while its TTL cooldown is active; ``"ready"``
    requires the cached model's name to match the current setting; anything
    else — never attempted, or a stale model cached under a different name
    with no active failure — is ``"cold"``.
    """
    if _failed_at is not None and time.monotonic() - _failed_at < _RETRY_TTL_SECONDS:
        return "failed_retrying"
    if _model is not None and _model_name_loaded == settings.retrieval_reranker_model:
        return "ready"
    return "cold"


def _reset_model_cache_for_tests() -> None:
    global _model, _model_name_loaded, _failed_at
    _model = None
    _model_name_loaded = None
    _failed_at = None


__all__ = ["backend_status", "rerank_chunk_ids"]
