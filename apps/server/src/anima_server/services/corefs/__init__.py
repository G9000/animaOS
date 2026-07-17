"""Portable ANIMA CORE filesystem key orchestration."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from anima_server.services.corefs.types import (
    KeyPurpose,
    KeyslotStatus,
    PayloadScope,
    WrappingPath,
)

if TYPE_CHECKING:
    from anima_server.services.corefs.logical import (
        CORE_FS_MIGRATION_WRITE_FROZEN,
        CoreFsValidationSnapshot,
    )

__all__ = [
    "CORE_FS_MIGRATION_WRITE_FROZEN",
    "CoreFsValidationSnapshot",
    "KeyPurpose",
    "KeyslotStatus",
    "PayloadScope",
    "WrappingPath",
]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    if name in {"CORE_FS_MIGRATION_WRITE_FROZEN", "CoreFsValidationSnapshot"}:
        logical_module = import_module("anima_server.services.corefs.logical")
        value = getattr(logical_module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
