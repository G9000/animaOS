from __future__ import annotations

import anima_core

from anima_server.services.corefs.types import KeyPurpose, PayloadScope, WrappingPath
from anima_server.services.crypto import (
    WrappedDekRecord,
    unwrap_secret_with_aad,
    wrap_secret_with_aad,
)


def manifest_keyslot_aad(
    *,
    core_id: str,
    owner_id: str,
    purpose: KeyPurpose,
    key_version: int,
    credential_generation: int,
    scope: PayloadScope,
    frk_version: int | None,
    object_key_epoch: int | None,
    wrapping_path: WrappingPath,
) -> bytes:
    return bytes(
        anima_core.corefs_manifest_keyslot_aad(
            core_id,
            owner_id,
            purpose.value,
            key_version,
            credential_generation,
            scope.value,
            frk_version,
            object_key_epoch,
            wrapping_path.value,
        )
    )


def soul_keyslot_aad(
    *,
    core_id: str,
    owner_id: str,
    domain: str,
    key_version: int,
    credential_generation: int,
    wrapping_path: WrappingPath,
) -> bytes:
    return bytes(
        anima_core.corefs_soul_keyslot_aad(
            core_id,
            owner_id,
            domain,
            key_version,
            credential_generation,
            wrapping_path.value,
        )
    )


def wrap_keyslot_secret(
    credential: str,
    secret: bytes,
    aad: bytes,
) -> WrappedDekRecord:
    return wrap_secret_with_aad(credential, secret, aad)


def unwrap_keyslot_secret(
    credential: str,
    wrapped: WrappedDekRecord,
    aad: bytes,
) -> bytes:
    return unwrap_secret_with_aad(credential, wrapped, aad)
