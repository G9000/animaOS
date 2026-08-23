from __future__ import annotations

import pytest
from anima_server.db.session import get_user_session_factory
from anima_server.models import PresenceConfig
from anima_server.services.corefs.diary_migration import (
    migration_opaque_id,
    read_prepared_writing_body,
    read_prepared_writing_snapshot,
)
from anima_server.services.corefs.formats import (
    CoreFormatError,
    decode_preferences_document,
    encode_preferences_document,
)
from anima_server.services.presence_config import get_presence_config_values
from anima_server.services.sessions import unlock_session_store
from conftest import managed_test_client
from sqlalchemy import select


def test_preferences_are_canonical_json_and_round_trip_portable_values() -> None:
    stable_id = migration_opaque_id("preferences", "owner-1")
    first = encode_preferences_document(
        stable_id=stable_id,
        owner_id="owner-1",
        values={
            "theme": "dark",
            "translateLanguage": "ms",
            "ascii": {"enabled": True, "density": 0.5},
            "presence": {"enabled": True},
        },
        updated_at="2026-08-13T08:00:00+00:00",
    )
    second = encode_preferences_document(
        stable_id=stable_id,
        owner_id="owner-1",
        values={
            "presence": {"enabled": True},
            "ascii": {"density": 0.5, "enabled": True},
            "translateLanguage": "ms",
            "theme": "dark",
        },
        updated_at="2026-08-13T08:00:00+00:00",
    )

    assert first == second
    decoded = decode_preferences_document(first)
    assert decoded.owner_id == "owner-1"
    assert decoded.values["theme"] == "dark"
    assert decoded.values["ascii"] == {"density": 0.5, "enabled": True}


def test_preferences_reject_non_json_or_non_finite_values() -> None:
    stable_id = migration_opaque_id("preferences", "owner-1")
    with pytest.raises(CoreFormatError, match="canonical JSON"):
        encode_preferences_document(
            stable_id=stable_id,
            owner_id="owner-1",
            values={"bad": object()},
            updated_at=None,
        )
    with pytest.raises(CoreFormatError, match="canonical JSON"):
        encode_preferences_document(
            stable_id=stable_id,
            owner_id="owner-1",
            values={"bad": float("nan")},
            updated_at=None,
        )


