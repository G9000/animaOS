from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from threading import RLock
from typing import Protocol

_SERVICE_NAME = "com.anima.credentials.v1"
_REFERENCE_PATTERN = re.compile(r"\Aanima-credential:v1:[0-9a-f]{64}\Z")
_REFERENCE_COMPONENT_PATTERN = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_AUDIENCE_PATTERN = re.compile(r"\Aanima-mod:[a-z0-9][a-z0-9.-]{0,126}\Z")
_MAX_CAPABILITIES = 256
_MAX_CAPABILITY_REFS = 32
_MAX_CAPABILITY_TTL_SECONDS = 30


class CredentialError(RuntimeError):
    """Base class for fail-closed OS credential failures."""


class CredentialUnavailableError(CredentialError):
    """Raised when the operating system has no usable secure store."""


class CredentialReferenceError(CredentialError):
    """Raised when a caller supplies a non-canonical credential reference."""


class CredentialCapabilityError(CredentialError):
    """Raised for invalid, expired, replayed, or wrong-audience capabilities."""


class CredentialBackend(Protocol):
    def get(self, reference: str) -> str | None: ...

    def set(self, reference: str, secret: str) -> None: ...

    def delete(self, reference: str) -> None: ...


class KeyringCredentialBackend:
    """Thin, fail-closed adapter over the platform keyring selected by keyring."""

    def __init__(self) -> None:
        try:
            import keyring
            from keyring.backends import fail, null
        except ImportError as exc:  # pragma: no cover - dependency contract
            raise CredentialUnavailableError("OS credential support is not installed.") from exc

        backend = keyring.get_keyring()
        if (
            isinstance(backend, (fail.Keyring, null.Keyring))
            or float(getattr(backend, "priority", 0)) <= 0
        ):
            raise CredentialUnavailableError(
                "No secure operating-system credential backend is available."
            )
        self._keyring = keyring

    def get(self, reference: str) -> str | None:
        try:
            return self._keyring.get_password(_SERVICE_NAME, reference)
        except self._keyring.errors.KeyringError as exc:
            raise CredentialUnavailableError(
                "The operating-system credential store could not be read."
            ) from exc

    def set(self, reference: str, secret: str) -> None:
        try:
            self._keyring.set_password(_SERVICE_NAME, reference, secret)
        except self._keyring.errors.KeyringError as exc:
            raise CredentialUnavailableError(
                "The operating-system credential store could not be written."
            ) from exc

    def delete(self, reference: str) -> None:
        try:
            self._keyring.delete_password(_SERVICE_NAME, reference)
        except self._keyring.errors.PasswordDeleteError:
            return
        except self._keyring.errors.KeyringError as exc:
            raise CredentialUnavailableError(
                "The operating-system credential could not be deleted."
            ) from exc


