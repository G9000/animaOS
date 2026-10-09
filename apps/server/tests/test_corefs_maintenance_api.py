from types import SimpleNamespace

from anima_server.api.routes import corefs_maintenance
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_client_principal_cannot_access_user_maintenance(monkeypatch):
    monkeypatch.setattr(
        corefs_maintenance, "require_unlocked_session", lambda request: SimpleNamespace(user_id=7)
    )
    monkeypatch.setattr(
        corefs_maintenance,
        "_resolve_principal",
        lambda request, session: SimpleNamespace(kind="client"),
    )
    monkeypatch.setattr(
        corefs_maintenance.core_maintenance,
        "inspect",
        lambda *args: (_ for _ in ()).throw(AssertionError("unauthorized native call")),
    )
    app = FastAPI()
    app.include_router(corefs_maintenance.router)
    response = TestClient(app).post(
        "/api/corefs/maintenance/inventory",
        json={"currentPassword": "secret", "recoveryPhrase": "phrase"},
    )
    assert response.status_code == 403


def test_agent_principal_cannot_purge_even_with_unlock(monkeypatch):
    monkeypatch.setattr(
        corefs_maintenance, "require_unlocked_session", lambda request: SimpleNamespace(user_id=7)
    )
    monkeypatch.setattr(
        corefs_maintenance,
        "_resolve_principal",
        lambda request, session: SimpleNamespace(kind="anima"),
    )
    app = FastAPI()
    app.include_router(corefs_maintenance.router)
    response = TestClient(app).post(
        "/api/corefs/maintenance/execute",
        json={
            "currentPassword": "secret",
            "recoveryPhrase": "phrase",
            "parameters": {
                "action": "purge",
                "targetStableId": "01J00000000000000000000001",
                "expectedRevision": 1,
                "waiveRestoreHistory": True,
                "acknowledgeBackupCopies": True,
            },
            "backupPath": "C:/backup.anima",
            "backupPassphrase": "backup",
            "confirmation": "a" * 43,
            "inventoryDigest": "b" * 64,
            "observedAtMs": 1000,
        },
    )
    assert response.status_code == 403


def test_purge_requires_history_and_backup_acknowledgments():
    app = FastAPI()
    app.include_router(corefs_maintenance.router)
    response = TestClient(app).post(
        "/api/corefs/maintenance/prepare",
        json={
            "currentPassword": "secret",
            "recoveryPhrase": "phrase",
            "parameters": {
                "action": "purge",
                "targetStableId": "01J00000000000000000000001",
                "expectedRevision": 1,
            },
            "backupPath": "C:/backup.anima",
            "backupPassphrase": "backup",
        },
    )
    assert response.status_code == 422