def test_portable_preference_api_persists_and_verifies_encrypted_values() -> None:
    with managed_test_client("anima-preferences-test-") as client:
        registered = client.post(
            "/api/auth/register",
            json={
                "username": "preference-test",
                "password": "pw123456",
                "name": "Preference Test",
            },
        )
        assert registered.status_code == 201, registered.text
        payload = registered.json()
        user_id = int(payload["id"])
        token = str(payload["unlockToken"])
        headers = {"x-anima-unlock": token}

        updated = client.patch(
            f"/api/preferences/{user_id}",
            headers=headers,
            json={
                "values": {
                    "theme": "system",
                    "translateLanguage": "ms",
                    "clockFormat": "12h",
                    "bgm": {"currentId": "builtin-v", "muted": True},
                }
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["values"]["theme"] == "system"
        assert updated.json()["values"]["translateLanguage"] == "ms"

        fetched = client.get(f"/api/preferences/{user_id}", headers=headers)
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["values"]["clockFormat"] == "12h"

        session = unlock_session_store.resolve(token)
        assert session is not None
        item = next(
            candidate
            for candidate in read_prepared_writing_snapshot(session=session).objects
            if candidate.kind == "preferences"
        )
        decoded = decode_preferences_document(
            read_prepared_writing_body(session=session, item=item)
        )
        assert decoded.values["bgm"] == {"currentId": "builtin-v", "muted": True}


def test_portable_preference_api_rejects_host_media_and_unknown_keys() -> None:
    with managed_test_client("anima-preferences-invalid-") as client:
        registered = client.post(
            "/api/auth/register",
            json={
                "username": "preference-invalid",
                "password": "pw123456",
                "name": "Preference Invalid",
            },
        )
        payload = registered.json()
        user_id = int(payload["id"])
        headers = {"x-anima-unlock": str(payload["unlockToken"])}

        host_media = client.patch(
            f"/api/preferences/{user_id}",
            headers=headers,
            json={"values": {"background": {"type": "image", "value": "private.png"}}},
        )
        assert host_media.status_code == 409

        unknown = client.patch(
            f"/api/preferences/{user_id}",
            headers=headers,
            json={"values": {"providerApiKey": "must-not-persist"}},
        )
        assert unknown.status_code == 409


def test_authoritative_preference_patch_writes_only_corefs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with managed_test_client("anima-preferences-corefs-") as client:
        registered = client.post(
            "/api/auth/register",
            json={
                "username": "preference-corefs",
                "password": "pw123456",
                "name": "Preference CoreFS",
            },
        )
        payload = registered.json()
        user_id = int(payload["id"])
        token = str(payload["unlockToken"])
        headers = {"x-anima-unlock": token}
        initial = client.patch(
            f"/api/preferences/{user_id}",
            headers=headers,
            json={"values": {"theme": "dark"}},
        )
        assert initial.status_code == 200, initial.text

        def reject_legacy_preparation(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("authoritative preferences touched the preparation path")

        monkeypatch.setattr(
            "anima_server.services.corefs.preferences.prepare_writing_source_catalog",
            reject_legacy_preparation,
        )
        updated = client.patch(
            f"/api/preferences/{user_id}",
            headers=headers,
            json={"values": {"theme": "light", "clockFormat": "24h"}},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["values"]["theme"] == "light"
        assert updated.json()["values"]["clockFormat"] == "24h"


def test_presence_update_refreshes_encrypted_preference_shadow() -> None:
    with managed_test_client("anima-presence-preference-") as client:
        registered = client.post(
            "/api/auth/register",
            json={
                "username": "presence-preference",
                "password": "pw123456",
                "name": "Presence Preference",
            },
        )
        payload = registered.json()
        user_id = int(payload["id"])
        token = str(payload["unlockToken"])
        headers = {"x-anima-unlock": token}

        response = client.put(
            f"/api/presence/{user_id}",
            headers=headers,
            json={"taskNudgesEnabled": False, "customInstruction": "Be concise"},
        )
        assert response.status_code == 200, response.text

        session = unlock_session_store.resolve(token)
        assert session is not None
        item = next(
            candidate
            for candidate in read_prepared_writing_snapshot(session=session).objects
            if candidate.kind == "preferences"
        )
        decoded = decode_preferences_document(
            read_prepared_writing_body(session=session, item=item)
        )
        assert decoded.values["presence"]["taskNudgesEnabled"] is False
        assert decoded.values["presence"]["customInstruction"] == "Be concise"


def test_authoritative_presence_uses_only_canonical_preferences(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with managed_test_client("anima-presence-corefs-") as client:
        registered = client.post(
            "/api/auth/register",
            json={
                "username": "presence-corefs",
                "password": "pw123456",
                "name": "Presence CoreFS",
            },
        )
        payload = registered.json()
        user_id = int(payload["id"])
        token = str(payload["unlockToken"])
        headers = {"x-anima-unlock": token}
        initial = client.put(
            f"/api/presence/{user_id}",
            headers=headers,
            json={"enabled": False, "customInstruction": "Initial value"},
        )
        assert initial.status_code == 200, initial.text

        session = unlock_session_store.resolve(token)
        assert session is not None

        def reject_legacy_write(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("authoritative presence touched fallback persistence")

        monkeypatch.setattr(
            "anima_server.api.routes.presence.update_presence_config",
            reject_legacy_write,
        )
        monkeypatch.setattr(
            "anima_server.api.routes.presence.prepare_writing_source_catalog",
            reject_legacy_write,
        )
        updated = client.put(
            f"/api/presence/{user_id}",
            headers=headers,
            json={"enabled": True, "customInstruction": "Canonical value"},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["enabled"] is True
        assert updated.json()["customInstruction"] == "Canonical value"

        fetched = client.get(f"/api/presence/{user_id}", headers=headers)
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["enabled"] is True
        assert fetched.json()["customInstruction"] == "Canonical value"

        with get_user_session_factory(user_id)() as db:
            legacy = db.scalar(select(PresenceConfig).where(PresenceConfig.user_id == user_id))
            assert legacy is None
            background_values = get_presence_config_values(db, user_id)
        assert background_values.enabled is True
        assert background_values.custom_instruction == "Canonical value"

        item = next(
            candidate
            for candidate in read_prepared_writing_snapshot(session=session).objects
            if candidate.kind == "preferences"
        )
        decoded = decode_preferences_document(
            read_prepared_writing_body(session=session, item=item)
        )
        assert decoded.values["presence"]["enabled"] is True
        assert decoded.values["presence"]["customInstruction"] == "Canonical value"


def test_presence_read_fails_closed_on_unparseable_manifest(
    monkeypatch: pytest.MonkeyPatch,
    managed_tmp_path,
) -> None:
    """A damaged manifest must not let consent reads fall back to legacy
    defaults (PR #148 review, P1); only a genuinely never-activated
    environment may use the legacy row fallback."""
    from unittest.mock import MagicMock

    from anima_server.config import settings
    from anima_server.services.corefs.authority import AuthorityStateError

    core = managed_tmp_path / "core"
    core.mkdir()
    monkeypatch.setattr(settings, "data_dir", core)

    # Never-activated environment: no manifest file -> legacy defaults with
    # every initiative gate off.
    db = MagicMock()
    db.scalar.return_value = None
    values = get_presence_config_values(db, 7)
    assert values.initiative_enabled is False

    # Damaged manifest: fail closed instead of reverting consent to defaults.
    (core / "manifest.json").write_text("{ this is not json", encoding="utf-8")
    with pytest.raises(AuthorityStateError):
        get_presence_config_values(db, 7)

    # A manifest this process has observed must also fail closed if it later
    # disappears; absence only means never-activated before first observation.
    (core / "manifest.json").write_text("{}", encoding="utf-8")
    assert get_presence_config_values(db, 7).initiative_enabled is False
    (core / "manifest.json").unlink()
    with pytest.raises(AuthorityStateError):
        get_presence_config_values(db, 7)


def test_damaged_manifest_returns_conflict_not_server_error() -> None:
    """A damaged manifest must fail closed as a stable 409, not an unhandled 500.

    Post-merge review: `AuthorityStateError` was handled nowhere outside its
    module, so `GET /api/presence/{id}` escaped as a server error while diary
    and tasks already returned 409. It now carries the shared
    `CoreFsAuthorityUnavailable` marker and lands on the same handler.
    """
    from anima_server.services.core import get_manifest_path

    with managed_test_client("anima-damaged-manifest-") as client:
        registered = client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "pw123456", "name": "Alice"},
        )
        assert registered.status_code == 201, registered.text
        payload = registered.json()
        user_id = int(payload["id"])
        headers = {"x-anima-unlock": str(payload["unlockToken"])}

        get_manifest_path().write_text("{ not valid json", encoding="utf-8")

        response = client.get(f"/api/presence/{user_id}", headers=headers)
        assert response.status_code == 409, response.text
        assert (
            response.json()["details"]["code"] == "corefs_content_authority_unavailable"
        )
