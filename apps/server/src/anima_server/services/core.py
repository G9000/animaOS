from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from anima_server.config import settings

CORE_VERSION = 1
SCHEMA_VERSION = "1.0.0"


def get_core_dir() -> Path:
    return settings.data_dir


def get_manifest_path() -> Path:
    return get_core_dir() / "manifest.json"


def ensure_core_manifest() -> dict[str, object]:
    path = get_manifest_path()
    now = datetime.now(UTC).isoformat()

    if path.is_file():
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["last_opened_at"] = now
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest

    manifest: dict[str, object] = {
        "version": CORE_VERSION,
        "schema_version": SCHEMA_VERSION,
        "created_at": now,
        "last_opened_at": now,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
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
