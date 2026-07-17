from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

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


def select_validation_snapshot(
    *,
    core_root: str,
    core_id: str,
    keys: object,
) -> CoreFsValidationSnapshot:
    return CoreFsValidationSnapshot.from_native(
        anima_core.corefs_validation_snapshot(core_root, core_id, keys)
    )


def stat_v1(
    *,
    core_root: str,
    core_id: str,
    keys: object,
    selected: CoreFsValidationSnapshot,
    path: str,
) -> bytes:
    return bytes(
        anima_core.corefs_stat_v1(
            core_root,
            core_id,
            keys,
            selected.generation,
            selected.catalog_hash,
            path,
        )
    )


def list_v1(
    *,
    core_root: str,
    core_id: str,
    keys: object,
    selected: CoreFsValidationSnapshot,
    path: str,
    cursor_after: str | None = None,
    limit: int = 100,
    response_bytes: int | None = None,
) -> bytes:
    return bytes(
        anima_core.corefs_list_v1(
            core_root,
            core_id,
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
    core_root: str,
    core_id: str,
    keys: object,
    selected: CoreFsValidationSnapshot,
    root: str,
    cursor_after: str | None = None,
    page_size: int = 100,
    include_directories: bool = True,
    response_bytes: int | None = None,
) -> bytes:
    return bytes(
        anima_core.corefs_walk_v1(
            core_root,
            core_id,
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
    core_root: str,
    core_id: str,
    keys: object,
    selected: CoreFsValidationSnapshot,
    root: str,
    pattern: str,
    max_results: int = 100,
    response_bytes: int | None = None,
) -> bytes:
    return bytes(
        anima_core.corefs_glob_v1(
            core_root,
            core_id,
            keys,
            selected.generation,
            selected.catalog_hash,
            root,
            pattern,
            max_results,
            response_bytes=response_bytes,
        )
    )


def grep_v1(
    *,
    core_root: str,
    core_id: str,
    keys: object,
    selected: CoreFsValidationSnapshot,
    root: str,
    query: str,
    regex: bool = False,
    max_files: int = 1000,
    max_matches: int = 100,
    max_line_bytes: int = 4096,
    response_bytes: int | None = None,
) -> bytes:
    return bytes(
        anima_core.corefs_grep_v1(
            core_root,
            core_id,
            keys,
            selected.generation,
            selected.catalog_hash,
            root,
            query,
            regex,
            max_files,
            max_matches,
            max_line_bytes,
            response_bytes=response_bytes,
        )
    )


def read_chunk_v1(
    *,
    core_root: str,
    core_id: str,
    keys: object,
    selected: CoreFsValidationSnapshot,
    path: str,
    offset: int = 0,
    max_bytes: int = 65536,
    response_bytes: int | None = None,
) -> bytes | None:
    chunk = anima_core.corefs_read_chunk_v1(
        core_root,
        core_id,
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
    core_root: str,
    core_id: str,
    keys: object,
    selected: CoreFsValidationSnapshot,
    state: CoreFsSearchState,
    index_generation: int | None = None,
) -> bytes:
    return bytes(
        anima_core.corefs_search_readiness_v1(
            core_root,
            core_id,
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


def mkdir() -> dict[str, object]:
    return dict(anima_core.corefs_mkdir())


def create_file() -> dict[str, object]:
    return dict(anima_core.corefs_create_file())


def write_file() -> dict[str, object]:
    return dict(anima_core.corefs_write_file())


def apply_patch() -> dict[str, object]:
    return dict(anima_core.corefs_apply_patch())


def move() -> dict[str, object]:
    return dict(anima_core.corefs_move())


def trash() -> dict[str, object]:
    return dict(anima_core.corefs_trash())


def restore() -> dict[str, object]:
    return dict(anima_core.corefs_restore())
