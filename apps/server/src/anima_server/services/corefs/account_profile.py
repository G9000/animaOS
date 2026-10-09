from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import RLock
from types import SimpleNamespace
from typing import Any

import anima_core

from anima_server.config import settings
from anima_server.services.core import get_core_id, get_owner_id
from anima_server.services.corefs import logical
from anima_server.services.corefs.catalog_reader import (
    CatalogReadError,
    read_prepared_writing_body,
    read_prepared_writing_snapshot,
    stable_opaque_id,
)
from anima_server.services.corefs.content_authority import (
    CoreFsAuthorityUnavailable,
    authenticated_content_authority,
    invalidate_active_catalog_indexes,
    publish_content_authority_after_mutation,
)
from anima_server.services.corefs.formats import (
    ACCOUNT_PROFILE_CONTENT_TYPE,
    AccountProfileDocument,
    decode_account_profile_document,
    encode_account_profile_document,
)

_account_locks_guard = RLock()
_account_locks: dict[int, RLock] = {}


class AccountProfileAuthorityError(RuntimeError, CoreFsAuthorityUnavailable):
    pass


@dataclass(frozen=True, slots=True)
class CanonicalAccountProfileRecord:
    document: AccountProfileDocument
    revision: int


def _account_marker_active(session: object) -> bool:
    marker = getattr(session, "content_authority", None)
    return (
        isinstance(marker, dict)
        and marker.get("version") == 1
        and marker.get("state") == "authoritative"
        and isinstance(marker.get("families"), list)
        and "account" in marker["families"]
    )


def account_profile_corefs_authority_active(session: object) -> bool:
    """True when canonical CoreFS is the required account authority.

    Covers both a canonical-capable session and an FS-locked session on an
    activated Core, which must fail closed instead of writing legacy rows.
    """
    from anima_server.services.corefs.content_authority import core_content_authority_active

    return _account_marker_active(session) or core_content_authority_active()


def authoritative_setup_complete(*, session: Any, soul_value: bool) -> bool:
    # Agent setup is retained cognition. A valid Soul-only unlock can report
    # this flag while private account profile fields remain unavailable.
    if session.corefs_keys is None:
        return soul_value
    profile = read_account_profile_for_session(session)
    if profile is None:
        raise AccountProfileAuthorityError("Canonical account profile is unavailable.")
    return profile.setup_complete


def read_account_profile_for_session(session: Any) -> AccountProfileDocument | None:
    """Read private account fields exclusively from authenticated native authority."""
    if session.corefs_session is None or session.corefs_keys is None:
        return None
    try:
        marker = authenticated_content_authority(session, family="account")
        if marker is None:
            raise AccountProfileAuthorityError("CoreFS account authority is not active.")
        record = _read_account_profile_record(session=session)
    except (CatalogReadError, ValueError, RuntimeError) as exc:
        raise AccountProfileAuthorityError(
            "Canonical account profile failed authentication."
        ) from exc
    if record is None:
        raise AccountProfileAuthorityError("Canonical account profile is unavailable.")
    return record.document


def _read_account_profile_record(*, session: Any) -> CanonicalAccountProfileRecord | None:
    snapshot = read_prepared_writing_snapshot(session=session)
    profiles = [item for item in snapshot.objects if item.kind == "account-profile"]
    if not profiles:
        return None
    if len(profiles) != 1:
        raise CatalogReadError("Core contains multiple account-profile objects.")

    item = profiles[0]
    profile = decode_account_profile_document(
        read_prepared_writing_body(session=session, item=item)
    )
    owner_id = get_owner_id()
    if not owner_id:
        raise CatalogReadError("Core account profile has no opaque owner binding.")
    expected_id = stable_opaque_id("account-profile", owner_id)
    if profile.owner_id != owner_id or profile.stable_id != expected_id:
        raise CatalogReadError("Core account profile owner binding is invalid.")
    if profile.legacy_user_id != int(session.user_id):
        raise CatalogReadError("Core account profile local owner binding is invalid.")
    return CanonicalAccountProfileRecord(document=profile, revision=item.revision)


