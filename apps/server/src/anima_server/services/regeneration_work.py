"""Machine-local work queue for disposable Soul-derived regeneration flags."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from threading import RLock

from anima_server.config import settings

_VERSION = 1
_KINDS = frozenset({"memory_episode", "self_model_block"})
_MAX_ITEMS = 100_000
_lock = RLock()


class RegenerationWorkError(RuntimeError):
    pass


def _path() -> Path:
    if not settings.runtime_instance_data_dir:
        raise RegenerationWorkError("Runtime instance is not bound.")
    return Path(settings.runtime_instance_data_dir) / "work" / "regeneration.json"


def _load(path: Path) -> set[tuple[int, str, int]]:
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegenerationWorkError("Regeneration work state is unreadable.") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("version") != _VERSION
        or not isinstance(payload.get("items"), list)
        or len(payload["items"]) > _MAX_ITEMS
    ):
        raise RegenerationWorkError("Regeneration work state has an unsupported format.")
    items: set[tuple[int, str, int]] = set()
    for raw in payload["items"]:
        if not isinstance(raw, dict):
            raise RegenerationWorkError("Regeneration work entry is invalid.")
        user_id = raw.get("userId")
        kind = raw.get("kind")
        record_id = raw.get("recordId")
        if (
            isinstance(user_id, bool)
            or not isinstance(user_id, int)
            or user_id < 0
            or kind not in _KINDS
            or isinstance(record_id, bool)
            or not isinstance(record_id, int)
            or record_id < 1
        ):
            raise RegenerationWorkError("Regeneration work entry is invalid.")
        items.add((user_id, kind, record_id))
    if len(items) != len(payload["items"]):
        raise RegenerationWorkError("Regeneration work state contains duplicates.")
    return items


def _write(path: Path, items: set[tuple[int, str, int]]) -> None:
    if len(items) > _MAX_ITEMS:
        raise RegenerationWorkError("Regeneration work state exceeds its bound.")
    payload = {
        "version": _VERSION,
        "items": [
            {"userId": user_id, "kind": kind, "recordId": record_id}
            for user_id, kind, record_id in sorted(items)
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    handle = None
    try:
        handle = os.fdopen(descriptor, "w", encoding="utf-8")
        with handle:
            if hasattr(os, "fchmod"):
                os.fchmod(handle.fileno(), 0o600)
            else:
                os.chmod(temporary, 0o600)
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        if handle is None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise
    if _load(path) != items:
        raise RegenerationWorkError("Regeneration work state verification failed.")


def mark_regeneration_work(items: set[tuple[int, str, int]]) -> None:
    if any(
        user_id < 0 or kind not in _KINDS or record_id < 1 for user_id, kind, record_id in items
    ):
        raise RegenerationWorkError("Regeneration work entry is invalid.")
    if not items:
        return
    path = _path()
    with _lock:
        current = _load(path)
        current.update(items)
        _write(path, current)


def regeneration_work_ids(*, user_id: int, kind: str) -> frozenset[int]:
    if kind not in _KINDS:
        raise RegenerationWorkError("Regeneration work kind is invalid.")
    path = _path()
    with _lock:
        return frozenset(
            record_id
            for item_user_id, item_kind, record_id in _load(path)
            if item_user_id == user_id and item_kind == kind
        )
