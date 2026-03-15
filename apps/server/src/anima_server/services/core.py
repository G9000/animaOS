from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

from anima_server.config import settings

CORE_VERSION = 1
SCHEMA_VERSION = "1.0.0"
_manifest_lock = Lock()


def get_core_dir() -> Path:
    return settings.data_dir


def get_manifest_path() -> Path:
    return get_core_dir() / "manifest.json"


def ensure_core_manifest() -> dict[str, object]:
    with _manifest_lock:
        now = datetime.now(UTC).isoformat()
        manifest = _load_manifest(now=now)
        manifest["last_opened_at"] = now
        manifest["next_user_id"] = max(
            int(manifest.get("next_user_id", 1)),
            _detect_next_user_id(),
        )
        _write_manifest(manifest)
        return manifest


def get_core_birth_date() -> str:
    """Return the Core's creation date as an ISO date string (YYYY-MM-DD)."""
    path = get_manifest_path()
    if path.is_file():
        manifest = json.loads(path.read_text(encoding="utf-8"))
        created = manifest.get("created_at", "")
        if created:
            return created[:10]
    return datetime.now(UTC).strftime("%Y-%m-%d")


def allocate_user_id() -> int:
    with _manifest_lock:
        manifest = _load_manifest(now=datetime.now(UTC).isoformat())
        next_user_id = max(
            int(manifest.get("next_user_id", 1)),
            _detect_next_user_id(),
        )
        manifest["next_user_id"] = next_user_id + 1
        _write_manifest(manifest)
        return next_user_id


def set_next_user_id(next_user_id: int) -> None:
    with _manifest_lock:
        manifest = _load_manifest(now=datetime.now(UTC).isoformat())
        manifest["next_user_id"] = max(int(next_user_id), 1)
        _write_manifest(manifest)


def _load_manifest(*, now: str) -> dict[str, object]:
    path = get_manifest_path()
    if path.is_file():
        manifest = json.loads(path.read_text(encoding="utf-8"))
    else:
        manifest = {
            "version": CORE_VERSION,
            "schema_version": SCHEMA_VERSION,
            "created_at": now,
            "last_opened_at": now,
        }

    manifest.setdefault("version", CORE_VERSION)
    manifest.setdefault("schema_version", SCHEMA_VERSION)
    manifest.setdefault("created_at", now)
    manifest.setdefault("last_opened_at", now)
    manifest.setdefault("next_user_id", _detect_next_user_id())
    return manifest


def _write_manifest(manifest: dict[str, object]) -> None:
    path = get_manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _detect_next_user_id() -> int:
    users_root = get_core_dir() / "users"
    highest = 0
    if users_root.is_dir():
        for child in users_root.iterdir():
            if not child.is_dir():
                continue
            try:
                highest = max(highest, int(child.name))
            except ValueError:
                continue
    return highest + 1
