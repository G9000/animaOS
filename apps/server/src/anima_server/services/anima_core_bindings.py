from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any

logger = logging.getLogger(__name__)

try:
    import anima_core as _anima_core
except (ImportError, ModuleNotFoundError):
    _anima_core = None
except Exception:
    logger.warning(
        "anima_core bindings are unavailable due to an unexpected import failure",
        exc_info=True,
    )
    _anima_core = None


def is_available() -> bool:
    return _anima_core is not None


def has_binding(name: str) -> bool:
    return _anima_core is not None and hasattr(_anima_core, name)


def get_binding(name: str) -> Callable[..., Any] | None:
    if _anima_core is None:
        return None
    try:
        binding = getattr(_anima_core, name)
    except Exception:
        logger.warning("Failed to resolve anima_core.%s", name, exc_info=True)
        return None
    return binding if callable(binding) else None


def require_binding(name: str) -> Callable[..., Any]:
    binding = get_binding(name)
    if binding is None:
        raise RuntimeError(f"anima_core.{name} is unavailable")
    return binding


def get_binding_status() -> dict[str, object]:
    capabilities = {
        "text_processing": has_binding("normalize_text") and has_binding("fix_pdf_spacing"),
        "triplet_extraction": has_binding("extract_triplets"),
        "adaptive_retrieval": has_binding("find_adaptive_cutoff")
        and has_binding("normalize_scores"),
        "capsule": has_binding("read_capsule") and has_binding("write_capsule"),
        "retrieval_index": has_binding("memory_index_search")
        and has_binding("memory_index_vector_search")
        and has_binding("transcript_index_search")
        and has_binding("retrieval_manifest_status"),
        "search_helpers": has_binding("cosine_similarity")
        and has_binding("rrf_fuse")
        and has_binding("compute_heat"),
        "stateful_engine": has_binding("Engine")
        and has_binding("FrameStore")
        and has_binding("CardStore")
        and has_binding("KnowledgeGraph"),
    }
    return {
        "available": is_available(),
        "capabilities": capabilities,
        "degraded": not is_available(),
    }


rust_normalize_text = get_binding("normalize_text")
rust_fix_pdf_spacing = get_binding("fix_pdf_spacing")
rust_extract_triplets = get_binding("extract_triplets")
rust_find_adaptive_cutoff = get_binding("find_adaptive_cutoff")
rust_normalize_scores = get_binding("normalize_scores")
rust_read_capsule = get_binding("read_capsule")
rust_write_capsule = get_binding("write_capsule")
rust_cosine_similarity = get_binding("cosine_similarity")
rust_rrf_fuse = get_binding("rrf_fuse")
_rust_compute_heat_binding = get_binding("compute_heat")


def rust_compute_heat(
    *,
    access_count: int,
    interaction_depth: int,
    importance: float,
    seconds_since_access: float,
    superseded: bool = False,
) -> float:
    if _rust_compute_heat_binding is None:
        raise RuntimeError("anima_core.compute_heat is unavailable")
    return float(
        _rust_compute_heat_binding(
            int(access_count),
            int(interaction_depth),
            float(importance),
            float(seconds_since_access),
            bool(superseded),
        )
    )


if _rust_compute_heat_binding is None:
    rust_compute_heat = None  # type: ignore[assignment]


def normalize_score_list(scores: Sequence[float]) -> list[float] | None:
    if rust_normalize_scores is None:
        return None
    return [float(score) for score in rust_normalize_scores([float(score) for score in scores])]
