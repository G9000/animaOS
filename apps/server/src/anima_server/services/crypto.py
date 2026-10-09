from __future__ import annotations

import base64
import logging
import os
import platform
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from argon2.low_level import Type, hash_secret_raw
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

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
ENCRYPTED_PREFIX = "enc2"

logger = logging.getLogger(__name__)


def _run_with_large_stack[T](fn: Callable[[], _T]) -> _T:
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
        except BaseException as exc:
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


def generate_domain_keys(domains: tuple[str, ...]) -> dict[str, bytes]:
    """Generate independent secrets; current versioned keyslots authenticate them."""
    if not domains or len(set(domains)) != len(domains):
        raise ValueError("Soul domain key set is empty or ambiguous")
    return {domain: os.urandom(KEY_LENGTH) for domain in domains}


def wrap_secret_with_aad(passphrase: str, secret: bytes, aad: bytes) -> WrappedDekRecord:
    if len(secret) != KEY_LENGTH:
        raise ValueError("wrapped secret must be exactly 32 bytes")
    salt = os.urandom(SALT_LENGTH)
    iv = os.urandom(IV_LENGTH)
    kek = derive_argon2id_key(passphrase, salt)
    encrypted = AESGCM(kek).encrypt(iv, secret, aad)
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


def unwrap_secret_with_aad(
    passphrase: str,
    record: WrappedDekRecord,
    aad: bytes,
) -> bytes:
    profile = (
        record.kdf_time_cost,
        record.kdf_memory_cost_kib,
        record.kdf_parallelism,
        record.kdf_key_length,
    )
    supported_profile = (
        ARGON2_TIME_COST,
        ARGON2_MEMORY_COST_KIB,
        ARGON2_PARALLELISM,
        KEY_LENGTH,
    )
    if profile != supported_profile:
        raise ValueError("unsupported wrapped-key profile")

    try:
        salt = base64.b64decode(record.kdf_salt, validate=True)
        iv = base64.b64decode(record.wrap_iv, validate=True)
        tag = base64.b64decode(record.wrap_tag, validate=True)
        ciphertext = base64.b64decode(record.wrapped_dek, validate=True)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid wrapped-key envelope encoding") from exc
    if (
        len(salt) != SALT_LENGTH
        or len(iv) != IV_LENGTH
        or len(tag) != AUTH_TAG_LENGTH
        or len(ciphertext) != KEY_LENGTH
    ):
        raise ValueError("invalid wrapped-key envelope size")
    kek = derive_argon2id_key(
        passphrase,
        salt,
        time_cost=record.kdf_time_cost,
        memory_cost_kib=record.kdf_memory_cost_kib,
        parallelism=record.kdf_parallelism,
        key_length=record.kdf_key_length,
    )
    return AESGCM(kek).decrypt(iv, ciphertext + tag, aad)


def encrypt_text_with_dek(plaintext: str, dek: bytes, *, aad: bytes | None = None) -> str:
    iv = os.urandom(IV_LENGTH)
    encrypted = AESGCM(dek).encrypt(iv, plaintext.encode("utf-8"), aad)
    ciphertext, tag = encrypted[:-AUTH_TAG_LENGTH], encrypted[-AUTH_TAG_LENGTH:]
    return ":".join(
        [
            ENCRYPTED_PREFIX,
            base64.b64encode(iv).decode("ascii"),
            base64.b64encode(tag).decode("ascii"),
            base64.b64encode(ciphertext).decode("ascii"),
        ]
    )


def decrypt_text_with_dek(serialized: str, dek: bytes, *, aad: bytes | None = None) -> str:
    # Plaintext passthrough — not encrypted
    if not serialized.startswith(f"{ENCRYPTED_PREFIX}:"):
        return serialized
    prefix, iv_b64, tag_b64, ciphertext_b64 = serialized.split(":", 3)
    if prefix != ENCRYPTED_PREFIX:
        raise ValueError("Invalid encrypted payload format.")
    iv = base64.b64decode(iv_b64)
    tag = base64.b64decode(tag_b64)
    ciphertext = base64.b64decode(ciphertext_b64)
    plaintext = AESGCM(dek).decrypt(iv, ciphertext + tag, aad)
    return plaintext.decode("utf-8")


def encrypt_blob(plaintext: bytes, dek: bytes, *, aad: bytes | None = None) -> bytes:
    """Encrypt raw bytes with AES-256-GCM.

    Returns ``IV || ciphertext || auth_tag``.
    """
    iv = os.urandom(IV_LENGTH)
    ct_and_tag = AESGCM(dek).encrypt(iv, plaintext, aad)
    return iv + ct_and_tag


def decrypt_blob(data: bytes, dek: bytes, *, aad: bytes | None = None) -> bytes:
    """Decrypt raw bytes produced by :func:`encrypt_blob`."""
    if len(data) < IV_LENGTH:
        raise ValueError("Encrypted data too short")

    iv = data[:IV_LENGTH]
    ct_and_tag = data[IV_LENGTH:]
    return AESGCM(dek).decrypt(iv, ct_and_tag, aad)
