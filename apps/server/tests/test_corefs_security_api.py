from __future__ import annotations

from types import SimpleNamespace

from anima_server.api.routes import corefs_security
from anima_server.services.corefs.indexer import CoreFSProgressiveIndex
from anima_server.services.corefs.rotation import CoreFSRotationResult
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(corefs_security.router)
    return app


def test_corefs_security_status_requires_unlock() -> None:
    with TestClient(_app()) as client:
        response = client.get("/api/corefs/security/status")

    assert response.status_code == 401


def test_corefs_security_status_exposes_only_progress_and_rotation_metadata(
    monkeypatch,
) -> None:
    index = CoreFSProgressiveIndex("core-index")
    index.unlock(sqlcipher_key=b"s" * 32, local_instance_id="instance-a")
    index.begin_catalog()
    index.publish_catalog(
        catalog_generation=12,
        families={"notes": 2},
        degraded={"notes": ("opaque-object-id",)},
    )
    index.begin_text_indexing()
    index.index_text(
        family="notes",
        object_id="note-1",
        revision="rev-1",
        text="private marker must never cross the API",
    )
    monkeypatch.setattr(
        corefs_security,
        "require_unlocked_session",
        lambda _request: SimpleNamespace(runtime_index=index),
    )
    monkeypatch.setattr(
        corefs_security,
        "_rotation_manifest_state",
        lambda: {
            "active_version": 2,
            "pending_version": None,
            "decrypt_only_versions": [1],
            "phase": "idle",
        },
    )

    with TestClient(_app()) as client:
        response = client.get("/api/corefs/security/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["readiness"]["state"] == "text_indexing"
    assert payload["readiness"]["catalogGeneration"] == 12
    assert payload["readiness"]["processedObjects"] == 1
    assert payload["readiness"]["families"]["notes"] == {
        "total": 2,
        "processed": 1,
        "failed": 1,
        "degraded": True,
    }
    assert payload["rotation"] == {
        "activeFrkVersion": 2,
        "pendingFrkVersion": None,
        "decryptOnlyFrkVersions": [1],
        "phase": "idle",
        "blindIndexGeneration": None,
        "blindIndexPendingGeneration": None,
        "blindIndexProgress": 0,
    }
    assert "private marker" not in response.text


def test_corefs_rotation_replaces_unlock_session_and_returns_no_credentials(
    monkeypatch,
) -> None:
    session = SimpleNamespace(user_id=7, deks={"memories": b"m" * 32})
    replacement = SimpleNamespace(runtime_index=None)
    seen: dict[str, object] = {}

    class Store:
        def replace_user(self, user_id, deks, *, corefs_keys):
            seen["replacement"] = (user_id, deks, corefs_keys)
            return "replacement-token"

        def resolve(self, token):
            assert token == "replacement-token"
            return replacement

    def rotate(_session, *, current_password, recovery_phrase):
        seen["credentials"] = (current_password, recovery_phrase)
        return CoreFSRotationResult(
            active_subkeys=object(),
            active_version=3,
            committed_catalog_generation=14,
            resumed=True,
        )

    monkeypatch.setattr(
        corefs_security,
        "require_unlocked_session",
        lambda _request: session,
    )
    monkeypatch.setattr(corefs_security, "rotate_or_resume_frk", rotate)
    monkeypatch.setattr(corefs_security, "unlock_session_store", Store())

    with TestClient(_app()) as client:
        response = client.post(
            "/api/corefs/security/rotate",
            json={
                "currentPassword": "password",
                "recoveryPhrase": "recovery words",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "unlockToken": "replacement-token",
        "activeFrkVersion": 3,
        "committedCatalogGeneration": 14,
        "resumed": True,
    }
    assert seen["credentials"] == ("password", "recovery words")
    assert "password" not in response.text
    assert "recovery words" not in response.text


def test_corefs_rotation_rejects_wrong_recovery_without_replacing_session(
    monkeypatch,
) -> None:
    session = SimpleNamespace(user_id=7, deks={"memories": b"m" * 32})

    def reject(*_args, **_kwargs):
        raise ValueError("invalid recovery credential")

    class Store:
        def replace_user(self, *_args, **_kwargs):
            raise AssertionError("failed rotation must not replace the session")

    monkeypatch.setattr(
        corefs_security,
        "require_unlocked_session",
        lambda _request: session,
    )
    monkeypatch.setattr(corefs_security, "rotate_or_resume_frk", reject)
    monkeypatch.setattr(corefs_security, "unlock_session_store", Store())

    with TestClient(_app()) as client:
        response = client.post(
            "/api/corefs/security/rotate",
            json={
                "currentPassword": "password",
                "recoveryPhrase": "wrong words",
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "corefs_rotation_failed"
