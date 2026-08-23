"""One-way authenticated authority activation for the first supported Core release."""

from __future__ import annotations

import json
import re
import secrets
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from threading import RLock
from typing import Any

from anima_server.services.core import get_manifest_path, update_core_manifest

PORTABLE_CORE_RELEASE = 1
_RELEASE_FIELD = "portable_core_release"
_AUTHORITY_FIELD = "corefs_authority"
_AUTHORITY_VERSION = 1
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_authority_lock = RLock()
# Manifest paths this process has successfully read (or created at startup),
# plus those observed in the irreversible authoritative state. Once observed,
# a manifest that disappears or reports a lesser state is damage and must fail
# closed, not read as a never-activated environment (PR #148 review, P1).
_observed_manifest_paths: set[str] = set()
_observed_authoritative_paths: set[str] = set()


def mark_manifest_observed(path: Any = None, *, authoritative: bool = False) -> None:
    resolved = str(get_manifest_path() if path is None else path)
    with _authority_lock:
        _observed_manifest_paths.add(resolved)
        if authoritative:
            _observed_authoritative_paths.add(resolved)


def latch_manifest_authority(manifest: object, path: Any = None) -> None:
    """Latch authority from an already-loaded manifest mapping.

    Startup loads the manifest before any authority read, so it must pass the
    state it already holds: otherwise a restart against an already-activated
    Core latches only the path, and a parseable non-authoritative replacement
    before the first authority-dependent request would reopen legacy content
    and consent fallbacks (PR #148 review, P1). A malformed authority record
    only skips the latch here; the next read still fails closed on it.
    """
    authoritative = False
    if isinstance(manifest, dict) and manifest.get(_RELEASE_FIELD) == PORTABLE_CORE_RELEASE:
        with suppress(AuthorityStateError):
            authoritative = _record_from_manifest(manifest).state is AuthorityState.AUTHORITATIVE
    mark_manifest_observed(path, authoritative=authoritative)


def _reject_authority_regression(path: Any, state: AuthorityState | None) -> None:
    """Reject a manifest that lost authority this process already observed.

    CoreFS activation is irreversible, so a manifest replaced in place with
    valid JSON that reports a lesser state — an empty object, a manifest
    without the release field, or an older pending-activation record — is
    damage rather than evidence that authority was never activated.
    """
    resolved = str(path)
    with _authority_lock:
        was_authoritative = resolved in _observed_authoritative_paths
    if was_authoritative and state is not AuthorityState.AUTHORITATIVE:
        raise AuthorityStateError(
            "ANIMA CORE manifest lost CoreFS authority this process already observed"
        )

CONTENT_AUTHORITY_FAMILIES = (
    "account",
    "assets",
    "conversations",
    "diary",
    "documents",
    "knowledge",
    "notes",
    "preferences",
    "tasks",
)


class AuthorityState(StrEnum):
    PENDING_ACTIVATION = "pending-activation"
    AUTHORITATIVE = "authoritative"


class AuthorityStateError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AuthorityRecord:
    state: AuthorityState
    prepared_generation: int | None = None
    prepared_catalog_hash: str | None = None
    authority_epoch: int | None = None
    authoritative_generation: int | None = None
    authoritative_catalog_hash: str | None = None

    def to_manifest(self) -> dict[str, object]:
        value: dict[str, object] = {
            "version": _AUTHORITY_VERSION,
            "state": self.state.value,
        }
        if self.prepared_generation is not None:
            value["preparedGeneration"] = self.prepared_generation
            value["preparedCatalogHash"] = self.prepared_catalog_hash
            value["authorityEpoch"] = self.authority_epoch
        if self.authoritative_generation is not None:
            value["authoritativeGeneration"] = self.authoritative_generation
            value["authoritativeCatalogHash"] = self.authoritative_catalog_hash
        return value


def read_authority_record() -> AuthorityRecord:
    path = get_manifest_path()
    if not path.is_file():
        raise AuthorityStateError("ANIMA CORE manifest is unavailable")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthorityStateError("ANIMA CORE manifest is unavailable or invalid") from exc
    if not isinstance(manifest, dict):
        raise AuthorityStateError("ANIMA CORE manifest is invalid")
    mark_manifest_observed(path)
    if manifest.get(_RELEASE_FIELD) != PORTABLE_CORE_RELEASE:
        _reject_authority_regression(path, None)
        raise AuthorityStateError(
            "This pre-release Core is not supported; create a new first-release ANIMA CORE."
        )
    record = _record_from_manifest(manifest)
    _reject_authority_regression(path, record.state)
    mark_manifest_observed(path, authoritative=record.state is AuthorityState.AUTHORITATIVE)
    return record


