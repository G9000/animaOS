"""Tests for security-hardening changes.

Covers:
- ``is_sqlite_mode()`` helper behaviour
- Per-user DB routing fails-closed for non-SQLite
- ``/api/db/*`` is blocked in shared-database mode
- ``/api/vault/import`` is blocked in shared-database mode
- ``PUT /api/config/{user_id}`` is blocked in shared-database mode
- Sidecar nonce middleware enforcement
- Health endpoint does not expose nonce
"""

from __future__ import annotations

import shutil
from unittest.mock import patch

import pytest
from anima_server.config import settings
from anima_server.db.session import is_sqlite_mode
from conftest import create_managed_temp_dir, managed_test_client
from fastapi.testclient import TestClient

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _register_user(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/auth/register",
        json={"username": "sectest", "password": "pw123456", "name": "Sec Test"},
    )
    assert response.status_code == 201
    return response.json()


def _create_test_app() -> tuple[object, object, object]:
    from anima_server.main import create_app

    temp_root = create_managed_temp_dir("anima-sec-app-")
    original_data_dir = settings.data_dir
    settings.data_dir = temp_root / "anima-data"
    try:
        return create_app(), original_data_dir, temp_root
    except Exception:
        settings.data_dir = original_data_dir
        shutil.rmtree(temp_root, ignore_errors=True)
        raise


def _cors_allow_origins(app: object) -> list[str]:
    for middleware in app.user_middleware:  # type: ignore[attr-defined]
        if middleware.cls.__name__ == "CORSMiddleware":
            return list(middleware.kwargs.get("allow_origins", []))
    raise AssertionError("CORSMiddleware not found")


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
# require_sqlite_mode dependency
# --------------------------------------------------------------------------- #


def test_require_sqlite_mode_raises_in_shared_mode() -> None:
    """The dependency must raise HTTPException(403) when not in SQLite mode."""
    from anima_server.api.deps.db_mode import require_sqlite_mode
    from fastapi import HTTPException

    with patch("anima_server.api.deps.db_mode.is_sqlite_mode", return_value=False):
        try:
            require_sqlite_mode()
            raise AssertionError("Expected HTTPException")
        except HTTPException as exc:
            assert exc.status_code == 403
            assert "shared-database" in str(exc.detail).lower()


def test_require_sqlite_mode_passes_in_sqlite_mode() -> None:
    """The dependency must not raise when in SQLite mode."""
    from anima_server.api.deps.db_mode import require_sqlite_mode

    with patch("anima_server.api.deps.db_mode.is_sqlite_mode", return_value=True):
        require_sqlite_mode()


# --------------------------------------------------------------------------- #
# get_user_database_url - fail-closed for non-SQLite
# --------------------------------------------------------------------------- #


def test_get_user_database_url_raises_for_postgres() -> None:
    """Per-user routing must raise HTTPException instead of silently falling back."""
    from anima_server.db.session import get_user_database_url
    from fastapi import HTTPException

    with patch.object(settings, "database_url", "postgresql://localhost/anima"):
        try:
            get_user_database_url(1)
            raise AssertionError("Expected HTTPException")
        except HTTPException as exc:
            assert exc.status_code == 403
            assert "tenant isolation" in str(exc.detail).lower()


# --------------------------------------------------------------------------- #
# /api/db/* blocked in shared-DB mode
# --------------------------------------------------------------------------- #


def test_db_tables_blocked_in_shared_mode() -> None:
    """DB viewer endpoints must return 403 when not in SQLite mode."""
    with managed_test_client("anima-sec-test-") as client:
        reg = _register_user(client)
        headers = {"x-anima-unlock": reg["unlockToken"]}

        with patch("anima_server.api.deps.db_mode.is_sqlite_mode", return_value=False):
            resp = client.get("/api/db/tables", headers=headers)
            assert resp.status_code == 403
            assert "shared-database" in resp.json()["error"].lower()


