from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class PayloadScope(StrEnum):
    FULL = "full"
    SOUL = "soul"
    FS = "fs"


class WrappingPath(StrEnum):
    PASSWORD = "password"
    RECOVERY = "recovery"


class KeyPurpose(StrEnum):
    SOUL = "soul"
    FILESYSTEM_ROOT = "filesystem-root"


class KeyslotStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    DECRYPT_ONLY = "decrypt-only"


class FrkRotationPhase(StrEnum):
    IDLE = "idle"
    PREPARED = "prepared"
    VERIFYING = "verifying"


SUPPORTED_KDF_ALGORITHM = "argon2id-v1"
SUPPORTED_WRAP_ALGORITHM = "aes-256-gcm"
KEYSLOT_ENVELOPE_VERSION = 1


@dataclass(frozen=True, slots=True)
class ManifestKeyslot:
    purpose: KeyPurpose
    wrapping_path: WrappingPath
    status: KeyslotStatus
    scope: PayloadScope
    key_version: int
    credential_generation: int
    frk_version: int | None
    object_key_epoch: int | None
    kdf_algorithm: str
    wrap_algorithm: str
    envelope_version: int
    wrapped: dict[str, object] = field(repr=False)

    def __post_init__(self) -> None:
        if self.kdf_algorithm != SUPPORTED_KDF_ALGORITHM:
            raise ValueError(f"unsupported KDF algorithm: {self.kdf_algorithm}")
        if self.wrap_algorithm != SUPPORTED_WRAP_ALGORITHM:
            raise ValueError(f"unsupported wrapping algorithm: {self.wrap_algorithm}")
        if self.envelope_version != KEYSLOT_ENVELOPE_VERSION:
            raise ValueError(f"unsupported keyslot envelope version: {self.envelope_version}")
        if self.key_version <= 0 or self.credential_generation <= 0:
            raise ValueError("key version and credential generation must be positive")
        if self.purpose is KeyPurpose.FILESYSTEM_ROOT:
            if self.frk_version is None or self.frk_version <= 0:
                raise ValueError("filesystem-root keyslot requires a positive FRK version")
            if self.frk_version != self.key_version:
                raise ValueError("FRK version must match key version")
            if self.object_key_epoch is None or self.object_key_epoch <= 0:
                raise ValueError("filesystem-root keyslot requires a positive object-key epoch")
        elif self.frk_version is not None or self.object_key_epoch is not None:
            raise ValueError("Soul keyslot cannot declare filesystem key metadata")
        if not isinstance(self.wrapped, dict):
            raise ValueError("wrapped keyslot payload must be an object")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ManifestKeyslot:
        try:
            return cls(
                purpose=KeyPurpose(value["purpose"]),
                wrapping_path=WrappingPath(value["wrapping_path"]),
                status=KeyslotStatus(value["status"]),
                scope=PayloadScope(value["scope"]),
                key_version=int(value["key_version"]),
                credential_generation=int(value["credential_generation"]),
                frk_version=(
                    int(value["frk_version"]) if value.get("frk_version") is not None else None
                ),
                object_key_epoch=(
                    int(value["object_key_epoch"])
                    if value.get("object_key_epoch") is not None
                    else None
                ),
                kdf_algorithm=str(value["kdf_algorithm"]),
                wrap_algorithm=str(value["wrap_algorithm"]),
                envelope_version=int(value["envelope_version"]),
                wrapped=dict(value["wrapped"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, ValueError) and (
                str(exc).startswith("unsupported")
                or str(exc) == "FRK version must match key version"
            ):
                raise
            raise ValueError("invalid manifest keyslot") from exc

    def to_dict(self) -> dict[str, object]:
        return {
            "purpose": self.purpose.value,
            "wrapping_path": self.wrapping_path.value,
            "status": self.status.value,
            "scope": self.scope.value,
            "key_version": self.key_version,
            "credential_generation": self.credential_generation,
            "frk_version": self.frk_version,
            "object_key_epoch": self.object_key_epoch,
            "kdf_algorithm": self.kdf_algorithm,
            "wrap_algorithm": self.wrap_algorithm,
            "envelope_version": self.envelope_version,
            "wrapped": dict(self.wrapped),
        }


@dataclass(frozen=True, slots=True)
class FrkRotationState:
    active_version: int
    pending_version: int | None = None
    decrypt_only_versions: tuple[int, ...] = ()
    phase: FrkRotationPhase = FrkRotationPhase.IDLE
    committed_catalog_generation: int = 0
    password_reopen_verified: bool = False
    recovery_reopen_verified: bool = False

    def __post_init__(self) -> None:
        if self.active_version <= 0:
            raise ValueError("active FRK version must be positive")
        if self.pending_version is not None and self.pending_version <= self.active_version:
            raise ValueError("pending FRK version must be newer than active")
        if any(version <= 0 for version in self.decrypt_only_versions):
            raise ValueError("decrypt-only FRK versions must be positive")
