from __future__ import annotations

import re
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


def normalize_logical_path(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip().replace("\\", "/")
    if normalized in {"", "/"}:
        return ""
    if _WINDOWS_DRIVE_RE.match(normalized) or normalized.startswith("/"):
        raise ValueError(f"{field_name} must be a CoreFS logical path, not a host filesystem path.")
    parts = [part for part in normalized.split("/") if part]
    if any(part in {".", ".."} or "\x00" in part for part in parts):
        raise ValueError(f"{field_name} must not contain traversal or NUL segments.")
    return "/".join(parts)


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
