"""Tests for security-hardening changes.

Covers:
- ``is_sqlite_mode()`` helper behaviour
- Per-user DB routing fails-closed for non-SQLite
- ``/api/db/*`` is blocked in shared-database mode
- ``/api/vault/import`` is blocked in shared-database mode
- ``PUT /api/config/{user_id}`` is blocked in shared-database mode
- Sidecar nonce middleware enforcement
- Health endpoint exposes nonce when configured
"""
from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from anima_server.config import settings
from anima_server.db.session import is_sqlite_mode

from conftest import managed_test_client


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _register_user(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/auth/register",
        json={"username": "sectest", "password": "pw1234", "name": "Sec Test"},
    )
    assert response.status_code == 201
    return response.json()


# --------------------------------------------------------------------------- #
# is_sqlite_mode()
# --------------------------------------------------------------------------- #


def test_is_sqlite_mode_true_for_sqlite_url() -> None:
    """When the database_url starts with 'sqlite', we're in per-user mode."""
    with patch.object(settings, "database_url", "sqlite:///tmp/test.db"):
        assert is_sqlite_mode() is True


def test_is_sqlite_mode_false_for_postgres_url() -> None:
    """When the database_url is PostgreSQL, per-user routing is unavailable."""
    with patch.object(settings, "database_url", "postgresql://localhost/anima"):
        assert is_sqlite_mode() is False


# --------------------------------------------------------------------------- #
# get_user_database_url – fail-closed for non-SQLite
# --------------------------------------------------------------------------- #


def test_get_user_database_url_raises_for_postgres() -> None:
    """Per-user routing must raise instead of silently falling back."""
    from anima_server.db.session import get_user_database_url

    with patch.object(settings, "database_url", "postgresql://localhost/anima"):
        try:
            get_user_database_url(1)
            raise AssertionError("Expected RuntimeError")  # noqa: TRY301
        except RuntimeError as exc:
            assert "tenant isolation" in str(exc).lower()


# --------------------------------------------------------------------------- #
# /api/db/* blocked in shared-DB mode
# --------------------------------------------------------------------------- #


def test_db_tables_blocked_in_shared_mode() -> None:
    """DB viewer endpoints must return 403 when not in SQLite mode."""
    with managed_test_client("anima-sec-test-") as client:
        reg = _register_user(client)
        headers = {"x-anima-unlock": reg["unlockToken"]}

        with patch("anima_server.api.routes.db.is_sqlite_mode", return_value=False):
            resp = client.get("/api/db/tables", headers=headers)
            assert resp.status_code == 403
            assert "shared-database" in resp.json()["error"].lower()


def test_db_query_blocked_in_shared_mode() -> None:
    with managed_test_client("anima-sec-test-") as client:
        reg = _register_user(client)
        headers = {"x-anima-unlock": reg["unlockToken"]}

        with patch("anima_server.api.routes.db.is_sqlite_mode", return_value=False):
            resp = client.post(
                "/api/db/query",
                headers=headers,
                json={"sql": "SELECT 1"},
            )
            assert resp.status_code == 403


def test_db_delete_blocked_in_shared_mode() -> None:
    with managed_test_client("anima-sec-test-") as client:
        reg = _register_user(client)
        headers = {"x-anima-unlock": reg["unlockToken"]}

        with patch("anima_server.api.routes.db.is_sqlite_mode", return_value=False):
            resp = client.request(
                "DELETE",
                "/api/db/tables/users/rows",
                headers=headers,
                json={"conditions": {"id": 1}},
            )
            assert resp.status_code == 403


def test_db_update_blocked_in_shared_mode() -> None:
    with managed_test_client("anima-sec-test-") as client:
        reg = _register_user(client)
        headers = {"x-anima-unlock": reg["unlockToken"]}

        with patch("anima_server.api.routes.db.is_sqlite_mode", return_value=False):
            resp = client.put(
                "/api/db/tables/users/rows",
                headers=headers,
                json={"conditions": {"id": 1}, "updates": {"display_name": "New"}},
            )
            assert resp.status_code == 403


# --------------------------------------------------------------------------- #
# DB viewer still works in SQLite mode (regression check)
# --------------------------------------------------------------------------- #


def test_db_tables_allowed_in_sqlite_mode() -> None:
    with managed_test_client("anima-sec-test-") as client:
        reg = _register_user(client)
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.get("/api/db/tables", headers=headers)
        assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# /api/vault/import blocked in shared-DB mode
# --------------------------------------------------------------------------- #


def test_vault_import_blocked_in_shared_mode() -> None:
    with managed_test_client("anima-sec-test-") as client:
        reg = _register_user(client)
        headers = {"x-anima-unlock": reg["unlockToken"]}

        with patch("anima_server.api.routes.vault.is_sqlite_mode", return_value=False):
            resp = client.post(
                "/api/vault/import",
                headers=headers,
                json={"passphrase": "testpassphrase", "vault": "{\"version\":2}"},
            )
            assert resp.status_code == 403
            assert "tenant isolation" in resp.json()["error"].lower()


# --------------------------------------------------------------------------- #
# PUT /api/config/{user_id} blocked in shared-DB mode
# --------------------------------------------------------------------------- #


def test_config_update_blocked_in_shared_mode() -> None:
    with managed_test_client("anima-sec-test-") as client:
        reg = _register_user(client)
        user_id = int(reg["id"])
        headers = {"x-anima-unlock": reg["unlockToken"]}

        with patch("anima_server.api.routes.config.is_sqlite_mode", return_value=False):
            resp = client.put(
                f"/api/config/{user_id}",
                headers=headers,
                json={"provider": "scaffold", "model": "scaffold"},
            )
            assert resp.status_code == 403
            assert "shared-database" in resp.json()["error"].lower()


def test_config_update_allowed_in_sqlite_mode() -> None:
    """Config mutation should still work in SQLite mode (single-user desktop)."""
    with managed_test_client("anima-sec-test-") as client:
        reg = _register_user(client)
        user_id = int(reg["id"])
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.put(
            f"/api/config/{user_id}",
            headers=headers,
            json={"provider": "scaffold", "model": "scaffold"},
        )
        assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# Sidecar nonce middleware
# --------------------------------------------------------------------------- #


def test_health_endpoint_returns_nonce_when_configured() -> None:
    """Health endpoint should include the nonce if ANIMA_SIDECAR_NONCE is set."""
    original = settings.sidecar_nonce
    try:
        settings.sidecar_nonce = "test-nonce-abc123"
        from anima_server.main import create_app

        app = create_app()
        with TestClient(app) as client:
            resp = client.get("/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["nonce"] == "test-nonce-abc123"
    finally:
        settings.sidecar_nonce = original


def test_health_endpoint_omits_nonce_when_not_configured() -> None:
    """Health endpoint should not include nonce if ANIMA_SIDECAR_NONCE is empty."""
    with managed_test_client("anima-sec-test-") as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "nonce" not in data
