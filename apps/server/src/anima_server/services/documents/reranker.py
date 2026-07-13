"""Optional local cross-encoder rerank stage for document retrieval.

Gated by ``ANIMA_RETRIEVAL_RERANKER`` (default off) and the ``reranker``
extra (sentence-transformers). Any unavailability — flag off, extra not
installed, model load or scoring failure — degrades to the fused order by
returning ``None``. Expect roughly 30-80ms per query for ~50 candidates on
CPU with the default BGE-reranker-v2-m3; the model loads lazily on first
use and is cached for the process lifetime.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from typing import Any

from anima_server.config import settings

logger = logging.getLogger(__name__)

_model_lock = threading.Lock()
_model: Any | None = None
_model_failed = False


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
        scores = model.predict([(query, text) for _chunk_id, text in candidates])
        ranked = sorted(
            zip(candidates, scores, strict=True),
            key=lambda pair: float(pair[1]),
            reverse=True,
        )
        return [chunk_id for (chunk_id, _text), _score in ranked]
    except Exception:
        logger.warning("Reranker scoring failed; using fused order", exc_info=True)
        return None


def _load_model() -> Any | None:
    global _model, _model_failed
    if _model is not None:
        return _model
    if _model_failed:
        return None
    with _model_lock:
        if _model is not None or _model_failed:
            return _model
        try:
            # Heavy import (torch); only paid when the flag is on.
            from sentence_transformers import CrossEncoder

            _model = CrossEncoder(settings.retrieval_reranker_model)
        except Exception:
            _model_failed = True
            logger.warning(
                "Local reranker unavailable (install the 'reranker' extra "
                "for %s); retrieval keeps the fused order",
                settings.retrieval_reranker_model,
                exc_info=True,
            )
            return None
    return _model


def _reset_model_cache_for_tests() -> None:
    global _model, _model_failed
    _model = None
    _model_failed = False


__all__ = ["rerank_chunk_ids"]
