#!/usr/bin/env python3
"""Verify exact legal artifacts are staged in the desktop release resources."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

LEGAL_FILES = {
    "THIRD_PARTY_NOTICES.md": "legal/THIRD_PARTY_NOTICES.md",
    "third_party/licenses/Apache-2.0.txt": "legal/licenses/Apache-2.0.txt",
    "third_party/notices/openai-codex-NOTICE.txt": "legal/notices/openai-codex-NOTICE.txt",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--release-root",
        type=Path,
        default=Path("apps/desktop/src-tauri/resources/runtime"),
    )
    args = parser.parse_args()
    root = args.root.resolve()
    release_root = args.release_root
    if not release_root.is_absolute():
        release_root = root / release_root
    errors: list[str] = []

    for source_relative, staged_relative in LEGAL_FILES.items():
        source = root / source_relative
        staged = release_root / staged_relative
        if not source.is_file():
            errors.append(f"missing legal source: {source_relative}")
            continue
        if not staged.is_file():
            errors.append(f"missing staged legal artifact: {staged_relative}")
            continue
        if sha256(source) != sha256(staged):
            errors.append(f"staged legal artifact hash mismatch: {staged_relative}")

    tauri_path = root / "apps/desktop/src-tauri/tauri.conf.json"
    try:
        resources = json.loads(tauri_path.read_text(encoding="utf-8"))["bundle"]["resources"]
    except (OSError, KeyError, json.JSONDecodeError) as error:
        errors.append(f"cannot inspect Tauri resource mapping: {error}")
    else:
        if not resource_map_includes_legal_tree(resources):
            errors.append("Tauri resource map does not include resources/runtime/legal/")

    if errors:
        print("CoreFS release notice check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("CoreFS release notices are staged with exact hashes and included by Tauri.")
    return 0


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resource_map_includes_legal_tree(resources: object) -> bool:
    if not isinstance(resources, dict):
        return False
    for source, destination in resources.items():
        source = str(source).replace("\\", "/").rstrip("/")
        destination = str(destination).replace("\\", "/").rstrip("/")
        if source in {"resources/runtime", "resources/runtime/legal"} and destination in {
            "runtime",
            "runtime/legal",
        }:
            return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
