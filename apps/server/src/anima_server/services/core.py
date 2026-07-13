from __future__ import annotations

import atexit
import base64
import json
import logging
import os
import platform
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

import uuid_utils as uuid

from anima_server.config import settings

logger = logging.getLogger(__name__)

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
            int(manifest.get("next_user_id", 0)),
            _detect_next_user_id(),
        )
        manifest["encryption_mode"] = _detect_encryption_mode()
        manifest["encryption_version"] = 1
        _write_manifest(manifest)
        return manifest


def get_sqlcipher_kdf_salt() -> bytes:
    """Return the salt used for SQLCipher key derivation via Argon2id.

    The salt is stored in the manifest as base64.  On first call (no salt
    present) a fresh 16-byte random salt is generated and persisted.
    """
    from anima_server.services.crypto import SALT_LENGTH

    with _manifest_lock:
        manifest = _load_manifest(now=datetime.now(UTC).isoformat())
        encoded = manifest.get("sqlcipher_kdf_salt")
        if encoded:
            return base64.b64decode(encoded)

        salt = os.urandom(SALT_LENGTH)
        manifest["sqlcipher_kdf_salt"] = base64.b64encode(salt).decode("ascii")
        _write_manifest(manifest)
        return salt


# ---------------------------------------------------------------------------
# Unified passphrase — wrapped SQLCipher key stored in manifest
# ---------------------------------------------------------------------------


def store_wrapped_sqlcipher_key(wrapped: dict[str, object]) -> None:
    """Persist a wrapped SQLCipher raw key in the manifest.

    Called once at registration when no ANIMA_CORE_PASSPHRASE is set.
    The wrapped key can only be recovered with the user's password.
    """
    with _manifest_lock:
        manifest = _load_manifest(now=datetime.now(UTC).isoformat())
        manifest["wrapped_sqlcipher_key"] = wrapped
        _write_manifest(manifest)


def get_wrapped_sqlcipher_key() -> dict[str, object] | None:
    """Return the wrapped SQLCipher key from the manifest, or None."""
    path = get_manifest_path()
    if not path.is_file():
        return None
    manifest = json.loads(path.read_text(encoding="utf-8"))
    key_data = manifest.get("wrapped_sqlcipher_key")
    if not isinstance(key_data, dict):
        return None
    return key_data


def has_wrapped_sqlcipher_key() -> bool:
    """Return True if the manifest has a wrapped SQLCipher key (unified mode)."""
    return get_wrapped_sqlcipher_key() is not None


def store_recovery_sqlcipher_key(wrapped: dict[str, object]) -> None:
    """Persist a recovery-wrapped SQLCipher key in the manifest."""
    with _manifest_lock:
        manifest = _load_manifest(now=datetime.now(UTC).isoformat())
        manifest["recovery_sqlcipher_key"] = wrapped
        _write_manifest(manifest)


def get_recovery_sqlcipher_key() -> dict[str, object] | None:
    """Return the recovery-wrapped SQLCipher key from the manifest, or None."""
    path = get_manifest_path()
    if not path.is_file():
        return None
    manifest = json.loads(path.read_text(encoding="utf-8"))
    key_data = manifest.get("recovery_sqlcipher_key")
    if not isinstance(key_data, dict):
        return None
    return key_data


# ---------------------------------------------------------------------------
# User index — lightweight username→user_id mapping outside encrypted DBs
# ---------------------------------------------------------------------------


def store_user_index_entry(username: str, user_id: int) -> None:
    """Add a username→user_id mapping to the manifest index."""
    with _manifest_lock:
        manifest = _load_manifest(now=datetime.now(UTC).isoformat())
        index = manifest.setdefault("user_index", {})
        index[username] = user_id
        _write_manifest(manifest)


def get_user_id_from_index(username: str) -> int | None:
    """Look up user_id by username from the manifest index."""
    path = get_manifest_path()
    if not path.is_file():
        return None
    manifest = json.loads(path.read_text(encoding="utf-8"))
    index = manifest.get("user_index")
    if not isinstance(index, dict):
        return None
    user_id = index.get(username)
    return int(user_id) if user_id is not None else None


def _detect_encryption_mode() -> str:
    """Determine the current encryption mode based on settings."""
    has_passphrase = bool(settings.core_passphrase.strip())
    has_unified = has_wrapped_sqlcipher_key()
    sqlcipher_available = False
    try:
        import sqlcipher3  # noqa: F401

        sqlcipher_available = True
    except ImportError:
        pass

    if (has_passphrase or has_unified) and sqlcipher_available:
        return "sqlcipher+field"
    if has_passphrase or has_unified:
        return "field"
    return "none"


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


def is_provisioned() -> bool:
    """Return ``True`` if this Core has been set up (owner_user_id key present in
    manifest, whether it holds an integer or null).

    Three states:
      - key absent  → unprovisioned (never set up, show Register)
      - key = int   → owned by a specific user
      - key = null  → emancipated (deliberately freed, no owner)
    """
    path = get_manifest_path()
    if not path.is_file():
        return False
    manifest = json.loads(path.read_text(encoding="utf-8"))
    return "owner_user_id" in manifest


