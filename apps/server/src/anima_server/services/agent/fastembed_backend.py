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


_loaded: _Loaded | None = None
# Per-model failure latch: model name -> monotonic timestamp of the failure.
# Keyed by name (not a single process-wide slot) for two reasons: (1) a
# failure loading one model — e.g. a mistyped model name in the settings UI —
# must not block a different, correctly-named model from its own fresh load
# attempt (without this, fixing the typo still yielded no dense embeddings for
# the full 300s cooldown); (2) a SECOND model's failure must not overwrite the
# first's cooldown — switching back to an earlier bad name must still respect
# its remaining cooldown instead of immediately re-attempting the load.
# Written COPY-ON-WRITE under ``_lock`` (a whole new dict is rebound, never
# mutated in place) so a lock-free reader — the ``_load_model`` /
# ``backend_status`` / ``_cooldown_active`` fast path — always sees a
# consistent dict: under the GIL, rebinding the module-level name is atomic,
# so a reader observes either the fully-old or the fully-new dict, and a
# single ``.get(name)`` on either is itself atomic.
_failed: dict[str, float] = {}

_RETRY_TTL_SECONDS = 300.0


def _create_model(model_name: str) -> Any:
    from fastembed import TextEmbedding

    cache_dir = settings.data_dir / "models" / "fastembed"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return TextEmbedding(model_name=model_name, cache_dir=str(cache_dir))


def embed_texts(texts: list[str], *, model_name: str) -> list[list[float] | None]:
    global _failed
    # A model can LOAD fine but then raise at inference time. That is still a
    # failure of this model, so honor its cooldown here too — otherwise a
    # loaded-but-inference-failing model would be re-hit every call and, worse,
    # backend_status() would keep reporting "ready" (it only looks at the load
    # latch) while embeds actually return None.
    if _cooldown_active(model_name):
        return [None] * len(texts)
    model = _load_model(model_name)
    if model is None:
        return [None] * len(texts)
    try:
        return [list(map(float, vector)) for vector in model.embed(texts)]
    except Exception:
        logger.warning("fastembed inference failed; degrading to no dense arm", exc_info=True)
        # Latch the inference failure (copy-on-write, same as the load path)
        # so the cooldown above applies AND backend_status()/capabilities
        # report this model as failed_retrying rather than a false "ready".
        with _lock:
            _failed = {**_failed, model_name: time.monotonic()}
        return [None] * len(texts)


def _cooldown_active(model_name: str) -> bool:
    at = _failed.get(model_name)  # atomic single-key read of the current dict
    return at is not None and time.monotonic() - at < _RETRY_TTL_SECONDS


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
            # Copy-on-write clear of ONLY this model's latch — other models'
            # unrelated failure records must survive this success untouched,
            # so each stays blocked until its own cooldown or successful retry.
            if model_name in _failed:
                _failed = {k: v for k, v in _failed.items() if k != model_name}
        except Exception:
            # Copy-on-write add for THIS model (see _failed's comment for why
            # the whole-dict rebind, rather than an in-place mutation, is what
            # keeps the lock-free reads above safe) — a different model's
            # existing cooldown is preserved.
            _failed = {**_failed, model_name: time.monotonic()}
            logger.warning(
                "Failed to load fastembed model %r; dense retrieval will be "
                "unavailable until the model can be loaded.",
                model_name,
                exc_info=True,
            )
            return None
    # Return the model THIS call loaded, not a re-read of the global _loaded:
    # once the lock is released, another thread loading a DIFFERENT model can
    # rebind _loaded before we return, and reading it here would hand back the
    # wrong model's vectors for our requested name.
    return model


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
    at = _failed.get(current_model_name)  # atomic single-key read
    if at is not None and time.monotonic() - at < _RETRY_TTL_SECONDS:
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
        _failed = {}


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
    """Test-only helper to latch a load failure for ``name``.

    Copy-on-write add (not overwrite), so multiple models' latches coexist —
    mirroring ``_load_model``'s failure path and exercising the per-model
    cooldown behavior rather than a single overwritable slot.
    """
    global _failed
    with _lock:
        _failed = {**_failed, name: at}


__all__ = ["backend_status", "embed_texts", "warm_up_retrieval_models"]
