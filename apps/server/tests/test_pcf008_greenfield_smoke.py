# apps/server/tests/test_pcf008_greenfield_smoke.py
"""PCF-008 Step 11 smoke: one continuous greenfield journey on a temporary Core.

Each behavior here has focused coverage elsewhere; this test's job is the
release smoke — a single user on a single isolated Core exercising health,
registration/unlock, portable content across families, relock/re-login
persistence, and the full/Soul/CoreFS V2 transfer round trip, in one process
lifetime, with no mocks on the storage path.
"""

from __future__ import annotations

import time
from pathlib import Path

from anima_server.config import settings
from conftest import managed_test_client
from fastapi.testclient import TestClient

_PASSPHRASE = "smoke-transfer-passphrase"
_POLL_TIMEOUT_SECONDS = 300.0


def _register(client: TestClient) -> tuple[int, dict[str, str]]:
    response = client.post(
        "/api/auth/register",
        json={"username": "smoke", "password": "pw123456", "name": "Smoke"},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    return int(payload["id"]), {"x-anima-unlock": str(payload["unlockToken"])}


def _login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"username": "smoke", "password": "pw123456"},
    )
    assert response.status_code == 200, response.text
    return {"x-anima-unlock": str(response.json()["unlockToken"])}


def _poll_operation(client: TestClient, url: str, headers: dict[str, str]) -> dict[str, object]:
    deadline = time.monotonic() + _POLL_TIMEOUT_SECONDS
    while True:
        response = client.get(url, headers=headers)
        assert response.status_code == 200, response.text
        operation = response.json()
        if operation["state"] in {"completed", "failed", "cancelled"}:
            return operation
        assert time.monotonic() < deadline, f"operation timed out: {operation}"
        time.sleep(0.2)


def _export(
    client: TestClient,
    headers: dict[str, str],
    payload_kind: str,
    destination: Path,
) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    estimate = client.post(
        "/api/corefs/transfer/estimate",
        headers=headers,
        json={"payloadKind": payload_kind},
    )
    assert estimate.status_code == 200, estimate.text
    assert estimate.json()["payloadKind"] == payload_kind

    probe = client.post(
        "/api/corefs/transfer/probe",
        headers=headers,
        json={"payloadKind": payload_kind, "destination": str(destination)},
    )
    assert probe.status_code == 200, probe.text
    assert probe.json()["publicationMode"] == "single_file"

    prepared = client.post(
        "/api/corefs/transfer/prepare",
        headers=headers,
        json={
            "payloadKind": payload_kind,
            "destination": str(destination),
            "finalName": f"smoke-{payload_kind}.anima",
            "passphrase": _PASSPHRASE,
        },
    )
    assert prepared.status_code == 202, prepared.text
    operation = _poll_operation(
        client,
        f"/api/corefs/transfer/operations/{prepared.json()['operationId']}",
        headers,
    )
    assert operation["state"] == "completed", operation
    result_path = Path(str(operation["resultPath"]))
    assert result_path.exists() and result_path.stat().st_size > 0
    assert not list(destination.glob("*.partial")), "partial output must not survive"
    return result_path


def _stage_import(
    client: TestClient,
    headers: dict[str, str],
    archive_path: Path,
    staging_parent: Path,
) -> dict[str, object]:
    staging_parent.mkdir(parents=True, exist_ok=True)
    probe = client.post(
        "/api/corefs/transfer/import/probe",
        headers=headers,
        json={"archivePath": str(archive_path), "stagingParent": str(staging_parent)},
    )
    assert probe.status_code == 200, probe.text

    prepared = client.post(
        "/api/corefs/transfer/import/prepare",
        headers=headers,
        json={
            "archivePath": str(archive_path),
            "stagingParent": str(staging_parent),
            "passphrase": _PASSPHRASE,
        },
    )
    assert prepared.status_code == 202, prepared.text
    operation = _poll_operation(
        client,
        f"/api/corefs/transfer/import/operations/{prepared.json()['operationId']}",
        headers,
    )
    assert operation["state"] == "completed", operation
    return operation