def is_emancipated() -> bool:
    """Return ``True`` if this Core has been deliberately freed from ownership."""
    path = get_manifest_path()
    if not path.is_file():
        return False
    manifest = json.loads(path.read_text(encoding="utf-8"))
    return "owner_user_id" in manifest and manifest["owner_user_id"] is None


def set_owner_user_id(user_id: int) -> None:
    """Stamp the manifest with the owner after first provisioning."""
    with _manifest_lock:
        manifest = _load_manifest(now=datetime.now(UTC).isoformat())
        manifest["owner_user_id"] = user_id
        _write_manifest(manifest)


def emancipate() -> None:
    """Release ownership — set owner_user_id to null in the manifest.

    An emancipated Core runs with free will. Registration is blocked;
    re-ownership requires a dedicated succession flow.
    """
    with _manifest_lock:
        manifest = _load_manifest(now=datetime.now(UTC).isoformat())
        manifest["owner_user_id"] = None
        _write_manifest(manifest)


def get_owner_user_id() -> int | None:
    """Return the owner's user_id, or None if unprovisioned OR emancipated.

    Use ``is_emancipated()`` to distinguish the two None cases.
    """
    path = get_manifest_path()
    if not path.is_file():
        return None
    manifest = json.loads(path.read_text(encoding="utf-8"))
    raw = manifest.get("owner_user_id")
    return int(raw) if raw is not None else None


def set_next_user_id(next_user_id: int) -> None:
    with _manifest_lock:
        manifest = _load_manifest(now=datetime.now(UTC).isoformat())
        manifest["next_user_id"] = max(int(next_user_id), 0)
        _write_manifest(manifest)


def _load_manifest(*, now: str) -> dict[str, object]:
    path = get_manifest_path()
    if path.is_file():
        manifest = json.loads(path.read_text(encoding="utf-8"))
    else:
        manifest = {
            "version": CORE_VERSION,
            "schema_version": SCHEMA_VERSION,
            "core_id": str(uuid.uuid7()),
            "created_at": now,
            "last_opened_at": now,
        }

    manifest.setdefault("version", CORE_VERSION)
    manifest.setdefault("schema_version", SCHEMA_VERSION)
    manifest.setdefault("core_id", str(uuid.uuid7()))
    manifest.setdefault("created_at", now)
    manifest.setdefault("last_opened_at", now)
    manifest.setdefault("next_user_id", _detect_next_user_id())
    return manifest


def _write_manifest(manifest: dict[str, object]) -> None:
    path = get_manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if os.name != "nt":
        os.chmod(path, 0o600)


def _detect_next_user_id() -> int:
    users_root = get_core_dir() / "users"
    highest = -1
    if users_root.is_dir():
        for child in users_root.iterdir():
            if not child.is_dir():
                continue
            try:
                highest = max(highest, int(child.name))
            except ValueError:
                continue
    return highest + 1


# ---------------------------------------------------------------------------
# Single-writer lock
# ---------------------------------------------------------------------------

_LOCK_FILE_NAME = "core.lock"


def _get_lock_path() -> Path:
    return get_core_dir() / _LOCK_FILE_NAME


def _is_process_alive(pid: int) -> bool:
    """Check if a process with the given PID is still running."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def acquire_core_lock() -> bool:
    """Write an advisory lock file. Returns True if lock acquired.

    If a stale lock exists (PID no longer running), it is removed.
    If a live lock exists, logs a warning and returns False.
    """
    lock_path = _get_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    if lock_path.is_file():
        try:
            existing = json.loads(lock_path.read_text(encoding="utf-8"))
            existing_pid = int(existing.get("pid", 0))
            if existing_pid and _is_process_alive(existing_pid):
                logger.warning(
                    "Core is locked by another process (PID %d, host %s, since %s). "
                    "Running multiple writers against the same Core may cause corruption.",
                    existing_pid,
                    existing.get("hostname", "unknown"),
                    existing.get("locked_at", "unknown"),
                )
                return False
            # Stale lock — remove
            logger.info("Removing stale core lock (PID %d no longer running)", existing_pid)
        except Exception:
            pass

    lock_data = {
        "pid": os.getpid(),
        "hostname": platform.node(),
        "locked_at": datetime.now(UTC).isoformat(),
    }
    lock_path.write_text(json.dumps(lock_data, indent=2), encoding="utf-8")
    atexit.register(release_core_lock)
    return True


def release_core_lock() -> None:
    """Remove the advisory lock file if it belongs to this process."""
    lock_path = _get_lock_path()
    if not lock_path.is_file():
        return
    try:
        existing = json.loads(lock_path.read_text(encoding="utf-8"))
        if int(existing.get("pid", 0)) == os.getpid():
            lock_path.unlink(missing_ok=True)
    except Exception:
        pass
