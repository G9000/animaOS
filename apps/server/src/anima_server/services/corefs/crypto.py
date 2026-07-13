from __future__ import annotations

from anima_server.services.corefs.types import KeyPurpose, WrappingPath
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
    wrapping_path: WrappingPath,
) -> bytes:
    if not core_id or not owner_id or key_version <= 0:
        raise ValueError("invalid manifest keyslot AAD")
    return (
        f"anima-keyslot-v1:core={core_id}:owner={owner_id}:"
        f"purpose={purpose.value}:version={key_version}:path={wrapping_path.value}"
    ).encode()


def soul_keyslot_aad(
    *,
    core_id: str,
    owner_id: str,
    domain: str,
    key_version: int,
    wrapping_path: WrappingPath,
) -> bytes:
    if not core_id or not owner_id or not domain or key_version <= 0:
        raise ValueError("invalid Soul keyslot AAD")
    return (
        f"anima-soul-keyslot-v1:core={core_id}:owner={owner_id}:"
        f"domain={domain}:version={key_version}:path={wrapping_path.value}"
    ).encode()


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
