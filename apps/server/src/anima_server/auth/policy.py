"""Trust policy stores for gateway ingress controls."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from anima_server.auth.context import GatewayRequestContext


DeviceMetadata = dict[str, str | int | float | bool]


@dataclass
class DeviceRecord:
    user_id: int
    device_id: str
    device_name: str
    device_secret_hash: str
    created_at: datetime
    last_seen_at: datetime
    revoked: bool = False
    metadata: DeviceMetadata | None = None


class DeviceTrustStore:
    """Stores device trust bindings in memory.

    Intended to be a lightweight baseline. The store is in-memory with
    deterministic persistence behavior expected from restart or process
    replacement for phase-1.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._devices: dict[int, dict[str, DeviceRecord]] = {}

    @staticmethod
    def _hash_secret(secret: str) -> str:
        return hashlib.sha256(secret.encode("utf-8")).hexdigest()

    @staticmethod
    def _mask_secret(secret: str) -> str:
        if len(secret) <= 8:
            return "*" * len(secret)
        return f"{secret[:4]}...{secret[-4:]}"

    def list_devices(self, user_id: int, include_revoked: bool = False) -> list[DeviceRecord]:
        with self._lock:
            records = list(self._devices.get(user_id, {}).values())
        if include_revoked:
            return records
        return [record for record in records if not record.revoked]

    def get_device(self, user_id: int, device_id: str) -> DeviceRecord | None:
        with self._lock:
            return self._devices.get(user_id, {}).get(device_id)

    def register_device(
        self,
        *,
        user_id: int,
        device_id: str | None = None,
        device_name: str = "desktop",
        secret: str | None = None,
    ) -> tuple[DeviceRecord, str]:
        now = datetime.now(UTC)
        new_device_id = device_id or str(secrets.token_urlsafe(12))
        new_secret = secret or secrets.token_urlsafe(24)
        record = DeviceRecord(
            user_id=user_id,
            device_id=new_device_id,
            device_name=device_name,
            device_secret_hash=self._hash_secret(new_secret),
            created_at=now,
            last_seen_at=now,
        )
        with self._lock:
            self._devices.setdefault(user_id, {})
            self._devices[user_id][new_device_id] = record
        return record, new_secret

    def rotate_secret(self, *, user_id: int, device_id: str) -> tuple[DeviceRecord, str] | None:
        now = datetime.now(UTC)
        with self._lock:
            record = self._devices.get(user_id, {}).get(device_id)
            if record is None or record.revoked:
                return None
            new_secret = secrets.token_urlsafe(24)
            record.device_secret_hash = self._hash_secret(new_secret)
            record.last_seen_at = now
            self._devices[user_id][device_id] = record
            return record, new_secret

    def revoke_device(self, *, user_id: int, device_id: str) -> bool:
        with self._lock:
            record = self._devices.get(user_id, {}).get(device_id)
            if record is None or record.revoked:
                return False
            record.revoked = True
            record.last_seen_at = datetime.now(UTC)
            self._devices[user_id][device_id] = record
            return True

    def touch(self, *, user_id: int, device_id: str) -> None:
        with self._lock:
            record = self._devices.get(user_id, {}).get(device_id)
            if record is not None and not record.revoked:
                record.last_seen_at = datetime.now(UTC)

    def validate_device(self, context: GatewayRequestContext) -> bool:
        if not context.device_id:
            return False
        if not context.device_secret:
            return False
        if context.user_id is None:
            return False

        record = self.get_device(context.user_id, context.device_id)
        if record is None or record.revoked:
            return False
        return hmac.compare_digest(
            record.device_secret_hash,
            self._hash_secret(context.device_secret),
        )

    def mask_device_secret(self, secret: str) -> str:
        return self._mask_secret(secret)


class ReplayStore:
    """Best-effort replay detection over request identifiers."""

    def __init__(self, *, ttl_seconds: int = 180) -> None:
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._nonces: dict[str, float] = {}

    def _cleanup_locked(self, now: float) -> None:
        cutoff = now - self._ttl_seconds
        stale = [key for key, expires_at in self._nonces.items() if expires_at < cutoff]
        for key in stale:
            self._nonces.pop(key, None)

    def consume(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            self._cleanup_locked(now)
            if key in self._nonces:
                return False
            self._nonces[key] = now + self._ttl_seconds
            return True


class RateLimiter:
    """Sliding-window-like request counter with coarse grain and simple cleanup."""

    def __init__(self, *, window_seconds: int = 60) -> None:
        self._window_seconds = window_seconds
        self._lock = threading.Lock()
        self._attempts: dict[str, list[float]] = {}

    def allow(self, key: str, max_requests: int) -> tuple[bool, int | None]:
        now = time.time()
        stale_cutoff = now - self._window_seconds
        with self._lock:
            attempts = [ts for ts in self._attempts.get(key, []) if ts >= stale_cutoff]
            if len(attempts) >= max_requests:
                self._attempts[key] = attempts
                retry_after = int(attempts[0] + self._window_seconds - now)
                return False, max(1, retry_after)
            attempts.append(now)
            self._attempts[key] = attempts
            return True, None


class IdempotencyStore:
    """Simple idempotency cache keyed by event id."""

    def __init__(self, *, ttl_seconds: int = 24 * 60 * 60) -> None:
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._records: dict[str, tuple[float, Any]] = {}

    def _cleanup_locked(self, now: float) -> None:
        cutoff = now - self._ttl_seconds
        stale = [key for key, (seen_at, _) in self._records.items() if seen_at < cutoff]
        for key in stale:
            self._records.pop(key, None)

    def check(self, key: str) -> tuple[bool, Any | None]:
        now = time.time()
        with self._lock:
            self._cleanup_locked(now)
            if key not in self._records:
                self._records[key] = (now, None)
                return True, None
            _, value = self._records[key]
            return False, value

    def mark(self, key: str, value: Any) -> None:
        with self._lock:
            self._records[key] = (time.time(), value)


device_trust_store = DeviceTrustStore()
request_replay_store = ReplayStore()
request_rate_limiter = RateLimiter()
webhook_idempotency_store = IdempotencyStore()
