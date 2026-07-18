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
from dataclasses import dataclass
from typing import Any

from anima_server.config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()


@dataclass(frozen=True)
class _Loaded:
    """A loaded model bound to the name it was loaded for.

    Held as ONE module-level reference (see ``_loaded`` below) so a
    lock-free reader (the ``_load_model``/``backend_status`` fast path)
    always observes a consistent (name, model) pair. Two separate globals
    (``_model`` and ``_model_name_loaded``, assigned one after the other
    under the lock) let a concurrent lock-free read land between the two
    assignments during a model switch — seeing the NEW model with the OLD
    name still in place — and silently serve the wrong model's vectors for
    the requested name. A single reference swap can't be observed
    mid-update: under the GIL, rebinding a module-level name is atomic, so
    any lock-free reader sees either the fully-old or the fully-new
    ``_Loaded`` instance, never a mix of the two.
    """

    name: str
    model: Any


@dataclass(frozen=True)
class _Failed:
    """A latched load failure bound to the model name it applies to.

    Held as ONE module-level reference (see ``_failed`` below), mirroring
    ``_Loaded`` above, so a lock-free reader (``_cooldown_active``/
    ``backend_status``) always observes a consistent (name, at) pair. Two
    separate globals (``_failed_at`` and ``_failed_model_name``, assigned one
    after the other under the lock) let a concurrent lock-free read pair a
    freshly-written timestamp with the STALE name still in place (or vice
    versa) during a failure-latch update for a different model — e.g.
    reporting model B as failing using model A's fresh timestamp. A single
    reference swap can't be observed mid-update for the same GIL-atomicity
    reason ``_Loaded`` relies on.
    """

    name: str
    at: float


_loaded: _Loaded | None = None
# Keyed by name (rather than a single process-wide latch) means a failure
# loading one model — e.g. a mistyped model name in the settings UI — cannot
# block a completely different, correctly-named model from getting its own
# fresh load attempt. Without this, fixing the typo still yielded no dense
# embeddings for the full 300s cooldown.
_failed: _Failed | None = None

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


def _cooldown_active(model_name: str) -> bool:
    failed = _failed  # single lock-free read of the atomic pair
    return (
        failed is not None
        and failed.name == model_name
        and time.monotonic() - failed.at < _RETRY_TTL_SECONDS
    )


def _load_model(model_name: str) -> Any | None:
    global _loaded, _failed
    loaded = _loaded  # single lock-free read of the atomic pair
    if loaded is not None and loaded.name == model_name:
        return loaded.model
    if _cooldown_active(model_name):
        return None
    with _lock:
        loaded = _loaded
        if loaded is not None and loaded.name == model_name:
            return loaded.model
        if _cooldown_active(model_name):
            return None
        try:
            model = _create_model(model_name)
            # Single reference swap — see _Loaded's docstring for why this
            # (rather than two separate globals) is what makes the fast
            # path above safe to read without the lock.
            _loaded = _Loaded(name=model_name, model=model)
            # Only clear the latch if it belongs to *this* model — a
            # different model's unrelated failure record must survive this
            # success untouched, so that model stays correctly blocked
            # until its own cooldown or its own successful retry.
            failed = _failed
            if failed is not None and failed.name == model_name:
                _failed = None
        except Exception:
            # Single reference swap — see _Failed's docstring for why this
            # (rather than two separate globals) is what makes the fast
            # path above safe to read without the lock.
            _failed = _Failed(name=model_name, at=time.monotonic())
            logger.warning(
                "Failed to load fastembed model %r; dense retrieval will be "
                "unavailable until the model can be loaded.",
                model_name,
                exc_info=True,
            )
            return None
    return _loaded.model


def _resolve_current_model_name() -> str:
    """Resolve the embedding model name that should be loaded right now.

    Imported lazily (module scope, not import time) to avoid a cycle:
    ``embeddings`` already imports ``embed_texts`` from this module lazily
    inside its functions, so keeping this edge lazy too means either module
    stays importable regardless of which one loads first.
    """
    from anima_server.services.agent.embeddings import _resolve_embedding_model

    return _resolve_embedding_model()


def backend_status() -> str:
    """Read-only snapshot of the in-process embedding model latch.

    Never triggers a load — purely observes the state ``_load_model`` already
    set, and is aware of *which* model is currently configured (not just
    whether some model happens to be loaded):

    - ``"failed_retrying"``: a load failure is latched *for the currently-
      resolved model name* and its TTL cooldown is still active. This takes
      priority even if a *different*, older model is still sitting in
      ``_model`` — that stale model must not be reported as backing the
      currently-configured one. A failure latched for some OTHER model name
      (e.g. a stale typo that's since been corrected in settings) must not
      make the current, never-attempted model report as failing — that
      would tell the UI dense embeddings are broken for a model that hasn't
      even been tried yet.
    - ``"ready"``: a model is loaded, no failure is latched, AND the loaded
      model name matches the currently-resolved one. A load of model A
      followed by a config switch to model B (with B's load still pending
      or its failure already past TTL) must NOT report "ready" — that would
      claim a model is healthy when it's actually never been attempted.
    - ``"cold"`` otherwise: never attempted, the cooldown lapsed without a
      retry happening yet (so a retry is implied on the next embed call), or
      the loaded model differs from the current one with no active failure.
    """
    current_model_name = _resolve_current_model_name()
    failed = _failed  # single lock-free read of the atomic pair
    if (
        failed is not None
        and failed.name == current_model_name
        and time.monotonic() - failed.at < _RETRY_TTL_SECONDS
    ):
        return "failed_retrying"
    loaded = _loaded
    if loaded is not None and loaded.name == current_model_name:
        return "ready"
    return "cold"


def warm_up_retrieval_models() -> None:
    """Load the bundled retrieval models off the request/chat path.

    Called once from the app lifespan (via ``asyncio.to_thread``) so the
    first chat or document query after startup does not pay for an ONNX
    model load (and, on a cold cache, a model download). Never raises —
    failures here just mean the latch above starts its retry-after-TTL
    cooldown, same as if the load had been triggered lazily.
    """
    from anima_server.services.agent.embeddings import (
        _resolve_embedding_model,
        _resolve_embedding_provider,
    )

    # Only warm up the embedding model if the provider is fastembed
    if _resolve_embedding_provider() == "fastembed":
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
    global _loaded, _failed
    with _lock:
        _loaded = None
        _failed = None


def _set_loaded_for_tests(name: str, model: Any) -> None:
    """Test-only helper to simulate an already-loaded model.

    Goes through the same single-reference-swap path as ``_load_model`` so
    tests exercise (and cannot bypass) the atomic-pair invariant described
    on ``_Loaded`` above, instead of poking at two separate globals that no
    longer exist as separate assignable attributes.
    """
    global _loaded
    with _lock:
        _loaded = _Loaded(name=name, model=model)


def _set_failed_for_tests(name: str, at: float) -> None:
    """Test-only helper to simulate a latched load failure.

    Mirrors ``_set_loaded_for_tests``: goes through the same single-
    reference-swap as ``_load_model``'s failure path so tests exercise (and
    cannot bypass) the atomic-pair invariant described on ``_Failed`` above,
    instead of poking at two separate globals that no longer exist as
    separate assignable attributes.
    """
    global _failed
    with _lock:
        _failed = _Failed(name=name, at=at)


__all__ = ["backend_status", "embed_texts", "warm_up_retrieval_models"]
