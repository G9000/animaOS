"""Optional local cross-encoder rerank stage for document retrieval.

Gated by ``ANIMA_RETRIEVAL_RERANKER`` (default "local"). The model is a
bundled ONNX cross-encoder run through fastembed — no extra install, no
external service. Any unavailability — flag off, model load failure, or
scoring failure — degrades to the fused order by returning ``None``. Expect
roughly 30-80ms per query for ~50 candidates on CPU with the default
MiniLM-based reranker; the model loads lazily on first use and is cached
for the process lifetime.
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
    global _model, _model_failed
    if _model is not None:
        return _model
    if _model_failed:
        return None
    with _model_lock:
        if _model is not None or _model_failed:
            return _model
        try:
            _model = _create_model()
        except Exception:
            _model_failed = True
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


def _reset_model_cache_for_tests() -> None:
    global _model, _model_failed
    _model = None
    _model_failed = False


__all__ = ["rerank_chunk_ids"]
