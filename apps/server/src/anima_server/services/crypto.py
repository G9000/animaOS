from __future__ import annotations

import platform
import threading
from dataclasses import dataclass
from collections.abc import Callable
from typing import TypeVar
import base64
import os

from argon2.low_level import Type, hash_secret_raw
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

_T = TypeVar("_T")
_LARGE_STACK = 8 * 1024 * 1024  # 8 MiB
_USE_LARGE_STACK = platform.system() == "Windows"

ARGON2_TIME_COST = 3
ARGON2_MEMORY_COST_KIB = 64 * 1024
ARGON2_PARALLELISM = 4
KEY_LENGTH = 32
SALT_LENGTH = 16
IV_LENGTH = 12
AUTH_TAG_LENGTH = 16
ENCRYPTED_TEXT_PREFIX = "enc1"
ENCRYPTED_TEXT_PREFIX_AAD = "enc2"

# Vault/succession use stronger parameters — these payloads may be stored
# offline for years and deserve additional brute-force resistance.
VAULT_ARGON2_TIME_COST = 4
VAULT_ARGON2_MEMORY_COST_KIB = 128 * 1024
VAULT_ARGON2_PARALLELISM = 4

# Domain separator for HKDF-based SQLCipher key derivation
SQLCIPHER_HKDF_INFO = b"anima-sqlcipher-v1"


def _run_with_large_stack(fn: Callable[[], _T]) -> _T:
    """Run *fn* in a thread with an 8 MiB stack on Windows.

    On Windows, anyio worker threads (used by FastAPI TestClient and potentially
    in production via run_sync) have a default 1 MiB stack.  Argon2id memory
    allocations in the C library can overflow this.  Running the heavy work in
    a dedicated thread with an explicit large stack avoids the crash.

    On non-Windows platforms this is a direct call (no thread overhead).
    """
    if not _USE_LARGE_STACK:
        return fn()

    result: list[_T] = []
    error: list[BaseException] = []

    def _worker() -> None:
        try:
            result.append(fn())
        except BaseException as exc:  # noqa: BLE001
            error.append(exc)

    _lock = threading.Lock()
    with _lock:
        prev = threading.stack_size(_LARGE_STACK)
        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        threading.stack_size(prev)
    t.join()

    if error:
        raise error[0]
    return result[0]


@dataclass(frozen=True, slots=True)
class WrappedDekRecord:
    kdf_salt: str
    kdf_time_cost: int
    kdf_memory_cost_kib: int
    kdf_parallelism: int
    kdf_key_length: int
    wrap_iv: str
    wrap_tag: str
    wrapped_dek: str


def derive_argon2id_key(
    passphrase: str,
    salt: bytes,
    *,
    time_cost: int = ARGON2_TIME_COST,
    memory_cost_kib: int = ARGON2_MEMORY_COST_KIB,
    parallelism: int = ARGON2_PARALLELISM,
    key_length: int = KEY_LENGTH,
) -> bytes:
    def _derive() -> bytes:
        return hash_secret_raw(
            secret=passphrase.encode("utf-8"),
            salt=salt,
            time_cost=time_cost,
            memory_cost=memory_cost_kib,
            parallelism=parallelism,
            hash_len=key_length,
            type=Type.ID,
        )

    return _run_with_large_stack(_derive)


def derive_sqlcipher_key(passphrase: str, salt: bytes) -> bytes:
    """Derive a 32-byte raw key for SQLCipher from the passphrase.

    Uses Argon2id for the memory-hard derivation, then HKDF-SHA256 with a
    domain separator to produce a key independent of the field-level KEK.
    The returned key should be passed to SQLCipher in raw hex format via
    ``PRAGMA key = "x'<hex>'"`` — this bypasses SQLCipher's weaker internal
    PBKDF2, giving us full control of the KDF chain.
    """
    master = derive_argon2id_key(passphrase, salt)
    return HKDF(
        algorithm=hashes.SHA256(),
        length=KEY_LENGTH,
        salt=None,  # salt already consumed by Argon2id
        info=SQLCIPHER_HKDF_INFO,
    ).derive(master)


def create_wrapped_dek(passphrase: str) -> tuple[bytes, WrappedDekRecord]:
    dek = os.urandom(KEY_LENGTH)
    return dek, wrap_dek(passphrase, dek)


