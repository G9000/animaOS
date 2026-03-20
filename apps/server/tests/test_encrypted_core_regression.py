from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from anima_server.config import settings
from anima_server.db import dispose_cached_engines
from anima_server.db.user_store import _bootstrapped_roots
from anima_server.main import create_app
from anima_server.services.agent import invalidate_agent_runtime_cache
from anima_server.services.agent.vector_store import reset_vector_store
from anima_server.services.sessions import clear_sqlcipher_key, unlock_session_store


SENTINEL_ROUND_TRIP = "SENTINEL_ROUND_TRIP_ALPHA_20260320"
SENTINEL_WRONG_PASS = "SENTINEL_WRONG_PASS_BETA_20260320"
SENTINEL_PORTABILITY = "SENTINEL_PORTABILITY_GAMMA_20260320"
SENTINEL_SOUL_MIGRATION = "SENTINEL_SOUL_MIGRATION_DELTA_20260320"


@pytest.fixture()
def isolated_runtime_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ANIMA_DATA_DIR", str(runtime_root))

    original_data_dir = settings.data_dir
    original_database_url = settings.database_url
    original_passphrase = settings.core_passphrase
    original_require_encryption = settings.core_require_encryption

    settings.data_dir = runtime_root
    settings.database_url = f"sqlite:///{(runtime_root / 'anima.db').as_posix()}"
    settings.core_passphrase = ""
    settings.core_require_encryption = False

    _reset_fresh_process_state()
    try:
        yield runtime_root
    finally:
        _reset_fresh_process_state()
        settings.data_dir = original_data_dir
        settings.database_url = original_database_url
        settings.core_passphrase = original_passphrase
        settings.core_require_encryption = original_require_encryption


@pytest.fixture(scope="module")
def encrypted_core_supported() -> bool:
    import tempfile

    with tempfile.TemporaryDirectory(prefix="anima-sqlcipher-probe-") as tmp:
        runtime_root = Path(tmp) / "runtime"
        runtime_root.mkdir(parents=True, exist_ok=True)

        original_data_dir = settings.data_dir
        original_database_url = settings.database_url
        original_passphrase = settings.core_passphrase
        original_require_encryption = settings.core_require_encryption

        settings.data_dir = runtime_root
        settings.database_url = f"sqlite:///{(runtime_root / 'anima.db').as_posix()}"
        settings.core_passphrase = "probe-passphrase"
        settings.core_require_encryption = True

        _reset_fresh_process_state()
        try:
            app = create_app()
            with TestClient(app) as client:
                register = client.post(
                    "/api/auth/register",
                    json={
                        "username": "probe-user",
                        "password": "probe-password",
                        "name": "Probe",
                    },
                )
                if register.status_code != 201:
                    return False

                payload = register.json()
                user_id = int(payload["id"])
                headers = {"x-anima-unlock": str(payload["unlockToken"])}
                write = client.post(
                    f"/api/memory/{user_id}/items",
                    headers=headers,
                    json={"content": "probe", "category": "fact"},
                )
                if write.status_code != 201:
                    return False

            _reset_fresh_process_state()

            app = create_app()
            with TestClient(app) as client:
                login = client.post(
                    "/api/auth/login",
                    json={"username": "probe-user", "password": "probe-password"},
                )
                return login.status_code == 200
        except RuntimeError:
            return False
        finally:
            _reset_fresh_process_state()
            settings.data_dir = original_data_dir
            settings.database_url = original_database_url
            settings.core_passphrase = original_passphrase
            settings.core_require_encryption = original_require_encryption


def _reset_fresh_process_state() -> None:
    dispose_cached_engines()
    unlock_session_store.clear()
    clear_sqlcipher_key()
    reset_vector_store()
    invalidate_agent_runtime_cache()
    _bootstrapped_roots.clear()


def _register_user(client: TestClient, username: str, password: str, name: str) -> dict[str, object]:
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "name": name},
    )
    assert response.status_code == 201
    return response.json()


def _login_user(client: TestClient, username: str, password: str):
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return response


def test_encrypted_core_opens_with_correct_passphrase_and_round_trips_data(
    isolated_runtime_root: Path,
    encrypted_core_supported: bool,
) -> None:
    if not encrypted_core_supported:
        pytest.skip("SQLCipher encrypted open unsupported")

    settings.core_passphrase = "core-passphrase-correct"
    settings.core_require_encryption = True

    with TestClient(create_app()) as client:
        reg = _register_user(client, "enc-open", "password-1", "Enc Open")
        user_id = int(reg["id"])
        headers = {"x-anima-unlock": str(reg["unlockToken"])}

        create_item = client.post(
            f"/api/memory/{user_id}/items",
            headers=headers,
            json={"content": SENTINEL_ROUND_TRIP, "category": "fact"},
        )
        assert create_item.status_code == 201

    _reset_fresh_process_state()

    with TestClient(create_app()) as client:
        login = _login_user(client, "enc-open", "password-1")
        login_headers = {"x-anima-unlock": str(login.json()["unlockToken"])}
        items = client.get(f"/api/memory/{user_id}/items", headers=login_headers)
        assert items.status_code == 200
        assert any(item["content"] == SENTINEL_ROUND_TRIP for item in items.json())

    raw_db = (isolated_runtime_root / "users" / str(user_id) / "anima.db").read_bytes()
    assert SENTINEL_ROUND_TRIP.encode("utf-8") not in raw_db


