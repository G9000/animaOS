from __future__ import annotations

import re
import unicodedata
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

CoreFsOperation = Literal[
    "stat",
    "list",
    "walk",
    "glob",
    "grep",
    "read",
    "search_readiness",
    "mkdir",
    "create_file",
    "write_file",
    "apply_patch",
    "move",
    "trash",
    "restore",
]

CoreFsPrincipalKind = Literal["user", "anima", "client"]

_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_MAX_LOGICAL_PATH_BYTES = 32 * 1024
_MAX_PORTABLE_NAME_BYTES = 255
_RESERVED_COMPONENTS = frozenset(
    {
        ".anima",
        ".corefs",
        "objects",
        "fs",
        "catalogs",
        "head",
        "validation_head",
        "manifest.json",
        "soul",
        "soul.db",
        "cutover_receipt",
        "cutover_complete",
        "commit.lock",
    }
)
_AMBIGUOUS_PATH_CHARACTERS = frozenset(
    {
        "\u2044",
        "\u2215",
        "\u29f8",
        "\uff0f",
        "\uff3c",
        "\ufeff",
    }
)


def normalize_logical_path(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if len(stripped.encode("utf-8")) > _MAX_LOGICAL_PATH_BYTES:
        raise ValueError(f"{field_name} exceeds the CoreFS logical path byte limit.")
    if stripped == "":
        return ""
    if _WINDOWS_DRIVE_RE.match(stripped) or stripped.startswith(("/", "\\")):
        raise ValueError(f"{field_name} must be a CoreFS logical path, not a host filesystem path.")
    if "\x00" in stripped:
        raise ValueError(f"{field_name} must not contain NUL.")
    if "\\" in stripped:
        raise ValueError(f"{field_name} must not contain host path separators.")
    if _has_uri_scheme(stripped):
        raise ValueError(f"{field_name} must not use a URI or foreign backend path form.")
    normalized = stripped
    parts = [part for part in normalized.split("/") if part]
    if len(parts) != len(normalized.split("/")):
        raise ValueError(f"{field_name} must not contain empty path components.")
    for part in parts:
        if part in {".", ".."}:
            raise ValueError(f"{field_name} must not contain traversal segments.")
        if len(part.encode("utf-8")) > _MAX_PORTABLE_NAME_BYTES:
            raise ValueError(f"{field_name} contains a component over the byte limit.")
        if any(char in _AMBIGUOUS_PATH_CHARACTERS for char in part) or any(
            "\u202a" <= char <= "\u202e" or "\u2066" <= char <= "\u2069"
            for char in part
        ):
            raise ValueError(f"{field_name} contains an ambiguous Unicode path character.")
        if any(unicodedata.category(char).startswith("C") for char in part):
            raise ValueError(f"{field_name} contains a control character.")
        if unicodedata.normalize("NFC", part) != part:
            raise ValueError(f"{field_name} must use Unicode NFC.")
        if part.casefold() in _RESERVED_COMPONENTS:
            raise ValueError(f"{field_name} contains a reserved CoreFS component.")
    return "/".join(parts)


def _has_uri_scheme(value: str) -> bool:
    first_component = value.split("/", 1)[0]
    scheme, separator, _rest = first_component.partition(":")
    if not separator or not scheme:
        return False
    return scheme[0].isalpha() and all(
        char.isalnum() or char in {"+", "-", "."} for char in scheme
    )


class CoreFsOperationRequest(BaseModel):
    operation: CoreFsOperation
    path: str | None = None
    root: str | None = None
    pattern: str | None = None
    query: str | None = None
    cursorAfter: str | None = None
    globCursorAfter: str | None = None
    grepCursorPath: str | None = None
    grepCursorByteOffset: int | None = Field(default=None, ge=0)
    grepCursorWalkAfter: str | None = None
    limit: int = Field(default=100, ge=1, le=1000)
    pageSize: int = Field(default=100, ge=1, le=1000)
    maxResults: int = Field(default=100, ge=1, le=1000)
    maxFiles: int = Field(default=1000, ge=1, le=10000)
    maxMatches: int = Field(default=100, ge=1, le=1000)
    maxLineBytes: int = Field(default=4096, ge=128, le=65536)
    offset: int = Field(default=0, ge=0)
    maxBytes: int = Field(default=65536, ge=1, le=1048576)
    responseBytes: int | None = Field(default=None, ge=1024, le=10485760)
    regex: bool = False
    includeDirectories: bool = True
    searchState: Literal["missing", "building", "ready", "degraded"] = "missing"
    indexGeneration: int | None = Field(default=None, ge=0)

    @field_validator(
        "path",
        "root",
        "cursorAfter",
        "globCursorAfter",
        "grepCursorPath",
        "grepCursorWalkAfter",
    )
    @classmethod
    def validate_logical_paths(cls, value: str | None, info: Any) -> str | None:
        return normalize_logical_path(value, field_name=str(info.field_name))


class CoreFsPrincipalResponse(BaseModel):
    kind: CoreFsPrincipalKind
    id: str
    userId: int
    installDigest: str | None = None


class CoreFsSelectedSnapshotResponse(BaseModel):
    generation: int
    catalogHash: str


class CoreFsOperationResponse(BaseModel):
    principal: CoreFsPrincipalResponse
    operation: CoreFsOperation
    selected: CoreFsSelectedSnapshotResponse | None = None
    result: dict[str, Any] | None = None