def update_canonical_account_profile(
    *,
    session: Any,
    username: str | None = None,
    display_name: str | None = None,
    gender: str | None = None,
    gender_present: bool = False,
    age: int | None = None,
    age_present: bool = False,
    birthday: str | None = None,
    birthday_present: bool = False,
    setup_complete: bool | None = None,
) -> AccountProfileDocument:
    user_id = int(session.user_id)
    with _account_lock(user_id):
        marker = authenticated_content_authority(session, family="account")
        if marker is None:
            raise AccountProfileAuthorityError("CoreFS account authority is not active.")
        try:
            record = _read_account_profile_record(session=session)
        except (CatalogReadError, ValueError) as exc:
            raise AccountProfileAuthorityError(
                "Canonical account profile failed authentication."
            ) from exc
        if record is None:
            raise AccountProfileAuthorityError("Canonical account profile is unavailable.")
        current = record.document
        next_username = current.username if username is None else username
        next_display_name = current.display_name if display_name is None else display_name
        if not next_username or not next_display_name:
            raise AccountProfileAuthorityError("Canonical account identity cannot be blank.")
        updated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        body = encode_account_profile_document(
            stable_id=current.stable_id,
            owner_id=current.owner_id,
            legacy_user_id=current.legacy_user_id,
            username=next_username,
            display_name=next_display_name,
            gender=gender if gender_present else current.gender,
            age=age if age_present else current.age,
            birthday=birthday if birthday_present else current.birthday,
            setup_complete=(current.setup_complete if setup_complete is None else setup_complete),
            created_at=current.created_at,
            updated_at=updated_at,
        )
        result = logical.execute_mutation_v1(
            corefs_session=session.corefs_session,
            keys=session.corefs_keys,
            selected=logical.CoreFsValidationSnapshot(
                int(marker["generation"]),
                str(marker["catalogHash"]),
            ),
            principal="user",
            mutation={
                "operation": "write_file",
                "target": {"stableId": current.stable_id},
                "expectedRevision": record.revision,
                "contentType": ACCOUNT_PROFILE_CONTENT_TYPE,
                "bodyEncoding": "utf-8",
            },
            body=body,
            invalidate=lambda _generation, _catalog_hash: invalidate_active_catalog_indexes(
                user_id
            ),
        )
        changes = result.get("changes")
        if (
            not isinstance(changes, list)
            or len(changes) != 1
            or not isinstance(changes[0], dict)
            or changes[0].get("stableId") != current.stable_id
            or changes[0].get("revision") != record.revision + 1
        ):
            raise AccountProfileAuthorityError("Native CoreFS account mutation result is invalid.")
        publish_content_authority_after_mutation(
            session,
            generation=int(result["generation"]),
            catalog_hash=str(result["catalogHash"]),
        )
        verified = read_account_profile_for_session(session)
        expected = decode_account_profile_document(body)
        if verified is None or verified != expected:
            raise AccountProfileAuthorityError("Canonical account verification failed.")
        return verified


def _account_lock(user_id: int) -> RLock:
    with _account_locks_guard:
        return _account_locks.setdefault(user_id, RLock())


def read_unlocked_account_profile(
    *,
    user_id: int,
    corefs_keys: object,
) -> AccountProfileDocument | None:
    """Open a short-lived native session for login-time profile hydration."""
    native = anima_core.CorefsSession(str(settings.data_dir), get_core_id())
    try:
        from anima_server.services.corefs.authority import reconcile_content_authority

        authority = reconcile_content_authority(corefs_session=native, keys=corefs_keys)
        session = SimpleNamespace(
            user_id=user_id,
            corefs_session=native,
            corefs_keys=corefs_keys,
            content_authority=authority,
        )
        return read_account_profile_for_session(session)
    finally:
        with suppress(Exception):
            native.close()


def serialize_account_profile(profile: AccountProfileDocument) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": profile.legacy_user_id,
        "username": profile.username,
        "name": profile.display_name,
        "gender": profile.gender,
        "age": profile.age,
        "birthday": profile.birthday,
        "ownerId": profile.owner_id,
        "profileAvailable": True,
        "filesystemAvailable": True,
    }
    if profile.created_at is not None:
        payload["createdAt"] = profile.created_at
    if profile.updated_at is not None:
        payload["updatedAt"] = profile.updated_at
    return payload


def read_active_account_profile(user_id: int) -> AccountProfileDocument | None:
    """Hydrate private profile fields only from an authenticated active Core."""
    from anima_server.services.sessions import unlock_session_store

    for session in reversed(unlock_session_store.get_active_sessions(user_id)):
        if session.corefs_keys is not None:
            return read_account_profile_for_session(session)
    return None
