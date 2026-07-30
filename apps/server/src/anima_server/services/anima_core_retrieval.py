from __future__ import annotations

import contextlib
import json
import logging
import math
import os
from collections.abc import Iterable
from pathlib import Path
from threading import Lock

from anima_server.config import (
    default_runtime_app_data_root,
    resolve_runtime_path_outside_core,
    settings,
)
from anima_server.services import anima_core_bindings

logger = logging.getLogger(__name__)


_RETRIEVAL_MANIFEST_VERSION = 1
_RETRIEVAL_LOCK = Lock()


def _normalize_root(root: Path | str) -> Path:
    return Path(root)


def _manifest_path(root: Path | str) -> Path:
    return _normalize_root(root) / "manifest.json"


def _documents_path(root: Path | str, family: str) -> Path:
    return _normalize_root(root) / family / "documents.json"


def _ensure_root_exists(root: Path | str) -> None:
    _normalize_root(root).mkdir(parents=True, exist_ok=True)


def _normalize_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _normalize_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_str_list(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(value).strip() for value in values if str(value).strip()]


def _normalize_embedding(value: object) -> list[float]:
    if not isinstance(value, list):
        return []
    normalized: list[float] = []
    for item in value:
        try:
            normalized.append(float(item))
        except (TypeError, ValueError):
            return []
    return normalized


def _tokenize(text: str) -> list[str]:
    if not text:
        return []
    return [token for token in text.lower().split() if token.strip()]


def _safe_coerce_bool(value: object, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _load_json(path: Path) -> object | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_path, path)


def _family_manifest_entry(raw: object) -> dict[str, object]:
    family = raw if isinstance(raw, dict) else {}
    generation = _normalize_int(family.get("generation"), default=0)
    dirty = _safe_coerce_bool(family.get("dirty"), default=True)
    return {"generation": generation, "dirty": dirty}


def _normalize_manifest(raw: object, *, exists: bool) -> dict[str, object]:
    payload = raw if isinstance(raw, dict) else {}
    families_raw = payload.get("families", {})
    families = (
        {name: _family_manifest_entry(raw_status) for name, raw_status in families_raw.items()}
        if isinstance(families_raw, dict)
        else {}
    )
    return {
        "exists": exists,
        "version": _normalize_int(payload.get("version"), default=_RETRIEVAL_MANIFEST_VERSION),
        "families": {
            "memory": families.get(
                "memory", {"generation": 0, "dirty": True}
            ),
            "transcript": families.get(
                "transcript", {"generation": 0, "dirty": True}
            ),
        },
    }


def _read_manifest(root: Path | str) -> dict[str, object]:
    manifest_file = _manifest_path(root)
    return _normalize_manifest(
        _load_json(manifest_file),
        exists=manifest_file.is_file(),
    )


def _write_manifest(root: Path | str, manifest: dict[str, object]) -> None:
    manifest_file = _manifest_path(root)
    _write_json(manifest_file, manifest)


def _read_family_documents(root: Path | str, family: str) -> list[dict[str, object]]:
    path = _documents_path(root, family)
    raw_payload = _load_json(path)
    if not isinstance(raw_payload, list):
        return []

    docs: list[dict[str, object]] = []
    for item in raw_payload:
        if not isinstance(item, dict):
            continue
        docs.append(item)
    return docs


def _write_family_documents(
    root: Path | str,
    family: str,
    docs: Iterable[dict[str, object]],
) -> None:
    path = _documents_path(root, family)
    _write_json(path, list(docs))


def _coerce_memory_document(raw: object) -> dict[str, object] | None:
    if not isinstance(raw, dict):
        return None

    record_id = raw.get("record_id")
    user_id = raw.get("user_id")
    if record_id is None or user_id is None:
        return None

    normalized = {
        "record_id": _normalize_int(record_id),
        "user_id": _normalize_int(user_id),
        "text": str(raw.get("text", "")),
        "source_type": str(raw.get("source_type", "")),
        "category": str(raw.get("category", "")),
        "importance": _normalize_int(raw.get("importance"), default=0),
        "created_at": _normalize_int(raw.get("created_at"), default=0),
        "embedding": _normalize_embedding(raw.get("embedding")),
        "lexical_terms": _normalize_str_list(
            raw.get("lexical_terms")
        ),
    }
    if not normalized["lexical_terms"]:
        normalized["lexical_terms"] = _tokenize(normalized["text"])
    return normalized