def test_db_query_blocked_in_shared_mode() -> None:
    with managed_test_client("anima-sec-test-") as client:
        reg = _register_user(client)
        headers = {"x-anima-unlock": reg["unlockToken"]}

        with patch("anima_server.api.deps.db_mode.is_sqlite_mode", return_value=False):
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

        with patch("anima_server.api.deps.db_mode.is_sqlite_mode", return_value=False):
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

        with patch("anima_server.api.deps.db_mode.is_sqlite_mode", return_value=False):
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

        with patch("anima_server.api.deps.db_mode.is_sqlite_mode", return_value=False):
            resp = client.post(
                "/api/vault/import",
                headers=headers,
                json={"passphrase": "testpassphrase", "vault": '{"version":2}'},
            )
            assert resp.status_code == 403
            assert "shared-database" in resp.json()["error"].lower()


# --------------------------------------------------------------------------- #
# PUT /api/config/{user_id} blocked in shared-DB mode
# --------------------------------------------------------------------------- #


def test_config_update_blocked_in_shared_mode() -> None:
    with managed_test_client("anima-sec-test-") as client:
        reg = _register_user(client)
        user_id = int(reg["id"])
        headers = {"x-anima-unlock": reg["unlockToken"]}

        with patch("anima_server.api.deps.db_mode.is_sqlite_mode", return_value=False):
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
# Sidecar nonce middleware enforcement
# --------------------------------------------------------------------------- #


def test_nonce_middleware_rejects_missing_header() -> None:
    original_nonce = settings.sidecar_nonce
    original_data_dir = settings.data_dir
    temp_root = None
    try:
        settings.sidecar_nonce = "test-nonce-enforce"
        app, original_data_dir, temp_root = _create_test_app()
        with TestClient(app) as client:
            resp = client.get("/api/config/providers")
            assert resp.status_code == 403
            assert "nonce" in resp.json()["error"].lower()
    finally:
        settings.sidecar_nonce = original_nonce
        settings.data_dir = original_data_dir
        if temp_root is not None:
            shutil.rmtree(temp_root, ignore_errors=True)


def test_nonce_middleware_accepts_correct_header() -> None:
    original_nonce = settings.sidecar_nonce
    original_data_dir = settings.data_dir
    temp_root = None
    try:
        settings.sidecar_nonce = "test-nonce-enforce"
        app, original_data_dir, temp_root = _create_test_app()
        with TestClient(app) as client:
            resp = client.get(
                "/api/config/providers",
                headers={"x-anima-nonce": "test-nonce-enforce"},
            )
            assert resp.status_code == 200
    finally:
        settings.sidecar_nonce = original_nonce
        settings.data_dir = original_data_dir
        if temp_root is not None:
            shutil.rmtree(temp_root, ignore_errors=True)


def test_nonce_middleware_rejects_wrong_header() -> None:
    original_nonce = settings.sidecar_nonce
    original_data_dir = settings.data_dir
    temp_root = None
    try:
        settings.sidecar_nonce = "test-nonce-enforce"
        app, original_data_dir, temp_root = _create_test_app()
        with TestClient(app) as client:
            resp = client.get(
                "/api/config/providers",
                headers={"x-anima-nonce": "wrong-nonce"},
            )
            assert resp.status_code == 403
    finally:
        settings.sidecar_nonce = original_nonce
        settings.data_dir = original_data_dir
        if temp_root is not None:
            shutil.rmtree(temp_root, ignore_errors=True)


def test_nonce_middleware_uses_compare_digest() -> None:
    original_nonce = settings.sidecar_nonce
    original_data_dir = settings.data_dir
    temp_root = None
    try:
        settings.sidecar_nonce = "test-nonce-enforce"
        from anima_server import main as main_module

        app, original_data_dir, temp_root = _create_test_app()
        with (
            patch.object(main_module.hmac, "compare_digest", return_value=True) as compare_digest,
            TestClient(app) as client,
        ):
            resp = client.get(
                "/api/config/providers",
                headers={"x-anima-nonce": " test-nonce-enforce "},
            )
        assert resp.status_code == 200
        compare_digest.assert_called_once_with("test-nonce-enforce", "test-nonce-enforce")
    finally:
        settings.sidecar_nonce = original_nonce
        settings.data_dir = original_data_dir
        if temp_root is not None:
            shutil.rmtree(temp_root, ignore_errors=True)


