from __future__ import annotations

from contextlib import suppress
from types import SimpleNamespace
from typing import Any

import anima_core

from anima_server.config import settings
from anima_server.services.core import get_core_id, get_owner_id
from anima_server.services.corefs.diary_migration import (
    DiaryMigrationError,
    migration_opaque_id,
    read_prepared_writing_body,
    read_prepared_writing_snapshot,
)
from anima_server.services.corefs.formats import (
    AccountProfileDocument,
    decode_account_profile_document,
)


def read_account_profile_for_session(session: Any) -> AccountProfileDocument | None:
    """Read the single authenticated account profile after CoreFS unlock.

    A missing validation head/profile is allowed only during the pre-PCF-008
    legacy-authoritative upgrade window. Any present conflicting profile fails
    closed instead of falling back to a plaintext locator.
    """
    if session.corefs_session is None or session.corefs_keys is None:
        return None
    if not (settings.data_dir / "fs" / "VALIDATION_HEAD").is_file():
        return None

    snapshot = read_prepared_writing_snapshot(session=session)
    profiles = [item for item in snapshot.objects if item.kind == "account-profile"]
    if not profiles:
        return None
    if len(profiles) != 1:
        raise DiaryMigrationError("Core contains multiple account-profile objects.")

    profile = decode_account_profile_document(
        read_prepared_writing_body(session=session, item=profiles[0])
    )
    owner_id = get_owner_id()
    if not owner_id:
        raise DiaryMigrationError("Core account profile has no opaque owner binding.")
    expected_id = migration_opaque_id("account-profile", owner_id)
    if profile.owner_id != owner_id or profile.stable_id != expected_id:
        raise DiaryMigrationError("Core account profile owner binding is invalid.")
    if profile.legacy_user_id != int(session.user_id):
        raise DiaryMigrationError("Core account profile local owner binding is invalid.")
    return profile


def read_unlocked_account_profile(
    *,
    user_id: int,
    corefs_keys: object,
) -> AccountProfileDocument | None:
    """Open a short-lived native session for login-time profile hydration."""
    native = anima_core.CorefsSession(str(settings.data_dir), get_core_id())
    session = SimpleNamespace(
        user_id=user_id,
        corefs_session=native,
        corefs_keys=corefs_keys,
    )
    try:
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
    }
    if profile.created_at is not None:
        payload["createdAt"] = profile.created_at
    if profile.updated_at is not None:
        payload["updatedAt"] = profile.updated_at
    return payload