def _coerce_transcript_document(raw: object) -> dict[str, object] | None:
    if not isinstance(raw, dict):
        return None

    thread_id = raw.get("thread_id")
    user_id = raw.get("user_id")
    if thread_id is None or user_id is None:
        return None

    normalized = {
        "thread_id": _normalize_int(thread_id),
        "user_id": _normalize_int(user_id),
        "transcript_ref": str(raw.get("transcript_ref", "")),
        "summary": str(raw.get("summary", "")),
        "keywords": _normalize_str_list(raw.get("keywords")),
        "text": str(raw.get("text", "")),
        "date_start": _normalize_int(raw.get("date_start"), default=0),
    }
    normalized["lexical_terms"] = _normalize_str_list(
        normalized["keywords"] + _tokenize(normalized["summary"]) + _tokenize(normalized["text"])
    )
    return normalized


def _coerce_memory_hit(doc: dict[str, object], score: float) -> dict[str, object]:
    return {
        "record_id": _normalize_int(doc.get("record_id"), default=0),
        "source_type": str(doc.get("source_type", "")),
        "category": str(doc.get("category", "")),
        "importance": _normalize_int(doc.get("importance"), default=0),
        "created_at": _normalize_int(doc.get("created_at"), default=0),
        "score": _normalize_float(score, default=0.0),
    }


def _coerce_transcript_hit(doc: dict[str, object], score: float) -> dict[str, object]:
    return {
        "thread_id": _normalize_int(doc.get("thread_id"), default=0),
        "user_id": _normalize_int(doc.get("user_id"), default=0),
        "transcript_ref": str(doc.get("transcript_ref", "")),
        "summary": str(doc.get("summary", "")),
        "keywords": _normalize_str_list(doc.get("keywords")),
        "date_start": _normalize_int(doc.get("date_start"), default=0),
        "score": _normalize_float(score, default=0.0),
    }


def _cosine_similarity(query: list[float], candidate: list[float]) -> float | None:
    if not query or not candidate or len(query) != len(candidate):
        return None
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for query_val, candidate_val in zip(query, candidate, strict=False):
        dot += query_val * candidate_val
        norm_a += query_val * query_val
        norm_b += candidate_val * candidate_val
    if norm_a <= 0.0 or norm_b <= 0.0:
        return None
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def _score_lexical(query_terms: set[str], terms: Iterable[str]) -> float:
    term_set = {term for term in terms if term}
    if not query_terms or not term_set:
        return 0.0
    return len(query_terms & term_set) / max(len(query_terms), 1)


def _mark_or_clear_family_dirty(
    *,
    root: Path | str,
    family: str,
    dirty: bool,
) -> None:
    manifest_path = _manifest_path(root)
    with _RETRIEVAL_LOCK:
        manifest = _read_manifest(root)
        families = manifest.setdefault("families", {})
        if not isinstance(families, dict):
            families = {}
            manifest["families"] = families
        family_state = families.get(family)
        if not isinstance(family_state, dict):
            family_state = {"generation": 0, "dirty": True}
        family_state = {
            "generation": _normalize_int(family_state.get("generation"), default=0),
            "dirty": _safe_coerce_bool(family_state.get("dirty"), default=False),
        }
        if not dirty:
            family_state["generation"] = family_state["generation"] + 1
            family_state["dirty"] = False
        else:
            family_state["dirty"] = True
        families[family] = family_state
        manifest["exists"] = manifest_path.is_file()
        manifest["version"] = _RETRIEVAL_MANIFEST_VERSION
        _write_manifest(root, manifest)


def _update_memory_index_doc(doc: dict[str, object], root: Path, *, upsert: bool) -> bool:
    docs = [_coerce_memory_document(item) for item in _read_family_documents(root, "memory")]
    docs = [item for item in docs if item is not None]
    changed = False
    record_id = _normalize_int(doc.get("record_id"))
    user_id = _normalize_int(doc.get("user_id"))
    new_docs: list[dict[str, object]] = []
    found = False
    for existing in docs:
        if (
            _normalize_int(existing.get("record_id")) == record_id
            and _normalize_int(existing.get("user_id")) == user_id
        ):
            found = True
            if upsert:
                new_docs.append(doc)
                changed = True
            else:
                changed = True
            continue
        new_docs.append(existing)
    if upsert and (not found):
        new_docs.append(doc)
        changed = True
    if changed:
        _write_family_documents(root, "memory", new_docs)
    return changed


