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
from dataclasses import dataclass
from typing import Any

from anima_server.config import settings

logger = logging.getLogger(__name__)

_model_lock = threading.Lock()


@dataclass(frozen=True)
class _Loaded:
    """A loaded model bound to the name it was loaded for.

    Mirrors ``fastembed_backend._Loaded`` — held as ONE module-level
    reference so a lock-free reader (the ``_load_model``/``backend_status``
    fast path) never observes a torn (name, model) pair from a concurrent
    model switch. See that class's docstring for the full rationale.
    """

    name: str
    model: Any


_loaded: _Loaded | None = None
_failed_at: float | None = None
# The model name the *current* _failed_at cooldown applies to. Keyed by name
# (mirrors fastembed_backend) so a failure loading one reranker model name
# cannot block a different, correctly-named one from getting a fresh attempt
# immediately — otherwise fixing a mistyped reranker model in settings still
# yields no reranking for the full 300s cooldown.
_failed_model_name: str | None = None

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


def _cooldown_active(model_name: str) -> bool:
    return (
        _failed_model_name == model_name
        and _failed_at is not None
        and time.monotonic() - _failed_at < _RETRY_TTL_SECONDS
    )


def _load_model() -> Any | None:
    global _loaded, _failed_at, _failed_model_name
    model_name = settings.retrieval_reranker_model
    loaded = _loaded  # single lock-free read of the atomic pair
    if loaded is not None and loaded.name == model_name:
        return loaded.model
    if _cooldown_active(model_name):
        return None
    with _model_lock:
        loaded = _loaded
        if loaded is not None and loaded.name == model_name:
            return loaded.model
        if _cooldown_active(model_name):
            return None
        try:
            model = _create_model()
            # Single reference swap — see _Loaded's docstring for why this
            # is what makes the fast path above safe to read lock-free.
            _loaded = _Loaded(name=model_name, model=model)
            # Only clear the latch if it belongs to *this* model name — a
            # different model's unrelated failure record must survive this
            # success untouched, so that model stays correctly blocked
            # until its own cooldown or its own successful retry.
            if _failed_model_name == model_name:
                _failed_at = None
                _failed_model_name = None
        except Exception:
            _failed_at = time.monotonic()
            _failed_model_name = model_name
            cache_dir = settings.data_dir / "models" / "fastembed"
            logger.warning(
                "Local reranker unavailable for %s (the model download to "
                "%s may not have completed); retrieval keeps the fused order",
                settings.retrieval_reranker_model,
                cache_dir,
                exc_info=True,
            )
            return None
    return _loaded.model


def backend_status() -> str:
    """Read-only snapshot of the local reranker model latch.

    Never triggers a load. Mirrors ``fastembed_backend.backend_status``:
    model-name-aware, not just "is *some* model loaded". A failed load for
    the currently-configured ``settings.retrieval_reranker_model`` reports
    ``"failed_retrying"`` (even if an older, differently-named model is
    still cached in ``_model``) while its TTL cooldown is active; ``"ready"``
    requires the cached model's name to match the current setting; anything
    else — never attempted, or a stale model cached under a different name
    with no active failure — is ``"cold"``. A failure latched for a
    DIFFERENT model name than the currently-configured one does not count
    here either — that would report the current model as failing when it
    has never actually been attempted.
    """
    model_name = settings.retrieval_reranker_model
    if (
        _failed_model_name == model_name
        and _failed_at is not None
        and time.monotonic() - _failed_at < _RETRY_TTL_SECONDS
    ):
        return "failed_retrying"
    loaded = _loaded
    if loaded is not None and loaded.name == model_name:
        return "ready"
    return "cold"


def _reset_model_cache_for_tests() -> None:
    global _loaded, _failed_at, _failed_model_name
    _loaded = None
    _failed_at = None
    _failed_model_name = None


def _set_loaded_for_tests(name: str, model: Any) -> None:
    """Test-only helper to simulate an already-loaded model.

    Goes through the same single-reference-swap path as ``_load_model`` —
    see ``fastembed_backend._set_loaded_for_tests``.
    """
    global _loaded
    with _model_lock:
        _loaded = _Loaded(name=name, model=model)


__all__ = ["backend_status", "rerank_chunk_ids"]