def core_authority_state_or_none() -> AuthorityState | None:
    """Return the first-release authority state, or None before one exists.

    A manifest that never existed in this process, or a parseable manifest
    without the first-release field, means no CoreFS authority was ever
    activated in this environment (fresh bootstrap, pre-registration, or an
    unsupported pre-release Core that fails closed at unlock). A manifest that
    cannot be parsed, one this process already observed that has since
    disappeared, or one that lost previously observed authority raises instead,
    so consent and authority decisions fail closed rather than falling back to
    legacy state.
    """
    path = get_manifest_path()
    if not path.is_file():
        with _authority_lock:
            observed = str(path) in _observed_manifest_paths
        if observed:
            raise AuthorityStateError(
                "ANIMA CORE manifest disappeared after this process observed it"
            )
        return None
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthorityStateError("ANIMA CORE manifest is unavailable or invalid") from exc
    if not isinstance(manifest, dict):
        raise AuthorityStateError("ANIMA CORE manifest is invalid")
    mark_manifest_observed(path)
    state = (
        _record_from_manifest(manifest).state
        if manifest.get(_RELEASE_FIELD) == PORTABLE_CORE_RELEASE
        else None
    )
    _reject_authority_regression(path, state)
    mark_manifest_observed(path, authoritative=state is AuthorityState.AUTHORITATIVE)
    return state


def prepare_authority_activation(*, generation: int, catalog_hash: str) -> AuthorityRecord:
    _validate_snapshot(generation, catalog_hash)
    with _authority_lock:
        current = read_authority_record()
        if current.state is AuthorityState.AUTHORITATIVE:
            return current
        if current.prepared_generation is not None:
            if (
                current.prepared_generation != generation
                or current.prepared_catalog_hash != catalog_hash
            ):
                raise AuthorityStateError(
                    "CoreFS authority activation snapshot changed after approval"
                )
            return current
        epoch = secrets.randbits(63) or 1
        return _write_record(
            AuthorityRecord(
                state=AuthorityState.PENDING_ACTIVATION,
                prepared_generation=generation,
                prepared_catalog_hash=catalog_hash,
                authority_epoch=epoch,
            ),
            expected=current,
        )


def activate_content_authority(
    *,
    corefs_session: Any,
    keys: object,
    generation: int,
    catalog_hash: str,
) -> dict[str, object]:
    """Activate an already-verified greenfield catalog without a content mutation."""
    current = reconcile_content_authority(corefs_session=corefs_session, keys=keys)
    if current is not None:
        return current
    record = prepare_authority_activation(generation=generation, catalog_hash=catalog_hash)
    if record.state is AuthorityState.AUTHORITATIVE:
        marker = reconcile_content_authority(corefs_session=corefs_session, keys=keys)
        if marker is None:
            raise AuthorityStateError("CoreFS authority marker is unavailable")
        return marker
    if (
        record.prepared_generation is None
        or record.prepared_catalog_hash is None
        or record.authority_epoch is None
    ):
        raise AuthorityStateError("CoreFS authority activation record is incomplete")

    native = getattr(corefs_session, "logical_mutate_v1", None)
    if not callable(native):
        raise AuthorityStateError("native CoreFS authority activation is unavailable")
    now = datetime.now(UTC)
    request = {
        "version": 1,
        "principal": "user",
        "commitMode": "first",
        "cutoverEpoch": record.authority_epoch,
        "selectedGeneration": record.prepared_generation,
        "selectedCatalogHash": record.prepared_catalog_hash,
        "timestampMs": int(now.timestamp() * 1000),
        "timestamp": now.isoformat().replace("+00:00", "Z"),
        "mutation": {"operation": "activate_authority"},
    }
    result = native(
        keys,
        json.dumps(request, separators=(",", ":"), sort_keys=True),
        None,
    )
    if (
        not isinstance(result, dict)
        or result.get("ok") is not True
        or result.get("atomic") is not True
        or result.get("cutoverCommitted") is not True
        or result.get("changes") != []
    ):
        raise AuthorityStateError("native CoreFS authority activation result is invalid")
    marker = reconcile_content_authority(corefs_session=corefs_session, keys=keys)
    if marker is None:
        raise AuthorityStateError("CoreFS authority activation did not publish authority")
    return marker


def reconcile_content_authority(
    *,
    corefs_session: Any,
    keys: object,
) -> dict[str, object] | None:
    """Authenticate native authority and repair a crash after its publication."""
    with _authority_lock:
        record = read_authority_record()
        marker = _read_native_marker(
            corefs_session=corefs_session,
            keys=keys,
            required=(
                record.state is AuthorityState.AUTHORITATIVE or record.authority_epoch is not None
            ),
        )
        if marker is None:
            if record.state is AuthorityState.AUTHORITATIVE:
                raise AuthorityStateError(
                    "authoritative manifest state has no authenticated CoreFS marker"
                )
            return None
        if record.authority_epoch != int(marker["cutoverEpoch"]):
            raise AuthorityStateError("authenticated CoreFS authority epoch is invalid")
        if (
            record.prepared_generation is not None
            and int(marker["generation"]) <= record.prepared_generation
        ):
            raise AuthorityStateError("authenticated CoreFS authority lineage is invalid")

        if record.state is AuthorityState.AUTHORITATIVE:
            if (
                record.authoritative_generation is None
                or record.authoritative_catalog_hash is None
                or int(marker["generation"]) < record.authoritative_generation
                or (
                    int(marker["generation"]) == record.authoritative_generation
                    and str(marker["catalogHash"]) != record.authoritative_catalog_hash
                )
            ):
                raise AuthorityStateError("authenticated CoreFS authority lineage changed")
        else:
            record = _write_record(
                AuthorityRecord(
                    state=AuthorityState.AUTHORITATIVE,
                    prepared_generation=record.prepared_generation,
                    prepared_catalog_hash=record.prepared_catalog_hash,
                    authority_epoch=int(marker["cutoverEpoch"]),
                    authoritative_generation=int(marker["generation"]),
                    authoritative_catalog_hash=str(marker["catalogHash"]),
                ),
                expected=record,
            )
        return {
            "version": 1,
            "state": "authoritative",
            "authorityImmutable": True,
            "authorityEpoch": record.authority_epoch,
            "generation": int(marker["generation"]),
            "catalogHash": str(marker["catalogHash"]),
            "families": list(CONTENT_AUTHORITY_FAMILIES),
        }