def _update_transcript_index_doc(
    doc: dict[str, object],
    root: Path,
    *,
    upsert: bool,
) -> bool:
    docs = [_coerce_transcript_document(item) for item in _read_family_documents(root, "transcript")]
    docs = [item for item in docs if item is not None]
    changed = False
    thread_id = _normalize_int(doc.get("thread_id"))
    user_id = _normalize_int(doc.get("user_id"))
    transcript_ref = str(doc.get("transcript_ref", ""))
    new_docs: list[dict[str, object]] = []
    found = False
    for existing in docs:
        if (
            _normalize_int(existing.get("thread_id")) == thread_id
            and _normalize_int(existing.get("user_id")) == user_id
            and str(existing.get("transcript_ref")) == transcript_ref
        ):
            found = True
            if upsert:
                new_docs.append(doc)
                changed = True
            else:
                changed = True
            continue
        new_docs.append(existing)
    if upsert and (not found):
        new_docs.append(doc)
        changed = True
    if changed:
        _write_family_documents(root, "transcript", new_docs)
    return changed


def get_retrieval_root() -> Path:
    if settings.runtime_instance_data_dir:
        instance_root = resolve_runtime_path_outside_core(
            Path(settings.runtime_instance_data_dir),
            setting_name="ANIMA_RUNTIME_INSTANCE_DATA_DIR",
        )
        return instance_root / "cache" / "indices"
    app_data_root = resolve_runtime_path_outside_core(
        Path(settings.runtime_app_data_dir)
        if settings.runtime_app_data_dir
        else default_runtime_app_data_root(),
        setting_name="ANIMA_RUNTIME_APP_DATA_DIR",
    )
    return app_data_root / "unbound" / "cache" / "indices"


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


def _has_binding(name: str) -> bool:
    return anima_core_bindings.has_binding(name)


def _require_binding(name: str):
    return anima_core_bindings.require_binding(name)


def retrieval_manifest_status(*, root: Path | str) -> dict[str, object]:
    if _has_binding("retrieval_manifest_status"):
        try:
            binding = _require_binding("retrieval_manifest_status")
            result = binding(str(root))
            return dict(result)
        except Exception:
            logger.debug("Fallback to Python manifest management for %s", root)

    manifest = _read_manifest(root)
    manifest["exists"] = _manifest_path(root).is_file()
    return manifest


def is_retrieval_family_dirty(*, root: Path | str, family: str) -> bool:
    if _has_binding("retrieval_manifest_status"):
        try:
            return bool(
                retrieval_manifest_status(root=root).get("families", {})
                .get(family, {})
                .get("dirty")
            )
        except Exception:
            logger.debug("Failed to read Rust retrieval manifest for %s", root)
    manifest = _read_manifest(root)
    status = manifest.get("families", {}).get(family, {})
    if not isinstance(status, dict):
        return True
    if "dirty" not in status:
        return True
    return bool(status.get("dirty", True))


def mark_retrieval_index_dirty(*, root: Path | str, family: str) -> None:
    if _has_binding("mark_retrieval_index_dirty"):
        with contextlib.suppress(Exception):
            _require_binding("mark_retrieval_index_dirty")(str(root), family)
            return
    _mark_or_clear_family_dirty(root=root, family=family, dirty=True)


def clear_retrieval_index_dirty(*, root: Path | str, family: str) -> None:
    if _has_binding("clear_retrieval_index_dirty"):
        with contextlib.suppress(Exception):
            _require_binding("clear_retrieval_index_dirty")(str(root), family)
            return
    _mark_or_clear_family_dirty(root=root, family=family, dirty=False)


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
    root_path = _normalize_root(root)
    try:
        if _has_binding("memory_index_upsert"):
            binding = _require_binding("memory_index_upsert")
            binding(
                str(root_path),
                int(record_id),
                int(user_id),
                text,
                source_type,
                category,
                int(importance),
                int(created_at),
                list(embedding) if embedding is not None else None,
            )
            return
    except Exception:
        logger.debug(
            "Rust memory_index_upsert unavailable, falling back to Python",
            exc_info=True,
        )

    normalized = {
        "record_id": int(record_id),
        "user_id": int(user_id),
        "text": text,
        "source_type": source_type,
        "category": category,
        "importance": int(importance),
        "created_at": int(created_at),
        "embedding": (
            list(embedding) if isinstance(embedding, Iterable) and embedding is not None else []
        ),
    }
    normalized["lexical_terms"] = _tokenize(text)
    _ensure_root_exists(root_path)
    with _RETRIEVAL_LOCK:
        _update_memory_index_doc(_coerce_memory_document(normalized) or normalized, root_path, upsert=True)