def test_health_exempt_from_nonce() -> None:
    original_nonce = settings.sidecar_nonce
    original_data_dir = settings.data_dir
    temp_root = None
    try:
        settings.sidecar_nonce = "test-nonce-enforce"
        app, original_data_dir, temp_root = _create_test_app()
        with TestClient(app) as client:
            resp = client.get("/health")
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"
    finally:
        settings.sidecar_nonce = original_nonce
        settings.data_dir = original_data_dir
        if temp_root is not None:
            shutil.rmtree(temp_root, ignore_errors=True)


def test_health_endpoint_does_not_expose_nonce() -> None:
    original_nonce = settings.sidecar_nonce
    original_data_dir = settings.data_dir
    temp_root = None
    try:
        settings.sidecar_nonce = "test-nonce-abc123"
        app, original_data_dir, temp_root = _create_test_app()
        with TestClient(app) as client:
            resp = client.get("/health")
            assert resp.status_code == 200
            data = resp.json()
            assert "nonce" not in data
    finally:
        settings.sidecar_nonce = original_nonce
        settings.data_dir = original_data_dir
        if temp_root is not None:
            shutil.rmtree(temp_root, ignore_errors=True)


def test_health_endpoint_omits_nonce_when_not_configured() -> None:
    with managed_test_client("anima-sec-test-") as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "nonce" not in data


def test_create_app_warns_when_nonce_missing_outside_development() -> None:
    original_nonce = settings.sidecar_nonce
    original_env = settings.app_env
    original_require_encryption = settings.core_require_encryption
    original_data_dir = settings.data_dir
    temp_root = None
    try:
        settings.sidecar_nonce = ""
        settings.app_env = "production"
        settings.core_require_encryption = False

        from anima_server import main as main_module

        with patch.object(main_module.logger, "warning") as warning:
            _, original_data_dir, temp_root = _create_test_app()

        warning.assert_called_once_with(
            "Sidecar nonce is not configured in non-development environment"
        )
    finally:
        settings.sidecar_nonce = original_nonce
        settings.app_env = original_env
        settings.core_require_encryption = original_require_encryption
        settings.data_dir = original_data_dir
        if temp_root is not None:
            shutil.rmtree(temp_root, ignore_errors=True)


def test_create_app_refuses_to_start_when_encryption_required_without_nonce() -> None:
    original_nonce = settings.sidecar_nonce
    original_env = settings.app_env
    original_require_encryption = settings.core_require_encryption
    try:
        settings.sidecar_nonce = ""
        settings.app_env = "production"
        settings.core_require_encryption = True

        with pytest.raises(RuntimeError):
            _create_test_app()
    finally:
        settings.sidecar_nonce = original_nonce
        settings.app_env = original_env
        settings.core_require_encryption = original_require_encryption


def test_create_app_limits_localhost_cors_origins_outside_development() -> None:
    original_env = settings.app_env
    original_data_dir = settings.data_dir
    temp_root = None
    try:
        settings.app_env = "production"
        app, original_data_dir, temp_root = _create_test_app()
        origins = _cors_allow_origins(app)
        assert "http://localhost:1420" not in origins
        assert "http://localhost:5173" not in origins
        assert "tauri://localhost" in origins
        assert "https://tauri.localhost" in origins
    finally:
        settings.app_env = original_env
        settings.data_dir = original_data_dir
        if temp_root is not None:
            shutil.rmtree(temp_root, ignore_errors=True)


def test_create_app_allows_localhost_cors_origins_in_development() -> None:
    original_env = settings.app_env
    original_data_dir = settings.data_dir
    temp_root = None
    try:
        settings.app_env = "development"
        app, original_data_dir, temp_root = _create_test_app()
        origins = _cors_allow_origins(app)
        assert "http://localhost:1420" in origins
        assert "http://localhost:5173" in origins
        assert "http://tauri.localhost" in origins
        assert "tauri://localhost" in origins
        assert "https://tauri.localhost" in origins
    finally:
        settings.app_env = original_env
        settings.data_dir = original_data_dir
        if temp_root is not None:
            shutil.rmtree(temp_root, ignore_errors=True)
