from __future__ import annotations

import json
import os
from dataclasses import dataclass
from threading import RLock

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_RUNTIME_SEAL_INFO = b"anima-runtime-seal-v1"


class RuntimeSealingLocked(RuntimeError):
    """Raised when sealed Runtime access is attempted without an unlock key."""


@dataclass(frozen=True, slots=True)
class RuntimePayloadAAD:
    row_type: str
    row_id: str
    owner_id: str

    def encode(self) -> bytes:
        if not self.row_type or not self.row_id or not self.owner_id:
            raise ValueError("Runtime payload AAD fields must be non-empty")
        return json.dumps(
            {
                "ownerId": self.owner_id,
                "rowId": self.row_id,
                "rowType": self.row_type,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class SealedRuntimePayload:
    nonce: bytes
    ciphertext: bytes
    version: int = 1


class RuntimePayloadSealer:
    """Unlock-scoped authenticated sealing for crash-durable Runtime payloads."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._key: bytearray | None = None

    @property
    def installed(self) -> bool:
        with self._lock:
            return self._key is not None

    def install(self, *, sqlcipher_key: bytes, local_instance_id: str) -> None:
        if not sqlcipher_key:
            raise ValueError("SQLCipher key must be non-empty")
        if not local_instance_id:
            raise ValueError("local instance ID must be non-empty")
        derived = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=local_instance_id.encode("utf-8"),
            info=_RUNTIME_SEAL_INFO,
        ).derive(sqlcipher_key)
        with self._lock:
            self._clear_locked()
            self._key = bytearray(derived)

    def seal(
        self,
        plaintext: bytes,
        *,
        aad: RuntimePayloadAAD,
    ) -> SealedRuntimePayload:
        with self._lock:
            key = self._require_key_locked()
            nonce = os.urandom(12)
            ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad.encode())
        return SealedRuntimePayload(nonce=nonce, ciphertext=ciphertext)

    def open(
        self,
        payload: SealedRuntimePayload,
        *,
        aad: RuntimePayloadAAD,
    ) -> bytes:
        if payload.version != 1:
            raise ValueError("unsupported sealed Runtime payload version")
        with self._lock:
            key = self._require_key_locked()
            return AESGCM(key).decrypt(
                payload.nonce,
                payload.ciphertext,
                aad.encode(),
            )

    def clear(self) -> None:
        with self._lock:
            self._clear_locked()

    def _require_key_locked(self) -> bytes:
        if self._key is None:
            raise RuntimeSealingLocked("Runtime sealing key is unavailable while locked")
        return bytes(self._key)

    def _clear_locked(self) -> None:
        if self._key is not None:
            self._key[:] = b"\0" * len(self._key)
            self._key = None
