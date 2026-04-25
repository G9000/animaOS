from __future__ import annotations

import logging
from pathlib import Path

from anima_server.config import settings
from anima_server.services import anima_core_bindings

logger = logging.getLogger(__name__)


def _has_binding(name: str) -> bool:
    return anima_core_bindings.has_binding(name)


def _require_binding(name: str):
    return anima_core_bindings.require_binding(name)


def get_retrieval_root() -> Path:
    return settings.data_dir / "indices"


def get_retrieval_status() -> dict[str, object]:
    available = anima_core_bindings.is_available()
    capabilities = {
        "memory_index": _has_binding("memory_index_search"),
        "memory_vector_index": _has_binding("memory_index_vector_search"),
        "transcript_index": _has_binding("transcript_index_search"),
        "manifest": _has_binding("retrieval_manifest_status"),
        "dirty_control": _has_binding("clear_retrieval_index_dirty"),
    }
    return {
        "available": available,
        "capabilities": capabilities,
        "degraded": not available,
    }


def retrieval_manifest_status(*, root: Path | str) -> dict[str, object]:
    binding = _require_binding("retrieval_manifest_status")
    result = binding(str(root))
    return dict(result)


def is_retrieval_family_dirty(*, root: Path | str, family: str) -> bool:
    manifest = retrieval_manifest_status(root=root)
    families = manifest.get("families")
    if not isinstance(families, dict):
        return False
    family_status = families.get(family)
    if not isinstance(family_status, dict):
        return False
    return bool(family_status.get("dirty"))


def mark_retrieval_index_dirty(*, root: Path | str, family: str) -> None:
    binding = _require_binding("mark_retrieval_index_dirty")
    binding(str(root), family)


def clear_retrieval_index_dirty(*, root: Path | str, family: str) -> None:
    binding = _require_binding("clear_retrieval_index_dirty")
    binding(str(root), family)


def memory_index_upsert(
    *,
    root: Path | str,
    record_id: int,
    user_id: int,
    text: str,
    embedding: list[float] | None = None,
    source_type: str,
    category: str,
    importance: int,
    created_at: int,
) -> None:
    binding = _require_binding("memory_index_upsert")
    binding(
        str(root),
        int(record_id),
        int(user_id),
        text,
        source_type,
        category,
        int(importance),
        int(created_at),
        list(embedding) if embedding is not None else None,
    )


def memory_index_delete(*, root: Path | str, record_id: int, user_id: int) -> bool:
    binding = _require_binding("memory_index_delete")
    return bool(binding(str(root), int(record_id), int(user_id)))


def memory_index_delete_user_documents(*, root: Path | str, user_id: int) -> int:
    binding = _require_binding("memory_index_delete_user_documents")
    return int(binding(str(root), int(user_id)))


def reset_memory_index(*, root: Path | str) -> None:
    binding = _require_binding("reset_memory_index")
    binding(str(root))


def memory_index_search(
    *,
    root: Path | str,
    user_id: int,
    query: str,
    limit: int,
) -> list[dict[str, object]]:
    binding = _require_binding("memory_index_search")
    hits = binding(str(root), int(user_id), query, int(limit))
    return [dict(hit) for hit in hits]


def memory_index_vector_search(
    *,
    root: Path | str,
    user_id: int,
    query_embedding: list[float],
    limit: int,
) -> list[dict[str, object]]:
    binding = _require_binding("memory_index_vector_search")
    hits = binding(str(root), int(user_id), list(query_embedding), int(limit))
    return [dict(hit) for hit in hits]


def transcript_index_upsert(
    *,
    root: Path | str,
    thread_id: int,
    user_id: int,
    transcript_ref: str,
    summary: str,
    keywords: list[str],
    text: str,
    date_start: int,
) -> None:
    binding = _require_binding("transcript_index_upsert")
    binding(
        str(root),
        int(thread_id),
        int(user_id),
        transcript_ref,
        summary,
        list(keywords),
        text,
        int(date_start),
    )


def transcript_index_delete(*, root: Path | str, thread_id: int, user_id: int) -> bool:
    binding = _require_binding("transcript_index_delete")
    return bool(binding(str(root), int(thread_id), int(user_id)))


def transcript_index_delete_user_documents(*, root: Path | str, user_id: int) -> int:
    binding = _require_binding("transcript_index_delete_user_documents")
    return int(binding(str(root), int(user_id)))


def reset_transcript_index(*, root: Path | str) -> None:
    binding = _require_binding("reset_transcript_index")
    binding(str(root))


def transcript_index_search(
    *,
    root: Path | str,
    user_id: int,
    query: str,
    limit: int,
) -> list[dict[str, object]]:
    binding = _require_binding("transcript_index_search")
    hits = binding(str(root), int(user_id), query, int(limit))
    return [dict(hit) for hit in hits]