class MemoryCredentialBackend:
    """Explicit test backend; never selected implicitly in production."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, reference: str) -> str | None:
        return self.values.get(reference)

    def set(self, reference: str, secret: str) -> None:
        self.values[reference] = secret

    def delete(self, reference: str) -> None:
        self.values.pop(reference, None)


def credential_reference(scope: str, name: str) -> str:
    """Return a non-reversible, canonical reference safe for config stores."""
    if not _REFERENCE_COMPONENT_PATTERN.fullmatch(scope) or not (
        _REFERENCE_COMPONENT_PATTERN.fullmatch(name)
    ):
        raise CredentialReferenceError("Invalid credential reference component.")
    digest = hashlib.sha256()
    digest.update(b"anima-credential-reference-v1\0")
    for component in (scope, name):
        encoded = component.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return f"anima-credential:v1:{digest.hexdigest()}"


def validate_credential_reference(reference: str) -> str:
    if not _REFERENCE_PATTERN.fullmatch(reference):
        raise CredentialReferenceError("Invalid credential reference.")
    return reference


class CredentialStore:
    def __init__(self, backend: CredentialBackend) -> None:
        self._backend = backend
        self._lock = RLock()

    def get(self, reference: str) -> str | None:
        validate_credential_reference(reference)
        with self._lock:
            return self._backend.get(reference)

    def put(self, reference: str, secret: str) -> None:
        validate_credential_reference(reference)
        if not isinstance(secret, str) or not secret:
            raise CredentialError("Credential values must be non-empty strings.")
        with self._lock:
            self._backend.set(reference, secret)
            verified = self._backend.get(reference)
            if verified is None or not hmac.compare_digest(verified, secret):
                raise CredentialUnavailableError(
                    "The operating-system credential write could not be verified."
                )

    def delete(self, reference: str) -> None:
        validate_credential_reference(reference)
        with self._lock:
            self._backend.delete(reference)
            if self._backend.get(reference) is not None:
                raise CredentialUnavailableError(
                    "The operating-system credential deletion could not be verified."
                )


_credential_store: CredentialStore | None = None
_credential_store_lock = RLock()


def credential_store() -> CredentialStore:
    global _credential_store
    with _credential_store_lock:
        if _credential_store is None:
            _credential_store = CredentialStore(KeyringCredentialBackend())
        return _credential_store


def set_credential_store_for_tests(store: CredentialStore | None) -> None:
    global _credential_store
    with _credential_store_lock:
        _credential_store = store


@dataclass(frozen=True, slots=True)
class CredentialCapability:
    token: str
    expires_in_seconds: int


@dataclass(frozen=True, slots=True)
class _CapabilityRecord:
    audience: str
    user_id: int
    references: tuple[str, ...]
    expires_at: float


class CredentialCapabilityBroker:
    """Process-memory-only, audience-bound, one-shot secret capabilities."""

    def __init__(self) -> None:
        self._records: dict[bytes, _CapabilityRecord] = {}
        self._lock = RLock()

    def issue(
        self,
        *,
        audience: str,
        user_id: int,
        references: Iterable[str],
        ttl_seconds: int = 15,
    ) -> CredentialCapability:
        if not _AUDIENCE_PATTERN.fullmatch(audience):
            raise CredentialCapabilityError("Invalid credential capability audience.")
        if user_id < 0:
            raise CredentialCapabilityError("Invalid credential capability owner.")
        if ttl_seconds <= 0 or ttl_seconds > _MAX_CAPABILITY_TTL_SECONDS:
            raise CredentialCapabilityError("Invalid credential capability lifetime.")
        canonical = tuple(dict.fromkeys(validate_credential_reference(r) for r in references))
        if not canonical or len(canonical) > _MAX_CAPABILITY_REFS:
            raise CredentialCapabilityError("Invalid credential capability scope.")

        token = secrets.token_urlsafe(32)
        token_digest = self._token_digest(token)
        now = time.monotonic()
        with self._lock:
            self._purge_locked(now)
            if len(self._records) >= _MAX_CAPABILITIES:
                raise CredentialCapabilityError("Credential capability capacity reached.")
            self._records[token_digest] = _CapabilityRecord(
                audience=audience,
                user_id=user_id,
                references=canonical,
                expires_at=now + ttl_seconds,
            )
        return CredentialCapability(token=token, expires_in_seconds=ttl_seconds)

    def consume(
        self,
        *,
        token: str,
        audience: str,
        user_id: int,
        store: CredentialStore,
    ) -> Mapping[str, str]:
        token_digest = self._token_digest(token)
        now = time.monotonic()
        with self._lock:
            self._purge_locked(now)
            record = self._records.pop(token_digest, None)
        if record is None:
            raise CredentialCapabilityError(
                "Credential capability is invalid, expired, or already used."
            )
        if record.audience != audience or record.user_id != user_id:
            raise CredentialCapabilityError("Credential capability audience mismatch.")

        resolved: dict[str, str] = {}
        for reference in record.references:
            secret = store.get(reference)
            if secret is None:
                raise CredentialCapabilityError(
                    "Credential capability references an unavailable secret."
                )
            resolved[reference] = secret
        return resolved

    def revoke_all(self) -> None:
        with self._lock:
            self._records.clear()

    @staticmethod
    def _token_digest(token: str) -> bytes:
        if not isinstance(token, str) or len(token) < 32 or len(token) > 256:
            raise CredentialCapabilityError("Invalid credential capability token.")
        return hashlib.sha256(b"anima-credential-capability-v1\0" + token.encode()).digest()

    def _purge_locked(self, now: float) -> None:
        self._records = {
            digest: record for digest, record in self._records.items() if record.expires_at > now
        }


credential_capability_broker = CredentialCapabilityBroker()


def broker_bootstrap_reference() -> str:
    return credential_reference("broker", "anima-mod-bootstrap")


def ensure_broker_bootstrap_secret() -> str:
    return provision_broker_bootstrap_secret(None)


def provision_broker_bootstrap_secret(supplied: str | None) -> str:
    store = credential_store()
    reference = broker_bootstrap_reference()
    existing = store.get(reference)
    supplied = supplied.strip() if supplied else ""
    if supplied:
        # The launcher creates a fresh process-pair secret for each supervised
        # server/anima-mod lifetime. Replacing an older value invalidates an
        # orphaned mod process instead of making every clean restart fail.
        store.put(reference, supplied)
        return supplied
    if existing:
        return existing
    generated = secrets.token_urlsafe(48)
    store.put(reference, generated)
    return generated
