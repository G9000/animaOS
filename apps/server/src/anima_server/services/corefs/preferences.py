"""Portable preference validation, encrypted persistence, and authority gating."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from threading import RLock
from typing import Any

from sqlalchemy.orm import Session

from anima_server.services.corefs.diary_migration import (
    DiaryMigrationError,
    read_prepared_writing_body,
    read_prepared_writing_snapshot,
)
from anima_server.services.corefs.formats import decode_preferences_document
from anima_server.services.corefs.writing_source import prepare_writing_source_catalog

PORTABLE_PREFERENCE_KEYS = frozenset(
    {
        "theme",
        "background",
        "translateLanguage",
        "ascii",
        "clockFormat",
        "dashboardNodePositions",
        "dashboardClosedNodes",
        "bgm",
    }
)
_MAX_PATCH_BYTES = 256 * 1024
_CORE_OBJECT_URI = re.compile(r"corefs://object/[0-7][0-9A-HJKMNP-TV-Z]{25}")
_locks_guard = RLock()
_locks: dict[int, RLock] = {}


class PortablePreferenceError(RuntimeError):
    pass


@contextmanager
def portable_preference_lock(user_id: int) -> Iterator[None]:
    with _locks_guard:
        lock = _locks.setdefault(user_id, RLock())
    with lock:
        yield


def validate_portable_preference_patch(values: Mapping[str, Any]) -> dict[str, Any]:
    patch = dict(values)
    unknown = sorted(set(patch) - PORTABLE_PREFERENCE_KEYS)
    if unknown:
        raise PortablePreferenceError(
            "Unsupported portable preference keys: " + ", ".join(unknown)
        )
    try:
        encoded = json.dumps(
            patch,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PortablePreferenceError("Portable preferences must be canonical JSON.") from exc
    if len(encoded) > _MAX_PATCH_BYTES:
        raise PortablePreferenceError("Portable preference patch exceeds 256 KiB.")

    if "theme" in patch and patch["theme"] not in {"dark", "light", "system"}:
        raise PortablePreferenceError("Theme must be dark, light, or system.")
    if "clockFormat" in patch and patch["clockFormat"] not in {"12h", "24h"}:
        raise PortablePreferenceError("Clock format must be 12h or 24h.")
    language = patch.get("translateLanguage")
    if language is not None and (
        not isinstance(language, str) or not language or len(language) > 16
    ):
        raise PortablePreferenceError("Translation language is invalid.")
    if "ascii" in patch and not isinstance(patch["ascii"], dict):
        raise PortablePreferenceError("ASCII preferences must be an object.")
    if "dashboardNodePositions" in patch and not isinstance(
        patch["dashboardNodePositions"], dict
    ):
        raise PortablePreferenceError("Dashboard positions must be an object.")
    closed = patch.get("dashboardClosedNodes")
    if closed is not None and (
        not isinstance(closed, list)
        or len(closed) > 1000
        or not all(isinstance(item, str) and len(item) <= 128 for item in closed)
    ):
        raise PortablePreferenceError("Closed dashboard nodes are invalid.")
    if "bgm" in patch:
        bgm = patch["bgm"]
        if not isinstance(bgm, dict) or not isinstance(bgm.get("muted"), bool):
            raise PortablePreferenceError("BGM preferences are invalid.")
        current_id = bgm.get("currentId")
        if current_id is not None and (
            not isinstance(current_id, str) or not current_id.startswith("builtin-")
        ):
            raise PortablePreferenceError("Only bundled BGM selections are portable.")
    if "background" in patch:
        background = patch["background"]
        if not isinstance(background, dict):
            raise PortablePreferenceError("Background preferences must be an object.")
        kind = background.get("type")
        if kind not in {"default", "color", "gradient", "image", "video"}:
            raise PortablePreferenceError("Background type is invalid.")
        value = background.get("value")
        if kind in {"image", "video"} and (
            not isinstance(value, str) or _CORE_OBJECT_URI.fullmatch(value) is None
        ):
            raise PortablePreferenceError(
                "Portable background media must reference an imported Core attachment."
            )
        if isinstance(value, str) and value.startswith(("data:", "blob:", "file:")):
            raise PortablePreferenceError("Browser or host media URLs are not portable.")
    return patch


def read_portable_preferences(*, session: Any) -> dict[str, Any]:
    try:
        objects = read_prepared_writing_snapshot(session=session).objects
    except ValueError as exc:
        if str(exc) == "CoreFS validation snapshot is missing":
            return {}
        raise PortablePreferenceError("Encrypted preferences could not be opened.") from exc
    matches = [item for item in objects if item.kind == "preferences"]
    if len(matches) != 1:
        if not matches:
            return {}
        raise PortablePreferenceError("Core contains conflicting preference objects.")
    try:
        decoded = decode_preferences_document(
            read_prepared_writing_body(session=session, item=matches[0])
        )
    except (DiaryMigrationError, ValueError) as exc:
        raise PortablePreferenceError("Encrypted preferences failed authentication.") from exc
    return dict(decoded.values)


def update_portable_preferences(
    *,
    session: Any,
    db: Session,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    patch = validate_portable_preference_patch(values)
    with portable_preference_lock(int(session.user_id)):
        try:
            prepare_writing_source_catalog(
                session=session,
                db=db,
                portable_preference_updates=patch,
            )
            current = read_portable_preferences(session=session)
        except (DiaryMigrationError, ValueError) as exc:
            raise PortablePreferenceError(
                "Encrypted portable preferences could not be updated."
            ) from exc
    if any(current.get(key) != value for key, value in patch.items()):
        raise PortablePreferenceError("Encrypted preference verification failed.")
    return current