def create_wrapped_deks_for_domains(
    passphrase: str,
    domains: tuple[str, ...],
) -> tuple[dict[str, bytes], list[tuple[str, WrappedDekRecord]]]:
    """Generate independent DEKs for each domain.

    Returns (deks_dict, wrapped_records) where deks_dict maps domain→plaintext
    DEK and wrapped_records is a list of (domain, WrappedDekRecord) pairs.
    """
    deks: dict[str, bytes] = {}
    records: list[tuple[str, WrappedDekRecord]] = []
    for domain in domains:
        dek, record = create_wrapped_dek(passphrase)
        deks[domain] = dek
        records.append((domain, record))
    return deks, records


def wrap_dek(passphrase: str, dek: bytes) -> WrappedDekRecord:
    salt = os.urandom(SALT_LENGTH)
    iv = os.urandom(IV_LENGTH)
    kek = derive_argon2id_key(passphrase, salt)
    encrypted = AESGCM(kek).encrypt(iv, dek, None)
    ciphertext, tag = encrypted[:-AUTH_TAG_LENGTH], encrypted[-AUTH_TAG_LENGTH:]

    return WrappedDekRecord(
        kdf_salt=base64.b64encode(salt).decode("ascii"),
        kdf_time_cost=ARGON2_TIME_COST,
        kdf_memory_cost_kib=ARGON2_MEMORY_COST_KIB,
        kdf_parallelism=ARGON2_PARALLELISM,
        kdf_key_length=KEY_LENGTH,
        wrap_iv=base64.b64encode(iv).decode("ascii"),
        wrap_tag=base64.b64encode(tag).decode("ascii"),
        wrapped_dek=base64.b64encode(ciphertext).decode("ascii"),
    )


def unwrap_dek(passphrase: str, record: WrappedDekRecord) -> bytes:
    salt = base64.b64decode(record.kdf_salt)
    iv = base64.b64decode(record.wrap_iv)
    tag = base64.b64decode(record.wrap_tag)
    ciphertext = base64.b64decode(record.wrapped_dek)
    kek = derive_argon2id_key(
        passphrase,
        salt,
        time_cost=record.kdf_time_cost,
        memory_cost_kib=record.kdf_memory_cost_kib,
        parallelism=record.kdf_parallelism,
        key_length=record.kdf_key_length,
    )
    return AESGCM(kek).decrypt(iv, ciphertext + tag, None)


def encrypt_text_with_dek(plaintext: str, dek: bytes, *, aad: bytes | None = None) -> str:
    iv = os.urandom(IV_LENGTH)
    encrypted = AESGCM(dek).encrypt(iv, plaintext.encode("utf-8"), aad)
    ciphertext, tag = encrypted[:-AUTH_TAG_LENGTH], encrypted[-AUTH_TAG_LENGTH:]
    prefix = ENCRYPTED_TEXT_PREFIX_AAD if aad is not None else ENCRYPTED_TEXT_PREFIX
    return ":".join(
        [
            prefix,
            base64.b64encode(iv).decode("ascii"),
            base64.b64encode(tag).decode("ascii"),
            base64.b64encode(ciphertext).decode("ascii"),
        ]
    )


def decrypt_text_with_dek(serialized: str, dek: bytes, *, aad: bytes | None = None) -> str:
    # Plaintext passthrough — not encrypted
    if not serialized.startswith(f"{ENCRYPTED_TEXT_PREFIX}:") and not serialized.startswith(f"{ENCRYPTED_TEXT_PREFIX_AAD}:"):
        return serialized
    prefix, iv_b64, tag_b64, ciphertext_b64 = serialized.split(":", 3)
    if prefix not in (ENCRYPTED_TEXT_PREFIX, ENCRYPTED_TEXT_PREFIX_AAD):
        raise ValueError("Invalid encrypted payload format.")
    iv = base64.b64decode(iv_b64)
    tag = base64.b64decode(tag_b64)
    ciphertext = base64.b64decode(ciphertext_b64)
    # enc1 = legacy without AAD; enc2 = AAD-bound
    effective_aad = aad if prefix == ENCRYPTED_TEXT_PREFIX_AAD else None
    plaintext = AESGCM(dek).decrypt(iv, ciphertext + tag, effective_aad)
    return plaintext.decode("utf-8")
