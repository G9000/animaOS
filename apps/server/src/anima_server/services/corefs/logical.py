from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import anima_core

CORE_FS_MIGRATION_WRITE_FROZEN = "corefs_migration_write_frozen"
CoreFsSearchState = Literal["missing", "building", "ready", "degraded"]


@dataclass(frozen=True, slots=True)
class CoreFsValidationSnapshot:
    generation: int
    catalog_hash: str

    @classmethod
    def from_native(cls, value: dict[str, object]) -> CoreFsValidationSnapshot:
        try:
            generation = int(value["generation"])
            catalog_hash = str(value["catalogHash"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid CoreFS validation snapshot") from exc
        if generation <= 0 or not catalog_hash:
            raise ValueError("invalid CoreFS validation snapshot")
        return cls(generation=generation, catalog_hash=catalog_hash)


@dataclass(frozen=True, slots=True)
class CoreFsGlobCursor:
    after: str


@dataclass(frozen=True, slots=True)
class CoreFsGrepCursor:
    path: str
    byte_offset: int | None = None
    walk_after: str | None = None


def select_validation_snapshot(
    *,
    corefs_session: Any,
    keys: object,
) -> CoreFsValidationSnapshot:
    return CoreFsValidationSnapshot.from_native(
        corefs_session.validation_snapshot(keys)
    )


def stat_v1(
    *,
    corefs_session: Any,
    keys: object,
    selected: CoreFsValidationSnapshot,
    path: str,
) -> bytes:
    return bytes(
        corefs_session.stat_v1(
            keys,
            selected.generation,
            selected.catalog_hash,
            path,
        )
    )


def list_v1(
    *,
    corefs_session: Any,
    keys: object,
    selected: CoreFsValidationSnapshot,
    path: str,
    cursor_after: str | None = None,
    limit: int = 100,
    response_bytes: int | None = None,
) -> bytes:
    return bytes(
        corefs_session.list_v1(
            keys,
            selected.generation,
            selected.catalog_hash,
            path,
            cursor_after,
            limit,
            response_bytes=response_bytes,
        )
    )


def walk_v1(
    *,
    corefs_session: Any,
    keys: object,
    selected: CoreFsValidationSnapshot,
    root: str,
    cursor_after: str | None = None,
    page_size: int = 100,
    include_directories: bool = True,
    response_bytes: int | None = None,
) -> bytes:
    return bytes(
        corefs_session.walk_v1(
            keys,
            selected.generation,
            selected.catalog_hash,
            root,
            cursor_after,
            page_size,
            include_directories,
            response_bytes=response_bytes,
        )
    )


def glob_v1(
    *,
    corefs_session: Any,
    keys: object,
    selected: CoreFsValidationSnapshot,
    root: str,
    pattern: str,
    max_results: int = 100,
    cursor: CoreFsGlobCursor | None = None,
    response_bytes: int | None = None,
) -> bytes:
    return bytes(
        corefs_session.glob_v1(
            keys,
            selected.generation,
            selected.catalog_hash,
            root,
            pattern,
            max_results,
            cursor.after if cursor is not None else None,
            response_bytes=response_bytes,
        )
    )


def grep_v1(
    *,
    corefs_session: Any,
    keys: object,
    selected: CoreFsValidationSnapshot,
    root: str,
    query: str,
    regex: bool = False,
    max_files: int = 1000,
    max_matches: int = 100,
    max_line_bytes: int = 4096,
    cursor: CoreFsGrepCursor | None = None,
    response_bytes: int | None = None,
) -> bytes:
    return bytes(
        corefs_session.grep_v1(
            keys,
            selected.generation,
            selected.catalog_hash,
            root,
            query,
            regex,
            max_files,
            max_matches,
            max_line_bytes,
            cursor.path if cursor is not None else None,
            cursor.byte_offset if cursor is not None else None,
            cursor.walk_after if cursor is not None else None,
            response_bytes=response_bytes,
        )
    )


def read_chunk_v1(
    *,
    corefs_session: Any,
    keys: object,
    selected: CoreFsValidationSnapshot,
    path: str,
    offset: int = 0,
    max_bytes: int = 65536,
    response_bytes: int | None = None,
) -> bytes | None:
    chunk = corefs_session.read_chunk_v1(
        keys,
        selected.generation,
        selected.catalog_hash,
        path,
        offset,
        max_bytes,
        response_bytes=response_bytes,
    )
    return bytes(chunk) if chunk is not None else None


def search_readiness_v1(
    *,
    corefs_session: Any,
    keys: object,
    selected: CoreFsValidationSnapshot,
    state: CoreFsSearchState,
    index_generation: int | None = None,
) -> bytes:
    return bytes(
        corefs_session.search_readiness_v1(
            keys,
            selected.generation,
            selected.catalog_hash,
            state,
            index_generation,
        )
    )


def frozen_mutation_result(operation: str) -> dict[str, object]:
    return {
        "ok": False,
        "operation": operation,
        "code": CORE_FS_MIGRATION_WRITE_FROZEN,
    }


def mkdir(*args: object, **kwargs: object) -> dict[str, object]:
    return dict(anima_core.corefs_mkdir(*args, **kwargs))


def create_file(*args: object, **kwargs: object) -> dict[str, object]:
    return dict(anima_core.corefs_create_file(*args, **kwargs))


def write_file(*args: object, **kwargs: object) -> dict[str, object]:
    return dict(anima_core.corefs_write_file(*args, **kwargs))


def apply_patch(*args: object, **kwargs: object) -> dict[str, object]:
    return dict(anima_core.corefs_apply_patch(*args, **kwargs))


def move(*args: object, **kwargs: object) -> dict[str, object]:
    return dict(anima_core.corefs_move(*args, **kwargs))


def trash(*args: object, **kwargs: object) -> dict[str, object]:
    return dict(anima_core.corefs_trash(*args, **kwargs))


def restore(*args: object, **kwargs: object) -> dict[str, object]:
    return dict(anima_core.corefs_restore(*args, **kwargs))