def test_encrypted_core_rejects_wrong_passphrase_without_corrupting_data(
    isolated_runtime_root: Path,
    encrypted_core_supported: bool,
) -> None:
    if not encrypted_core_supported:
        pytest.skip("SQLCipher encrypted open unsupported")

    settings.core_passphrase = ""
    settings.core_require_encryption = True

    with TestClient(create_app()) as client:
        reg = _register_user(client, "enc-wrong-pass", "correct-password", "Wrong Pass")
        user_id = int(reg["id"])
        headers = {"x-anima-unlock": str(reg["unlockToken"])}
        create_item = client.post(
            f"/api/memory/{user_id}/items",
            headers=headers,
            json={"content": SENTINEL_WRONG_PASS, "category": "fact"},
        )
        assert create_item.status_code == 201

    _reset_fresh_process_state()

    with TestClient(create_app()) as client:
        wrong_login = client.post(
            "/api/auth/login",
            json={"username": "enc-wrong-pass", "password": "wrong-password"},
        )
        assert wrong_login.status_code == 401
        assert wrong_login.json() == {"error": "Invalid credentials"}

    _reset_fresh_process_state()

    with TestClient(create_app()) as client:
        login = _login_user(client, "enc-wrong-pass", "correct-password")
        login_headers = {"x-anima-unlock": str(login.json()["unlockToken"])}
        items = client.get(f"/api/memory/{user_id}/items", headers=login_headers)
        assert items.status_code == 200
        assert any(item["content"] == SENTINEL_WRONG_PASS for item in items.json())


def test_encrypted_core_is_portable_when_canonical_artifacts_are_copied(
    isolated_runtime_root: Path,
    tmp_path: Path,
    encrypted_core_supported: bool,
) -> None:
    if not encrypted_core_supported:
        pytest.skip("SQLCipher encrypted open unsupported")

    settings.core_passphrase = "portable-core-passphrase"
    settings.core_require_encryption = True

    source_root = isolated_runtime_root
    copied_root = tmp_path / "copied-runtime"

    with TestClient(create_app()) as client:
        reg = _register_user(client, "enc-port", "portable-password", "Portable")
        user_id = int(reg["id"])
        headers = {"x-anima-unlock": str(reg["unlockToken"])}
        create_item = client.post(
            f"/api/memory/{user_id}/items",
            headers=headers,
            json={"content": SENTINEL_PORTABILITY, "category": "fact"},
        )
        assert create_item.status_code == 201

    _reset_fresh_process_state()

    shutil.copytree(source_root, copied_root, dirs_exist_ok=True)
    tombstone_root = tmp_path / "original-gone"
    source_root.rename(tombstone_root)

    settings.data_dir = copied_root
    settings.database_url = f"sqlite:///{(copied_root / 'anima.db').as_posix()}"
    _reset_fresh_process_state()

    with TestClient(create_app()) as client:
        login = _login_user(client, "enc-port", "portable-password")
        login_headers = {"x-anima-unlock": str(login.json()["unlockToken"])}
        items = client.get(f"/api/memory/{user_id}/items", headers=login_headers)
        assert items.status_code == 200
        assert any(item["content"] == SENTINEL_PORTABILITY for item in items.json())


def test_legacy_plaintext_soul_is_rewritten_on_first_read_without_data_loss(
    isolated_runtime_root: Path,
) -> None:
    settings.core_passphrase = ""
    settings.core_require_encryption = False

    with TestClient(create_app()) as client:
        reg = _register_user(client, "legacy-soul", "legacy-password", "Legacy Soul")
        user_id = int(reg["id"])
        headers = {"x-anima-unlock": str(reg["unlockToken"])}

        legacy_path = isolated_runtime_root / "users" / str(user_id) / "soul.md"
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_path.write_text(SENTINEL_SOUL_MIGRATION, encoding="utf-8")

        from anima_server.db.session import get_user_session_factory
        from anima_server.models import SelfModelBlock
        from sqlalchemy import select

        with get_user_session_factory(user_id)() as db:
            existing = db.scalar(
                select(SelfModelBlock).where(
                    SelfModelBlock.user_id == user_id,
                    SelfModelBlock.section == "user_directive",
                )
            )
            assert existing is None

        first_read = client.get(f"/api/soul/{user_id}", headers=headers)
        assert first_read.status_code == 200
        assert first_read.json()["content"] == SENTINEL_SOUL_MIGRATION

        assert not legacy_path.exists()

        second_read = client.get(f"/api/soul/{user_id}", headers=headers)
        assert second_read.status_code == 200
        assert second_read.json()["content"] == SENTINEL_SOUL_MIGRATION

    raw_db = (isolated_runtime_root / "users" / str(user_id) / "anima.db").read_bytes()
    assert SENTINEL_SOUL_MIGRATION.encode("utf-8") not in raw_db
