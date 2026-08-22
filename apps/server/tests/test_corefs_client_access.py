from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from anima_server.api.routes import corefs_access as access_routes
from anima_server.config import settings
from anima_server.services.corefs.client_access import (
    ClientAccessError,
    ClientGrantRequired,
    ClientReapprovalRequired,
    CoreFsFolderGrantTarget,
    VerifiedClientInstallation,
    approve_installation,
    authorize_client_path,
    client_capability_broker,
    public_registry,
    register_verified_installation,
    revoke_installation,
    set_folder_grant,
)
from conftest import managed_test_client


def _identity(
    *,
    client_id: str = "notes-extension",
    package_id: str = "com.example.notes",
    digest: str = "a",
    publisher: str | None = "ed25519:publisher-a",
) -> VerifiedClientInstallation:
    return VerifiedClientInstallation(
        client_id=client_id,
        package_id=package_id,
        display_name="Notes Extension",
        version="1.0.0",
        install_digest=f"sha256:{digest * 64}",
        publisher_identity=publisher,
        publisher_verified=publisher is not None,
        declared_roles=(f"client:{client_id}:notes",),
        declared_metadata_keys=(f"client:{client_id}:source",),
    )


@pytest.fixture(autouse=True)
def _clear_capabilities() -> None:
    client_capability_broker.revoke_all()
    yield
    client_capability_broker.revoke_all()


def test_grants_require_user_confirmation_and_follow_stable_folder_id(
    managed_tmp_path: Path,
) -> None:
    previous_data_dir = settings.data_dir
    previous_runtime_dir = settings.runtime_instance_data_dir
    core = managed_tmp_path / ".anima"
    core.mkdir()
    (core / "manifest.json").write_text(
        json.dumps({"core_id": "core-client-access"}), encoding="utf-8"
    )
    runtime = managed_tmp_path / "runtime" / "instances" / "instance-a"
    settings.data_dir = core
    settings.runtime_instance_data_dir = str(runtime)
    try:
        installation_id = register_verified_installation(_identity())
        with pytest.raises(ClientReapprovalRequired):
            set_folder_grant(
                installation_id,
                folder_stable_id="folder-notes",
                scope="read",
                confirmed=True,
            )
        with pytest.raises(ClientAccessError, match="explicit confirmation"):
            approve_installation(installation_id, confirmed=False)
        approve_installation(installation_id, confirmed=True)
        with pytest.raises(ClientAccessError, match="explicit confirmation"):
            set_folder_grant(
                installation_id,
                folder_stable_id="folder-notes",
                scope="write",
            )
        set_folder_grant(
            installation_id,
            folder_stable_id="folder-notes",
            scope="write",
            confirmed=True,
        )

        session = object()
        token, ttl = client_capability_broker.issue(
            audience="anima-mod:notes-extension",
            client_id="notes-extension",
            install_digest=f"sha256:{'a' * 64}",
            user_id=0,
            active_sessions=(session,),
        )
        assert ttl == 15
        identity = client_capability_broker.consume(
            token=token,
            user_id=0,
            session=session,
        )
        with pytest.raises(ClientAccessError, match="already used"):
            client_capability_broker.consume(token=token, user_id=0, session=session)

        assert (
            authorize_client_path(
                identity,
                folders=(CoreFsFolderGrantTarget("folder-notes", "Notes", "core.notes"),),
                logical_path="Notes/today.md",
                required_scope="write",
                record_use=True,
            )
            == "folder-notes"
        )
        # Rename/move changes only presentation path; the stable-ID grant remains valid.
        assert (
            authorize_client_path(
                identity,
                folders=(
                    CoreFsFolderGrantTarget("folder-notes", "Archive/Renamed Notes", "core.notes"),
                ),
                logical_path="Archive/Renamed Notes/today.md",
                required_scope="read",
            )
            == "folder-notes"
        )

        # A stable-ID grant on the authenticated root covers every descendant.
        assert (
            authorize_client_path(
                identity,
                folders=(CoreFsFolderGrantTarget("folder-notes", "", None),),
                logical_path="Archive/Renamed Notes/today.md",
                required_scope="read",
            )
            == "folder-notes"
        )

        record = public_registry()[0]
        assert record["lastUsedAt"] is not None
        assert record["grants"][0]["lastUsedAt"] == record["lastUsedAt"]

        set_folder_grant(
            installation_id,
            folder_stable_id="folder-notes",
            scope="none",
        )
        with pytest.raises(ClientGrantRequired):
            authorize_client_path(
                identity,
                folders=(CoreFsFolderGrantTarget("folder-notes", "Notes", None),),
                logical_path="Notes/today.md",
                required_scope="read",
            )
        if os.name != "nt":
            mode = (runtime / "config" / "corefs-client-access.json").stat().st_mode
            assert mode & 0o077 == 0
    finally:
        settings.data_dir = previous_data_dir
        settings.runtime_instance_data_dir = previous_runtime_dir


