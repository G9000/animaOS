from __future__ import annotations

import re
import unicodedata
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CoreFsOperation = Literal[
    "stat",
    "list",
    "walk",
    "glob",
    "grep",
    "read",
    "search",
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
CoreFsSearchMode = Literal["exact", "text", "semantic"]

_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_MAX_LOGICAL_PATH_BYTES = 32 * 1024
_MAX_PORTABLE_NAME_BYTES = 255
_MAX_NATIVE_U64 = (1 << 64) - 1
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
    if len(value.encode("utf-8")) > _MAX_LOGICAL_PATH_BYTES:
        raise ValueError(f"{field_name} exceeds the CoreFS logical path byte limit.")
    if value == "":
        return ""
    if _WINDOWS_DRIVE_RE.match(value) or value.startswith(("/", "\\")):
        raise ValueError(f"{field_name} must be a CoreFS logical path, not a host filesystem path.")
    if "\x00" in value:
        raise ValueError(f"{field_name} must not contain NUL.")
    if "\\" in value:
        raise ValueError(f"{field_name} must not contain host path separators.")
    if _has_uri_scheme(value):
        raise ValueError(f"{field_name} must not use a URI or foreign backend path form.")
    normalized = value
    parts = [part for part in normalized.split("/") if part]
    if len(parts) != len(normalized.split("/")):
        raise ValueError(f"{field_name} must not contain empty path components.")
    for part in parts:
        if part in {".", ".."}:
            raise ValueError(f"{field_name} must not contain traversal segments.")
        if len(part.encode("utf-8")) > _MAX_PORTABLE_NAME_BYTES:
            raise ValueError(f"{field_name} contains a component over the byte limit.")
        if any(char in _AMBIGUOUS_PATH_CHARACTERS for char in part) or any(
            "\u202a" <= char <= "\u202e" or "\u2066" <= char <= "\u2069" for char in part
        ):
            raise ValueError(f"{field_name} contains an ambiguous Unicode path character.")
        if any(unicodedata.category(char) == "Cc" for char in part):
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
    return (
        scheme[0].isascii()
        and scheme[0].isalpha()
        and all((char.isascii() and char.isalnum()) or char in {"+", "-", "."} for char in scheme)
    )


class CoreFsOperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: CoreFsOperation
    path: str | None = None
    root: str | None = None
    pattern: str | None = None
    query: str | None = None
    searchMode: CoreFsSearchMode = "text"
    cursorAfter: str | None = None
    globCursorAfter: str | None = None
    grepCursorPath: str | None = None
    grepCursorByteOffset: int | None = Field(default=None, ge=0, le=_MAX_NATIVE_U64)
    grepCursorWalkAfter: str | None = None
    cursorGeneration: int | None = Field(default=None, ge=1)
    limit: int = Field(default=100, ge=1, le=1000)
    pageSize: int = Field(default=100, ge=1, le=1000)
    maxResults: int = Field(default=100, ge=1, le=1000)
    maxFiles: int = Field(default=1000, ge=1, le=10000)
    maxMatches: int = Field(default=100, ge=1, le=1000)
    maxLineBytes: int = Field(default=4096, ge=128, le=65536)
    offset: int = Field(default=0, ge=0, le=_MAX_NATIVE_U64)
    maxBytes: int = Field(default=65536, ge=1, le=1048576)
    responseBytes: int | None = Field(default=None, ge=1024, le=10485760)
    regex: bool = False
    includeDirectories: bool = True

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

    @model_validator(mode="after")
    def validate_cursor_generation(self) -> CoreFsOperationRequest:
        if self.operation == "search" and not (self.query or "").strip():
            raise ValueError("query is required for search.")
        if self.grepCursorPath is None and (
            self.grepCursorByteOffset is not None or self.grepCursorWalkAfter is not None
        ):
            raise ValueError("grep cursor offsets require grepCursorPath.")

        cursor_present = (
            (self.operation in {"list", "walk"} and self.cursorAfter is not None)
            or (self.operation == "glob" and self.globCursorAfter is not None)
            or (self.operation == "grep" and self.grepCursorPath is not None)
        )
        if cursor_present and self.cursorGeneration is None:
            raise ValueError("cursorGeneration is required with a continuation cursor.")
        if self.cursorGeneration is not None and not cursor_present:
            raise ValueError("cursorGeneration requires a continuation cursor.")
        return self


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
