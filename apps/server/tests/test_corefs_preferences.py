from __future__ import annotations

import pytest
from anima_server.services.corefs import logical
from anima_server.services.corefs.cutover import (
    approve_validation_cutover,
    begin_migration,
    publish_validation_readonly,
    reconcile_cutover_authority,
)
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
from anima_server.services.sessions import unlock_session_store
from conftest import managed_test_client


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
        assert host_media.status_code == 422

        unknown = client.patch(
            f"/api/preferences/{user_id}",
            headers=headers,
            json={"values": {"providerApiKey": "must-not-persist"}},
        )
        assert unknown.status_code == 422


def test_post_cutover_preference_patch_writes_only_corefs(
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

        session = unlock_session_store.resolve(token)
        assert session is not None
        selected = session.corefs_session.validation_snapshot(session.corefs_keys)
        begin_migration()
        publish_validation_readonly(
            generation=int(selected["generation"]),
            catalog_hash=str(selected["catalogHash"]),
        )
        approve_validation_cutover()
        logical.execute_mutation_v1(
            corefs_session=session.corefs_session,
            keys=session.corefs_keys,
            selected=logical.CoreFsValidationSnapshot(
                int(selected["generation"]), str(selected["catalogHash"])
            ),
            principal="user",
            mutation={"operation": "mkdir", "path": "Preference activation proof"},
        )
        marker = reconcile_cutover_authority(
            corefs_session=session.corefs_session,
            keys=session.corefs_keys,
        )
        assert marker is not None
        object.__setattr__(session, "content_authority", marker)

        def reject_legacy_preparation(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("post-cutover preferences touched the legacy preparation path")

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