def memory_index_delete(*, root: Path | str, record_id: int, user_id: int) -> bool:
    if _has_binding("memory_index_delete"):
        try:
            binding = _require_binding("memory_index_delete")
            return bool(binding(str(root), int(record_id), int(user_id)))
        except Exception:
            logger.debug(
                "Rust memory_index_delete unavailable, falling back to Python",
                exc_info=True,
            )

    record_id_value = int(record_id)
    user_id_value = int(user_id)
    docs = [_coerce_memory_document(item) for item in _read_family_documents(root, "memory")]
    docs = [item for item in docs if item is not None]
    kept = [
        item
        for item in docs
        if not (
            _normalize_int(item.get("record_id"), default=-1) == record_id_value
            and _normalize_int(item.get("user_id"), default=-1) == user_id_value
        )
    ]
    removed = len(kept) != len(docs)
    with _RETRIEVAL_LOCK:
        _write_family_documents(root, "memory", kept)
    return removed


def memory_index_delete_user_documents(*, root: Path | str, user_id: int) -> int:
    if _has_binding("memory_index_delete_user_documents"):
        try:
            binding = _require_binding("memory_index_delete_user_documents")
            return int(binding(str(root), int(user_id)))
        except Exception:
            logger.debug(
                "Rust memory_index_delete_user_documents unavailable, falling back",
                exc_info=True,
            )

    user_id_value = int(user_id)
    docs = [_coerce_memory_document(item) for item in _read_family_documents(root, "memory")]
    docs = [item for item in docs if item is not None]
    kept = [
        item
        for item in docs
        if _normalize_int(item.get("user_id"), default=-1) != user_id_value
    ]
    removed = len(docs) - len(kept)
    with _RETRIEVAL_LOCK:
        _write_family_documents(root, "memory", kept)
    return removed


def reset_memory_index(*, root: Path | str) -> None:
    if _has_binding("reset_memory_index"):
        with contextlib.suppress(Exception):
            _require_binding("reset_memory_index")(str(root))
            return
    path = _documents_path(root, "memory")
    if path.is_file():
        path.unlink(missing_ok=True)


def memory_index_search(
    *,
    root: Path | str,
    user_id: int,
    query: str,
    limit: int,
) -> list[dict[str, object]]:
    if _has_binding("memory_index_search"):
        try:
            binding = _require_binding("memory_index_search")
            hits = binding(str(root), int(user_id), query, int(limit))
            return [dict(hit) for hit in hits]
        except Exception:
            logger.debug(
                "Rust memory_index_search unavailable, falling back to Python",
                exc_info=True,
            )

    query_terms = set(_tokenize(query))
    if not query_terms:
        return []
    docs = [_coerce_memory_document(item) for item in _read_family_documents(root, "memory")]
    user_id_value = int(user_id)
    ranked: list[tuple[float, dict[str, object]]] = []
    for doc in docs:
        if doc is None or _normalize_int(doc.get("user_id"), default=-1) != user_id_value:
            continue
        score = _score_lexical(query_terms, doc.get("lexical_terms", []))
        if score <= 0.0:
            continue
        ranked.append((score, _coerce_memory_hit(doc, score)))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [hit for _score, hit in ranked][:max(int(limit), 0)]


def memory_index_vector_search(
    *,
    root: Path | str,
    user_id: int,
    query_embedding: list[float],
    limit: int,
) -> list[dict[str, object]]:
    if _has_binding("memory_index_vector_search"):
        try:
            binding = _require_binding("memory_index_vector_search")
            hits = binding(str(root), int(user_id), list(query_embedding), int(limit))
            return [dict(hit) for hit in hits]
        except Exception:
            logger.debug(
                "Rust memory_index_vector_search unavailable, falling back to Python",
                exc_info=True,
            )

    normalized_query = _normalize_embedding(query_embedding)
    if not normalized_query:
        return []
    docs = [_coerce_memory_document(item) for item in _read_family_documents(root, "memory")]
    user_id_value = int(user_id)
    ranked: list[tuple[float, dict[str, object]]] = []
    for doc in docs:
        if doc is None or _normalize_int(doc.get("user_id"), default=-1) != user_id_value:
            continue
        doc_embedding = _normalize_embedding(doc.get("embedding"))
        cosine = _cosine_similarity(normalized_query, doc_embedding)
        if cosine is None:
            continue
        # Score contract: raw cosine similarity clamped to [0, 1], higher is
        # better — matching the rust native index (`retrieval_index.rs` uses
        # `simd::cosine_similarity` directly) and the pgvector backend
        # (`1 - cosine_distance`).  The previous `(cosine + 1) / 2` remap
        # compressed [0, 1] cosine into [0.5, 1], so a shared
        # `similarity_threshold` gated this backend far more loosely than the
        # others.
        similarity_score = max(0.0, cosine)
        ranked.append((similarity_score, _coerce_memory_hit(doc, similarity_score)))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [hit for _score, hit in ranked][:max(int(limit), 0)]


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
    root_path = _normalize_root(root)
    try:
        if _has_binding("transcript_index_upsert"):
            binding = _require_binding("transcript_index_upsert")
            binding(
                str(root_path),
                int(thread_id),
                int(user_id),
                transcript_ref,
                summary,
                list(keywords),
                text,
                int(date_start),
            )
            return
    except Exception:
        logger.debug(
            "Rust transcript_index_upsert unavailable, falling back to Python",
            exc_info=True,
        )

    normalized = {
        "thread_id": int(thread_id),
        "user_id": int(user_id),
        "transcript_ref": transcript_ref,
        "summary": summary,
        "keywords": _normalize_str_list(keywords),
        "text": text,
        "date_start": int(date_start),
    }
    _ensure_root_exists(root_path)
    with _RETRIEVAL_LOCK:
        _update_transcript_index_doc(
            _coerce_transcript_document(normalized) or normalized,
            root_path,
            upsert=True,
        )