def test_digest_change_collision_revocation_and_transfer_fail_closed(
    managed_tmp_path: Path,
) -> None:
    previous_data_dir = settings.data_dir
    previous_runtime_dir = settings.runtime_instance_data_dir
    core = managed_tmp_path / ".anima"
    core.mkdir()
    (core / "manifest.json").write_text(
        json.dumps({"core_id": "core-client-transfer"}), encoding="utf-8"
    )
    runtime_a = managed_tmp_path / "runtime" / "instances" / "instance-a"
    settings.data_dir = core
    settings.runtime_instance_data_dir = str(runtime_a)
    try:
        with pytest.raises(ClientAccessError, match="namespace"):
            register_verified_installation(replace(_identity(), declared_roles=("core.journal",)))

        installation_id = register_verified_installation(_identity())
        approve_installation(installation_id, confirmed=True)
        set_folder_grant(
            installation_id,
            folder_stable_id="folder-notes",
            scope="read",
            confirmed=True,
        )
        session = object()
        token, _ = client_capability_broker.issue(
            audience="anima-mod:notes-extension",
            client_id="notes-extension",
            install_digest=f"sha256:{'a' * 64}",
            user_id=0,
            active_sessions=(session,),
        )

        assert register_verified_installation(_identity(digest="b")) == installation_id
        assert public_registry()[0]["status"] == "reapproval_required"
        with pytest.raises(ClientAccessError, match="approval or reapproval"):
            client_capability_broker.consume(token=token, user_id=0, session=session)

        collision_id = register_verified_installation(
            _identity(package_id="org.example.other", publisher="ed25519:publisher-b")
        )
        assert collision_id != installation_id
        assert {item["status"] for item in public_registry()} == {"collision"}

        revoke_installation(collision_id)
        assert (
            next(item for item in public_registry() if item["installationId"] == collision_id)[
                "status"
            ]
            == "revoked"
        )
        surviving = next(
            item for item in public_registry() if item["installationId"] == installation_id
        )
        assert surviving["status"] == "reapproval_required"
        approve_installation(installation_id, confirmed=True)

        settings.runtime_instance_data_dir = str(
            managed_tmp_path / "runtime" / "instances" / "instance-b"
        )
        assert public_registry() == []
        settings.runtime_instance_data_dir = str(runtime_a)
        assert len(public_registry()) == 2
    finally:
        settings.data_dir = previous_data_dir
        settings.runtime_instance_data_dir = previous_runtime_dir


def test_user_access_api_has_no_client_self_registration_or_unconfirmed_expansion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    folders = (CoreFsFolderGrantTarget("folder-notes", "Notes", "core.notes"),)
    monkeypatch.setattr(access_routes, "_folder_inventory", lambda _session: folders)
    with managed_test_client("pcf007-client-access-") as client:
        registered = client.post(
            "/api/auth/register",
            json={"username": "client-access", "password": "pw123456", "name": "Owner"},
        )
        assert registered.status_code == 201, registered.text
        headers = {"x-anima-unlock": registered.json()["unlockToken"]}
        installation_id = register_verified_installation(_identity())

        state = client.get("/api/corefs/access", headers=headers)
        assert state.status_code == 200, state.text
        assert state.json()["deviceLocal"] is True
        assert state.json()["installations"][0]["status"] == "pending"

        unconfirmed = client.post(
            f"/api/corefs/access/installations/{installation_id}/approve",
            headers=headers,
            json={"confirmed": False},
        )
        assert unconfirmed.status_code == 422
        approved = client.post(
            f"/api/corefs/access/installations/{installation_id}/approve",
            headers=headers,
            json={"confirmed": True},
        )
        assert approved.status_code == 200, approved.text

        expansion = client.put(
            f"/api/corefs/access/installations/{installation_id}/grants/folder-notes",
            headers=headers,
            json={"scope": "read"},
        )
        assert expansion.status_code == 422, expansion.text
        granted = client.put(
            f"/api/corefs/access/installations/{installation_id}/grants/folder-notes",
            headers=headers,
            json={"scope": "read", "confirmed": True},
        )
        assert granted.status_code == 200, granted.text

        route_paths = {route.path for route in access_routes.router.routes}
        assert not any(path.endswith("/register") for path in route_paths)
        assert client.get("/api/corefs/access").status_code == 401
