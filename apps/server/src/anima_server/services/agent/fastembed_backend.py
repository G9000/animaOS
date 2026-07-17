"""Bundled in-process embedding backend (fastembed / ONNX Runtime).

This is the provider that makes dense retrieval work with zero setup and no
external services: models are ONNX, run on CPU, and cache under the app data
dir. Failure never raises to callers — a load or inference error logs once,
latches a failed flag, and yields ``None`` vectors, the same degradation
contract the HTTP providers have during an outage.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from anima_server.config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_model: Any | None = None
_model_name_loaded: str | None = None
_failed = False


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
    global _model, _model_name_loaded, _failed
    if _model is not None and _model_name_loaded == model_name:
        return _model
    if _failed:
        return None
    with _lock:
        if _model is not None and _model_name_loaded == model_name:
            return _model
        if _failed:
            return None
        try:
            _model = _create_model(model_name)
            _model_name_loaded = model_name
        except Exception:
            _failed = True
            logger.warning(
                "Failed to load fastembed model %r; dense retrieval will be "
                "unavailable until the model can be loaded.",
                model_name,
                exc_info=True,
            )
            return None
    return _model


def _reset_backend_for_tests() -> None:
    global _model, _model_name_loaded, _failed
    with _lock:
        _model = None
        _model_name_loaded = None
        _failed = False


__all__ = ["embed_texts"]