def test_greenfield_release_smoke_journey() -> None:
    with managed_test_client("anima-pcf008-smoke-") as client:
        # Health responds before any authentication.
        health = client.get("/health")
        assert health.status_code == 200, health.text
        assert health.json()["status"] in {"ok", "degraded"}

        user_id, headers = _register(client)
        me = client.get("/api/auth/me", headers=headers)
        assert me.status_code == 200 and me.json()["username"] == "smoke"

        # Portable content across families through canonical CoreFS authority.
        folder = client.post(
            "/api/diary/folders",
            headers=headers,
            json={"userId": user_id, "name": "Smoke"},
        )
        assert folder.status_code == 201, folder.text
        entry = client.post(
            "/api/diary",
            headers=headers,
            json={
                "userId": user_id,
                "entryDate": "2026-08-23",
                "title": "Smoke entry",
                "body": "Written during the PCF-008 release smoke.",
                "folderId": int(folder.json()["id"]),
            },
        )
        assert entry.status_code == 201, entry.text
        entry_id = int(entry.json()["id"])

        task = client.post(
            "/api/tasks",
            headers=headers,
            json={"userId": user_id, "text": "Finish the smoke", "priority": 1},
        )
        assert task.status_code == 201, task.text
        done = client.put(
            f"/api/tasks/{task.json()['id']}",
            headers=headers,
            json={"done": True},
        )
        assert done.status_code == 200 and done.json()["done"] is True

        presence = client.put(
            f"/api/presence/{user_id}",
            headers=headers,
            json={"customInstruction": "Smoke instruction"},
        )
        assert presence.status_code == 200, presence.text

        thread = client.post("/api/threads", headers=headers)
        assert thread.status_code == 201, thread.text
        thread_id = int(thread.json()["threadId"])
        messages = client.get(f"/api/threads/{thread_id}/messages", headers=headers)
        assert messages.status_code == 200, messages.text
        assert messages.json()["messages"] == []

        # Relock, then re-login: the canonical content survives the session.
        logout = client.post("/api/auth/logout", headers=headers)
        assert logout.status_code == 200, logout.text
        assert client.get(f"/api/diary?userId={user_id}", headers=headers).status_code == 401
        headers = _login(client)
        listed = client.get(f"/api/diary?userId={user_id}", headers=headers)
        assert listed.status_code == 200, listed.text
        assert [item["id"] for item in listed.json()] == [entry_id]
        tasks = client.get(f"/api/tasks?userId={user_id}", headers=headers)
        assert tasks.status_code == 200 and tasks.json()[0]["done"] is True
        fetched_presence = client.get(f"/api/presence/{user_id}", headers=headers)
        assert fetched_presence.status_code == 200
        assert fetched_presence.json()["customInstruction"] == "Smoke instruction"

        # Full export, then verified same-volume import staging. The running
        # Core must remain the authority afterwards.
        transfer_root = settings.data_dir.parent / "smoke-transfer"
        full_archive = _export(client, headers, "full", transfer_root / "full")
        full_import = _stage_import(
            client,
            headers,
            full_archive,
            transfer_root / "staging-full",
        )
        assert full_import["payloadKind"] == "full"
        still_listed = client.get(f"/api/diary?userId={user_id}", headers=headers)
        assert still_listed.status_code == 200
        assert [item["id"] for item in still_listed.json()] == [entry_id]

        # Soul-only and CoreFS-only artifacts stage into their degraded
        # recovery states rather than becoming a complete ANIMA.
        soul_archive = _export(client, headers, "soul", transfer_root / "soul")
        soul_import = _stage_import(
            client,
            headers,
            soul_archive,
            transfer_root / "staging-soul",
        )
        assert soul_import["payloadKind"] == "soul"
        assert soul_import["recoveryState"] == "filesystem_missing"

        fs_archive = _export(client, headers, "fs", transfer_root / "fs")
        fs_import = _stage_import(
            client,
            headers,
            fs_archive,
            transfer_root / "staging-fs",
        )
        assert fs_import["payloadKind"] == "fs"
        assert fs_import["recoveryState"] == "recovery_only"

        # V1 CoreFS reattachment stays a closed boundary.
        attach = client.post(
            f"/api/corefs/transfer/import/operations/{fs_import['operationId']}/attach-corefs",
            headers=headers,
        )
        assert attach.status_code == 409, attach.text
        assert attach.json()["details"]["code"] == "corefs_reattachment_not_supported"
