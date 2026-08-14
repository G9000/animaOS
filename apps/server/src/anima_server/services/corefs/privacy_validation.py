"""Bounded release validation for private plaintext outside canonical CoreFS."""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_SCAN_CHUNK_BYTES = 1024 * 1024
_MAX_MARKERS = 128
_MAX_MARKER_BYTES = 4096


@dataclass(frozen=True, slots=True)
class PrivacyScanHit:
    root_label: str
    relative_path: str
    marker_label: str


def scan_private_markers(
    *,
    roots: Mapping[str, Path],
    markers: Mapping[str, bytes],
) -> tuple[PrivacyScanHit, ...]:
    """Stream exact seeded markers across Runtime/cache/log/index roots.

    Results contain caller-provided opaque labels only. Marker bytes and
    absolute machine paths are never returned, persisted, or logged.
    """
    normalized_markers = _validated_markers(markers)
    hits: list[PrivacyScanHit] = []
    for root_label, root in sorted(roots.items()):
        if not root_label or not isinstance(root, Path):
            raise ValueError("Privacy scan roots require opaque labels and paths.")
        if not root.exists():
            continue
        if root.is_symlink():
            raise RuntimeError("Privacy scan root must not be a symbolic link.")
        paths = (root,) if root.is_file() else tuple(sorted(root.rglob("*")))
        for path in paths:
            if path.is_symlink():
                raise RuntimeError("Privacy scan encountered a symbolic link.")
            if not path.is_file():
                continue
            relative_path = path.name if path == root else path.relative_to(root).as_posix()
            for marker_label in _scan_file(path, normalized_markers):
                hits.append(
                    PrivacyScanHit(
                        root_label=root_label,
                        relative_path=relative_path,
                        marker_label=marker_label,
                    )
                )
    return tuple(hits)


def _validated_markers(markers: Mapping[str, bytes]) -> tuple[tuple[str, bytes], ...]:
    if not markers or len(markers) > _MAX_MARKERS:
        raise ValueError("Privacy scan marker inventory is invalid.")
    normalized: list[tuple[str, bytes]] = []
    for label, marker in sorted(markers.items()):
        if (
            not label
            or not isinstance(marker, bytes)
            or not marker
            or len(marker) > _MAX_MARKER_BYTES
        ):
            raise ValueError("Privacy scan marker inventory is invalid.")
        normalized.append((label, marker))
    return tuple(normalized)


def _scan_file(
    path: Path,
    markers: tuple[tuple[str, bytes], ...],
) -> tuple[str, ...]:
    before = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode):
        raise RuntimeError("Privacy scan target must remain a regular file.")
    open_flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        open_flags |= os.O_NOFOLLOW
    found: set[str] = set()
    overlap_size = max(len(marker) for _label, marker in markers) - 1
    overlap = b""
    descriptor = os.open(path, open_flags)
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            while chunk := handle.read(_SCAN_CHUNK_BYTES):
                window = overlap + chunk
                for label, marker in markers:
                    if label not in found and marker in window:
                        found.add(label)
                overlap = window[-overlap_size:] if overlap_size else b""
    finally:
        os.close(descriptor)
    after = path.stat(follow_symlinks=False)
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after:
        raise RuntimeError("Privacy scan target changed during validation.")
    return tuple(sorted(found))