def _write_record(record: AuthorityRecord, *, expected: AuthorityRecord) -> AuthorityRecord:
    def update(manifest: dict[str, object]) -> None:
        if manifest.get(_RELEASE_FIELD) != PORTABLE_CORE_RELEASE:
            raise AuthorityStateError("ANIMA CORE release identity changed concurrently")
        observed = _record_from_manifest(manifest)
        if observed != expected:
            raise AuthorityStateError("CoreFS authority manifest changed concurrently")
        manifest[_AUTHORITY_FIELD] = record.to_manifest()

    update_core_manifest(update)
    # Latch at the single authority-write choke point: a Core first activated
    # during this process must not depend on a later read to record its
    # irreversible state (PR #148 review, P1).
    mark_manifest_observed(
        get_manifest_path(),
        authoritative=record.state is AuthorityState.AUTHORITATIVE,
    )
    return record


def _record_from_manifest(manifest: dict[str, object]) -> AuthorityRecord:
    raw = manifest.get(_AUTHORITY_FIELD)
    if raw is None:
        return AuthorityRecord(AuthorityState.PENDING_ACTIVATION)
    if not isinstance(raw, dict) or raw.get("version") != _AUTHORITY_VERSION:
        raise AuthorityStateError("CoreFS authority manifest record is invalid")
    try:
        state = AuthorityState(raw["state"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthorityStateError("CoreFS authority manifest state is invalid") from exc
    base = {
        "version",
        "state",
        "preparedGeneration",
        "preparedCatalogHash",
        "authorityEpoch",
    }
    expected = (
        base
        if state is AuthorityState.PENDING_ACTIVATION
        else base | {"authoritativeGeneration", "authoritativeCatalogHash"}
    )
    if set(raw) != expected:
        raise AuthorityStateError("CoreFS authority manifest record has an invalid shape")
    prepared_generation = _positive_int(raw, "preparedGeneration")
    prepared_hash = _hash(raw, "preparedCatalogHash")
    epoch = _positive_int(raw, "authorityEpoch")
    authoritative_generation = (
        _positive_int(raw, "authoritativeGeneration")
        if state is AuthorityState.AUTHORITATIVE
        else None
    )
    authoritative_hash = (
        _hash(raw, "authoritativeCatalogHash") if state is AuthorityState.AUTHORITATIVE else None
    )
    return AuthorityRecord(
        state=state,
        prepared_generation=prepared_generation,
        prepared_catalog_hash=prepared_hash,
        authority_epoch=epoch,
        authoritative_generation=authoritative_generation,
        authoritative_catalog_hash=authoritative_hash,
    )


def _read_native_marker(
    *,
    corefs_session: Any,
    keys: object,
    required: bool,
) -> dict[str, object] | None:
    reader = getattr(corefs_session, "authoritative_cutover_v1", None)
    if not callable(reader):
        if required:
            raise AuthorityStateError("native CoreFS authority authentication is unavailable")
        return None
    raw = reader(keys)
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != {
        "version",
        "legacyRollbackDisabled",
        "cutoverEpoch",
        "generation",
        "catalogHash",
    }:
        raise AuthorityStateError("native CoreFS authority marker has an invalid shape")
    if raw.get("version") != 1 or raw.get("legacyRollbackDisabled") is not True:
        raise AuthorityStateError("native CoreFS authority marker is invalid")
    _positive_int(raw, "cutoverEpoch")
    _positive_int(raw, "generation")
    _hash(raw, "catalogHash")
    return dict(raw)


def _validate_snapshot(generation: int, catalog_hash: str) -> None:
    if (
        isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation <= 0
        or not isinstance(catalog_hash, str)
        or _SHA256_HEX.fullmatch(catalog_hash) is None
    ):
        raise AuthorityStateError("CoreFS prepared snapshot identity is invalid")


def _positive_int(raw: dict[str, object], key: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise AuthorityStateError(f"CoreFS authority {key} is invalid")
    return value


def _hash(raw: dict[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or _SHA256_HEX.fullmatch(value) is None:
        raise AuthorityStateError(f"CoreFS authority {key} is invalid")
    return value
