from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

DEV_SESSION_STATE_PATH_ENV = "ANIMA_DEV_SESSION_STATE_PATH"
DEV_SESSION_KEY_ENV = "ANIMA_DEV_SESSION_KEY"

_MAGIC = b"ANIMADEV1"
_NONCE_LENGTH = 12
_KEY_LENGTH = 32
_AAD = b"anima-dev-session-snapshot:v1"
_VERSION = 1

logger = logging.getLogger(__name__)


class DevSessionSnapshotError(ValueError):
    """Raised when an authenticated dev-session snapshot is invalid."""


class DevSessionSnapshot:
    def __init__(self, *, path: Path, key: bytes) -> None:
        if len(key) != _KEY_LENGTH:
            raise DevSessionSnapshotError("Dev session snapshot key must be 32 bytes")
        self.path = path
        self._key = key

    @classmethod
    def from_environment(cls) -> DevSessionSnapshot | None:
        raw_path = os.getenv(DEV_SESSION_STATE_PATH_ENV, "").strip()
        raw_key = os.getenv(DEV_SESSION_KEY_ENV, "").strip()
        if not raw_path or not raw_key:
            return None
        try:
            key = base64.b64decode(raw_key, validate=True)
            return cls(path=Path(raw_path), key=key)
        except (binascii.Error, DevSessionSnapshotError, ValueError) as exc:
            logger.warning("Ignoring invalid dev session continuity environment: %s", type(exc).__name__)
            return None

    def write(self, payload: dict[str, object]) -> None:
        normalized = _validate_payload(payload)
        plaintext = json.dumps(
            normalized,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        nonce = os.urandom(_NONCE_LENGTH)
        ciphertext = AESGCM(self._key).encrypt(nonce, plaintext, _AAD)
        temporary_path = self.path.with_name(
            f"{self.path.name}.tmp-{os.getpid()}-{secrets.token_hex(6)}"
        )
        try:
            with temporary_path.open("xb") as handle:
                handle.write(_MAGIC + nonce + ciphertext)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    def load(self) -> dict[str, object] | None:
        if not self.path.is_file():
            return None
        encoded = self.path.read_bytes()
        minimum_length = len(_MAGIC) + _NONCE_LENGTH + 16
        if len(encoded) < minimum_length or not encoded.startswith(_MAGIC):
            raise DevSessionSnapshotError("Invalid dev session snapshot envelope")
        nonce_start = len(_MAGIC)
        nonce_end = nonce_start + _NONCE_LENGTH
        try:
            plaintext = AESGCM(self._key).decrypt(
                encoded[nonce_start:nonce_end],
                encoded[nonce_end:],
                _AAD,
            )
        except InvalidTag as exc:
            raise DevSessionSnapshotError("Dev session snapshot authentication failed") from exc
        try:
            payload = json.loads(plaintext.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DevSessionSnapshotError("Invalid dev session snapshot payload") from exc
        return _validate_payload(payload)


def _validate_payload(payload: Any) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise DevSessionSnapshotError("Snapshot payload must be an object")
    if payload.get("version") != _VERSION:
        raise DevSessionSnapshotError("Unsupported dev session snapshot version")

    raw_sessions = payload.get("sessions")
    if not isinstance(raw_sessions, list):
        raise DevSessionSnapshotError("Snapshot sessions must be a list")

    sessions: list[dict[str, object]] = []
    tokens: set[str] = set()
    for raw_session in raw_sessions:
        if not isinstance(raw_session, dict):
            raise DevSessionSnapshotError("Snapshot session must be an object")
        token = raw_session.get("token")
        if not isinstance(token, str) or not token.strip():
            raise DevSessionSnapshotError("Snapshot token must be a non-empty string")
        if token in tokens:
            raise DevSessionSnapshotError("Snapshot contains duplicate tokens")
        tokens.add(token)

        user_id = raw_session.get("userId")
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 0:
            raise DevSessionSnapshotError("Snapshot userId must be a non-negative integer")

        expires_at = raw_session.get("expiresAt")
        _parse_utc_expiry(expires_at)

        raw_deks = raw_session.get("deks")
        if not isinstance(raw_deks, dict):
            raise DevSessionSnapshotError("Snapshot deks must be an object")
        deks: dict[str, str] = {}
        for domain, encoded_key in raw_deks.items():
            if not isinstance(domain, str) or not domain.strip():
                raise DevSessionSnapshotError("Snapshot DEK domain must be non-empty")
            deks[domain] = _validate_encoded_key(encoded_key)

        sessions.append(
            {
                "token": token,
                "userId": user_id,
                "expiresAt": expires_at,
                "deks": deks,
            }
        )

    raw_sqlcipher_key = payload.get("sqlcipherKey")
    sqlcipher_key = (
        None if raw_sqlcipher_key is None else _validate_encoded_key(raw_sqlcipher_key)
    )
    return {
        "version": _VERSION,
        "sessions": sessions,
        "sqlcipherKey": sqlcipher_key,
    }


def _validate_encoded_key(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise DevSessionSnapshotError("Snapshot key must be non-empty base64")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise DevSessionSnapshotError("Snapshot key is not valid base64") from exc
    if len(decoded) != _KEY_LENGTH:
        raise DevSessionSnapshotError("Snapshot key must decode to 32 bytes")
    return value


def _parse_utc_expiry(value: Any) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise DevSessionSnapshotError("Snapshot expiry must be UTC RFC3339")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise DevSessionSnapshotError("Snapshot expiry must be UTC RFC3339") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise DevSessionSnapshotError("Snapshot expiry must be UTC RFC3339")
    return parsed.astimezone(UTC)

