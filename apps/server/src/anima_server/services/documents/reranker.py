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
# Per-model failure latch (mirrors fastembed_backend): model name -> monotonic
# failure timestamp. Keyed by name, not a single slot, so (1) a failure
# loading one reranker model can't block a different, correctly-named one from
# a fresh attempt (fixing a mistyped model in settings works immediately), and
# (2) a second bad model's failure doesn't overwrite the first's cooldown.
# Written copy-on-write under ``_model_lock`` (whole-dict rebind, never mutated
# in place) so a lock-free reader (``_cooldown_active`` / ``backend_status``)
# always sees a consistent dict — GIL-atomic rebind, and a single ``.get``.
_failed: dict[str, float] = {}

_RETRY_TTL_SECONDS = 300.0


def rerank_chunk_ids(
    query: str,
    candidates: Sequence[tuple[int, str]],
) -> list[int] | None:
    """Rerank ``(chunk_id, text)`` candidates for *query*.

    Returns chunk ids in reranked order, or ``None`` when reranking is off
    or unavailable (callers keep the fused order).
    """
    global _failed
    if settings.retrieval_reranker != "local" or len(candidates) < 2:
        return None
    model_name = settings.retrieval_reranker_model
    # A model can LOAD fine but then raise at scoring time. Honor the cooldown
    # here too so a scoring-failing model isn't re-hit every query and, more
    # importantly, so backend_status() reports failed_retrying (it only checks
    # the load latch) instead of a false "ready".
    if _cooldown_active(model_name):
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
        # Latch the scoring failure (copy-on-write, same as the load path) so
        # the cooldown above applies and the trust surface reports it.
        with _model_lock:
            _failed = {**_failed, model_name: time.monotonic()}
        return None


def _create_model() -> Any:
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    cache_dir = settings.data_dir / "models" / "fastembed"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return TextCrossEncoder(
        model_name=settings.retrieval_reranker_model, cache_dir=str(cache_dir)
    )


def _cooldown_active(model_name: str) -> bool:
    at = _failed.get(model_name)  # atomic single-key read of the current dict
    return at is not None and time.monotonic() - at < _RETRY_TTL_SECONDS


def _load_model() -> Any | None:
    global _loaded, _failed
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
            # Copy-on-write clear of ONLY this model's latch — a different
            # model's unrelated failure record must survive this success.
            if model_name in _failed:
                _failed = {k: v for k, v in _failed.items() if k != model_name}
        except Exception:
            # Copy-on-write add for THIS model (see _failed's comment) — a
            # different model's existing cooldown is preserved.
            _failed = {**_failed, model_name: time.monotonic()}
            cache_dir = settings.data_dir / "models" / "fastembed"
            logger.warning(
                "Local reranker unavailable for %s (the model download to "
                "%s may not have completed); retrieval keeps the fused order",
                settings.retrieval_reranker_model,
                cache_dir,
                exc_info=True,
            )
            return None
    # Return the model THIS call loaded, not a re-read of the global _loaded:
    # after the lock is released, another thread loading a different reranker
    # model could rebind _loaded before we return, handing back the wrong
    # model here.
    return model


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
    at = _failed.get(model_name)  # atomic single-key read
    if at is not None and time.monotonic() - at < _RETRY_TTL_SECONDS:
        return "failed_retrying"
    loaded = _loaded
    if loaded is not None and loaded.name == model_name:
        return "ready"
    return "cold"


def _reset_model_cache_for_tests() -> None:
    global _loaded, _failed
    _loaded = None
    _failed = {}


def _set_loaded_for_tests(name: str, model: Any) -> None:
    """Test-only helper to simulate an already-loaded model.

    Goes through the same single-reference-swap path as ``_load_model`` —
    see ``fastembed_backend._set_loaded_for_tests``.
    """
    global _loaded
    with _model_lock:
        _loaded = _Loaded(name=name, model=model)


def _set_failed_for_tests(name: str, at: float) -> None:
    """Test-only helper to latch a load failure for ``name``.

    Copy-on-write add (mirrors ``fastembed_backend._set_failed_for_tests``),
    so multiple models' latches coexist rather than a single overwritable slot.
    """
    global _failed
    with _model_lock:
        _failed = {**_failed, name: at}


__all__ = ["backend_status", "rerank_chunk_ids"]