def transcript_index_delete(*, root: Path | str, thread_id: int, user_id: int) -> bool:
    if _has_binding("transcript_index_delete"):
        try:
            binding = _require_binding("transcript_index_delete")
            return bool(binding(str(root), int(thread_id), int(user_id)))
        except Exception:
            logger.debug(
                "Rust transcript_index_delete unavailable, falling back to Python",
                exc_info=True,
            )

    thread_id_value = int(thread_id)
    user_id_value = int(user_id)
    docs = [
        item for item in (_coerce_transcript_document(item) for item in _read_family_documents(root, "transcript")) if item is not None
    ]
    kept = [
        item
        for item in docs
        if not (
            _normalize_int(item.get("thread_id"), default=-1) == thread_id_value
            and _normalize_int(item.get("user_id"), default=-1) == user_id_value
        )
    ]
    deleted = len(kept) != len(docs)
    with _RETRIEVAL_LOCK:
        _write_family_documents(root, "transcript", kept)
    return deleted


def transcript_index_delete_user_documents(*, root: Path | str, user_id: int) -> int:
    if _has_binding("transcript_index_delete_user_documents"):
        try:
            binding = _require_binding("transcript_index_delete_user_documents")
            return int(binding(str(root), int(user_id)))
        except Exception:
            logger.debug(
                "Rust transcript_index_delete_user_documents unavailable, falling back",
                exc_info=True,
            )

    user_id_value = int(user_id)
    docs = [
        item
        for item in (_coerce_transcript_document(item) for item in _read_family_documents(root, "transcript"))
        if item is not None
    ]
    kept = [
        item
        for item in docs
        if _normalize_int(item.get("user_id"), default=-1) != user_id_value
    ]
    removed = len(docs) - len(kept)
    with _RETRIEVAL_LOCK:
        _write_family_documents(root, "transcript", kept)
    return removed


def reset_transcript_index(*, root: Path | str) -> None:
    if _has_binding("reset_transcript_index"):
        with contextlib.suppress(Exception):
            _require_binding("reset_transcript_index")(str(root))
            return
    path = _documents_path(root, "transcript")
    if path.is_file():
        path.unlink(missing_ok=True)


def transcript_index_search(
    *,
    root: Path | str,
    user_id: int,
    query: str,
    limit: int,
) -> list[dict[str, object]]:
    if _has_binding("transcript_index_search"):
        try:
            binding = _require_binding("transcript_index_search")
            hits = binding(str(root), int(user_id), query, int(limit))
            return [dict(hit) for hit in hits]
        except Exception:
            logger.debug(
                "Rust transcript_index_search unavailable, falling back to Python",
                exc_info=True,
            )

    query_terms = set(_tokenize(query))
    if not query_terms:
        return []
    docs = [
        item
        for item in (_coerce_transcript_document(item) for item in _read_family_documents(root, "transcript"))
        if item is not None
    ]
    user_id_value = int(user_id)
    ranked: list[tuple[float, dict[str, object]]] = []
    for doc in docs:
        if _normalize_int(doc.get("user_id"), default=-1) != user_id_value:
            continue
        lexical_terms = list(doc.get("lexical_terms", []))
        if not lexical_terms:
            lexical_terms = _tokenize(
                f"{doc.get('summary', '')} {doc.get('text', '')} {' '.join(doc.get('keywords', []))}"
            )
        score = _score_lexical(query_terms, lexical_terms)
        if score <= 0.0:
            continue
        ranked.append((score, _coerce_transcript_hit(doc, score)))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [hit for _score, hit in ranked][:max(int(limit), 0)]
