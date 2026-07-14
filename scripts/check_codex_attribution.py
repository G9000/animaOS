#!/usr/bin/env python3
"""Validate Codex adaptation attribution without relying on an upstream checkout."""

from __future__ import annotations

import argparse
import os
import re
import sys
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

PINNED_COMMIT = "9e552e9d15ba52bed7077d5357f3e18e330f8f38"
INVENTORY = {
    "packages/anima-file-tools/src/limits.rs": "codex-rs/file-system/src/lib.rs",
    "packages/anima-file-tools/src/read.rs": "codex-rs/file-system/src/lib.rs",
    "packages/anima-file-tools/src/walk.rs": "codex-rs/file-system/src/lib.rs",
    "packages/anima-file-tools/src/search.rs": "codex-rs/file-system/src/lib.rs",
    "packages/anima-file-tools/src/text.rs": "codex-rs/file-system/src/lib.rs",
    "packages/anima-file-tools/src/patch/parser.rs": "codex-rs/apply-patch/src/parser.rs",
    "packages/anima-file-tools/src/patch/planner.rs": "codex-rs/apply-patch/src/seek_sequence.rs",
}
REQUIRED_LEGAL_TEXT = {
    "third_party/licenses/Apache-2.0.txt": (
        "Apache License",
        "Version 2.0, January 2004",
        "http://www.apache.org/licenses/",
    ),
    "third_party/notices/openai-codex-NOTICE.txt": (
        "OpenAI Codex",
        "Copyright\u00a02025\u00a0OpenAI",
    ),
}
SOURCE_SUFFIXES = {".rs", ".py", ".ts", ".tsx", ".js", ".mjs", ".yml", ".yaml"}
SKIP_DIRECTORIES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".worktrees",
    "dist",
    "node_modules",
    "target",
}
SIBLING_REFERENCE = re.compile(r"(?:^|[\"'])(?:\.\.[/\\])+codex(?:[/\\]|[\"'])", re.IGNORECASE)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    errors: list[str] = []
    repository_files = list(iter_repository_files(root))

    notices = require_text(root, "THIRD_PARTY_NOTICES.md", errors)
    if notices:
        for local, upstream in INVENTORY.items():
            for expected in (f"`{local}`", f"`{upstream}`", PINNED_COMMIT):
                if expected not in notices:
                    errors.append(f"THIRD_PARTY_NOTICES.md is missing {expected}")

    for relative, fragments in REQUIRED_LEGAL_TEXT.items():
        text = require_text(root, relative, errors)
        for fragment in fragments:
            if text and fragment not in text:
                errors.append(f"{relative} is missing required text: {fragment}")

    adapted_files: set[str] = set()
    for path in source_files(repository_files):
        text = path.read_text(encoding="utf-8", errors="replace")
        relative = path.relative_to(root).as_posix()
        if (
            path.resolve() != Path(__file__).resolve()
            and "adapted from openai codex" in text.lower()
        ):
            adapted_files.add(relative)
        if SIBLING_REFERENCE.search(text):
            errors.append(f"source/build/test file references a sibling Codex checkout: {relative}")

    for relative in INVENTORY:
        text = require_text(root, relative, errors)
        if not text:
            continue
        if "SPDX-License-Identifier: Apache-2.0" not in text:
            errors.append(f"adapted file is missing Apache-2.0 SPDX header: {relative}")
        if PINNED_COMMIT not in text or "OpenAI Codex" not in text:
            errors.append(f"adapted file is missing pinned Codex attribution: {relative}")
        adapted_files.add(relative)

    unlisted = adapted_files.difference(INVENTORY)
    if unlisted:
        errors.extend(f"adapted file is absent from inventory: {path}" for path in sorted(unlisted))

    for manifest in (path for path in repository_files if path.name == "Cargo.toml"):
        try:
            data = tomllib.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as error:
            errors.append(f"cannot inspect {manifest.relative_to(root)}: {error}")
            continue
        for dependency_path in find_path_dependencies(data):
            resolved = (manifest.parent / dependency_path).resolve()
            if not resolved.is_relative_to(root):
                errors.append(
                    f"Cargo path dependency escapes repository: {manifest.relative_to(root)} -> {dependency_path}"
                )

    if errors:
        print("Codex attribution check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"Codex attribution check passed for {len(INVENTORY)} adapted files.")
    return 0


def require_text(root: Path, relative: str, errors: list[str]) -> str:
    path = root / relative
    if not path.is_file():
        errors.append(f"missing required file: {relative}")
        return ""
    return path.read_text(encoding="utf-8")


def iter_repository_files(root: Path) -> Iterable[Path]:
    for directory, directories, filenames in os.walk(root):
        directories[:] = [name for name in directories if name not in SKIP_DIRECTORIES]
        base = Path(directory)
        for filename in filenames:
            yield base / filename


def source_files(paths: Iterable[Path]) -> Iterable[Path]:
    return (path for path in paths if path.suffix in SOURCE_SUFFIXES)


def find_path_dependencies(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        path = value.get("path")
        if isinstance(path, str):
            yield path
        for nested in value.values():
            yield from find_path_dependencies(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from find_path_dependencies(nested)


if __name__ == "__main__":
    raise SystemExit(main())
