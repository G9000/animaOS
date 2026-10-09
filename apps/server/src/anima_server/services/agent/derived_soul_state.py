"""Disposable, unlock-scoped vectors rebuilt from retained Soul text.

These properties deliberately are not SQLAlchemy columns. A Core switch,
lock, or embedding configuration change discards every derived vector.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
from threading import RLock
from typing import Any
from weakref import WeakKeyDictionary

from anima_server.services.agent.embedding_integrity import (
    compute_embedding_checksum,
    parse_embedding_payload,
)

logger = logging.getLogger(__name__)
_lock = RLock()
_vectors: dict[tuple[str, int, str, int], tuple[str, tuple[float, ...] | None, str | None]] = {}
_pending: WeakKeyDictionary = WeakKeyDictionary()
_active_root: str | None = None
_epoch = 0


def clear_derived_soul_state(*, owner_id: int | None = None) -> None:
    """Drop vectors, including pending ORM constructor values, on final lock."""
    global _epoch
    with _lock:
        _epoch += 1
        for key in list(_vectors):
            if owner_id is None or key[1] == owner_id:
                del _vectors[key]
        for row in list(_pending):
            if owner_id is None or row.user_id == owner_id:
                del _pending[row]


def _core_root() -> str:
    from anima_server.services.core import get_core_dir

    return os.path.normcase(str(get_core_dir().resolve()))


def _capture_row_origin(target, _context=None) -> None:
    target._derived_origin_root = _core_root()


def _scope(row: Any) -> tuple[str, int, str, int] | None:
    global _active_root
    from anima_server.config import settings
    from anima_server.services.sessions import active_unlock_sessions

    root = _core_root()
    # Session lookup can purge expired sessions and clear this cache. It must
    # happen outside the cache lock to avoid inverting the session/cache locks.
    if settings.core_require_encryption and not active_unlock_sessions(row.user_id):
        clear_derived_soul_state(owner_id=row.user_id)
        return None
    with _lock:
        if _active_root != root:
            clear_derived_soul_state()
            _active_root = root
    origin = getattr(row, "_derived_origin_root", None)
    if origin is None:
        _capture_row_origin(row)
    elif origin != root:
        return None
    if row.id is None:
        return None
    return root, row.user_id, row.__tablename__, row.id


def _signature(row: Any) -> str:
    from anima_server.config import settings
    from anima_server.services.agent.embedding_resolution import configured_embedding_fingerprint

    fields = {
        "memory_items": ("content",),
        "kg_entities": ("name", "entity_type", "description"),
        "agent_experiences": ("task_intent",),
        "agent_skills": ("description",),
    }[row.__tablename__]
    payload = repr(
        (
            configured_embedding_fingerprint(settings),
            tuple(getattr(row, field, None) for field in fields),
        )
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _text(row: Any) -> str:
    from anima_server.services.data_crypto import df

    if row.__tablename__ == "kg_entities":
        from anima_server.services.agent.knowledge_graph import _entity_embedding_text

        return _entity_embedding_text(
            name=row.name, entity_type=row.entity_type, description=row.description or ""
        )
    field = {
        "memory_items": "content",
        "agent_experiences": "task_intent",
        "agent_skills": "description",
    }[row.__tablename__]
    return df(row.user_id, getattr(row, field) or "", table=row.__tablename__, field=field)


def _entry(row: Any, *, rebuild: bool = True):
    key = _scope(row)
    if key is None:
        return None
    signature = _signature(row)
    with _lock:
        pending = _pending.pop(row, None)
        if pending is not None and pending[0] == key[0] and pending[1] == signature:
            _vectors[key] = pending[2]
        entry = _vectors.get(key)
        if entry is not None and entry[0] == signature:
            return entry
        _vectors.pop(key, None)
        epoch = _epoch
    if not rebuild or (row.__tablename__ == "memory_items" and row.distilled_at is not None):
        return None
    try:
        from anima_server.services.agent.embeddings import generate_embedding
        from anima_server.services.agent.knowledge_graph import _run_async_blocking

        embedding = parse_embedding_payload(_run_async_blocking(generate_embedding(_text(row))))
    except Exception:
        logger.debug("Derived Soul vector rebuild unavailable", exc_info=True)
        return None
    if embedding is None or _scope(row) != key or _signature(row) != signature:
        return None
    entry = (signature, tuple(embedding), compute_embedding_checksum(embedding))
    with _lock:
        if epoch != _epoch:
            return None
        _vectors[key] = entry
    return entry


def _publish_constructor_embedding(_mapper, _connection, target) -> None:
    # Once the insert has assigned a durable ID, the weak constructor entry
    # must move into the scoped cache even if the caller releases the row.
    _entry(target, rebuild=False)


class DerivedSoulEmbedding:
    """Compatibility vector API whose state never participates in Soul SQL."""

    @classmethod
    def __declare_last__(cls) -> None:
        from sqlalchemy import event

        event.listen(cls, "after_insert", _publish_constructor_embedding)
        event.listen(cls, "load", _capture_row_origin)

    @property
    def embedding_json(self) -> list[float] | None:
        entry = _entry(self)
        return list(entry[1]) if entry is not None and entry[1] is not None else None

    @embedding_json.setter
    def embedding_json(self, value: Any) -> None:
        key = _scope(self)
        vector = parse_embedding_payload(value)
        signature = _signature(self)
        with _lock:
            if vector is None:
                if key is not None:
                    _vectors.pop(key, None)
                _pending.pop(self, None)
                return
        entry = (signature, tuple(vector), compute_embedding_checksum(vector))
        if key is None:
            from anima_server.config import settings
            from anima_server.services.sessions import active_unlock_sessions

            if settings.core_require_encryption and not active_unlock_sessions(self.user_id):
                return
        with _lock:
            if key is not None:
                _vectors[key] = entry
            elif self.id is None and getattr(self, "_derived_origin_root", None) == _core_root():
                _pending[self] = (_core_root(), signature, entry)

    @property
    def embedding_checksum(self) -> str | None:
        entry = _entry(self, rebuild=False)
        return entry[2] if entry is not None else None

    @embedding_checksum.setter
    def embedding_checksum(self, value: str | None) -> None:
        key = _scope(self)
        with _lock:
            if key is not None and key in _vectors:
                signature, vector, _ = _vectors[key]
                _vectors[key] = (signature, vector, value)
            elif self in _pending:
                root, signature, entry = _pending[self]
                _pending[self] = (root, signature, (entry[0], entry[1], value))


def get_cluster_state(db, *, user_id: int, exclude_id: int | None = None) -> dict[str, Any]:
    """Recompute centroids from live retained experiences, without Soul writes."""
    from sqlalchemy import select

    from anima_server.models import AgentExperience
    from anima_server.services.agent.agent_experience import _running_centroid

    state: dict[str, Any] = {"next_index": 0, "clusters": {}}
    rows = db.scalars(
        select(AgentExperience)
        .where(AgentExperience.user_id == user_id)
        .order_by(AgentExperience.created_at.asc(), AgentExperience.id.asc())
    ).all()
    for row in rows:
        if row.cluster_id:
            with contextlib.suppress(ValueError, IndexError):
                state["next_index"] = max(
                    state["next_index"], int(row.cluster_id.rsplit("_", 1)[1]) + 1
                )
        if row.id == exclude_id or row.superseded_by is not None or not row.cluster_id:
            continue
        vector = row.embedding_json
        if not vector:
            continue
        cluster = state["clusters"].setdefault(
            row.cluster_id,
            {"centroid": vector, "count": 0, "experience_ids": [], "last_activity": None},
        )
        cluster["centroid"] = _running_centroid(cluster["centroid"], cluster["count"], vector)
        cluster["count"] += 1
        cluster["experience_ids"].append(row.id)
        cluster["last_activity"] = row.created_at.isoformat()
    return state
