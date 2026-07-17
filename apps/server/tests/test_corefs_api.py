from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from anima_server.api.routes import corefs as corefs_route
from anima_server.services.corefs import logical
from anima_server.services.sessions import unlock_session_store
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def corefs_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Generator[TestClient, None, None]:
    app = FastAPI()
    app.include_router(corefs_route.router)
    calls: list[tuple[str, object]] = []
    core_root = str(tmp_path / "core")

    def fake_context(session: object) -> corefs_route.CoreFsRequestContext:
        calls.append(("context", session))
        return corefs_route.CoreFsRequestContext(
            core_root=core_root,
            core_id="core-test",
            keys={"memories": b"unit-test-dek"},
        )

    monkeypatch.setattr(corefs_route, "_resolve_request_context", fake_context)
    try:
        with TestClient(app) as client:
            client._corefs_calls = calls  # type: ignore[attr-defined]
            client._corefs_root = core_root  # type: ignore[attr-defined]
            yield client
    finally:
        unlock_session_store.clear()


def _unlock_headers(user_id: int = 42) -> dict[str, str]:
    token = unlock_session_store.create(user_id, {"memories": b"unit-test-dek"})
    return {"x-anima-unlock": token}


def test_corefs_operation_requires_unlocked_session(corefs_client: TestClient) -> None:
    response = corefs_client.post(
        "/api/corefs/operation",
        json={"operation": "stat", "path": "Diary/today.md"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Session locked. Please sign in again."


def test_user_read_operation_dispatches_with_selected_snapshot(
    corefs_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, ...]] = []
    selected = logical.CoreFsValidationSnapshot(generation=9, catalog_hash="catalog-hash")

    def fake_select(**kwargs: object) -> logical.CoreFsValidationSnapshot:
        calls.append(("select", kwargs))
        return selected

    def fake_stat(**kwargs: object) -> bytes:
        calls.append(("stat", kwargs))
        return b'{"version":"corefs-logical-v1","result":{"path":"Diary/today.md"}}'

    monkeypatch.setattr(corefs_route.logical, "select_validation_snapshot", fake_select)
    monkeypatch.setattr(corefs_route.logical, "stat_v1", fake_stat)

    response = corefs_client.post(
        "/api/corefs/operation",
        headers=_unlock_headers(),
        json={"operation": "stat", "path": "Diary/today.md"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "principal": {"kind": "user", "id": "42", "userId": 42},
        "operation": "stat",
        "selected": {"generation": 9, "catalogHash": "catalog-hash"},
        "result": {"version": "corefs-logical-v1", "result": {"path": "Diary/today.md"}},
    }
    assert calls[0] == (
        "select",
        {
            "core_root": corefs_client._corefs_root,  # type: ignore[attr-defined]
            "core_id": "core-test",
            "keys": {"memories": b"unit-test-dek"},
        },
    )
    stat_kwargs = calls[1][1]
    assert isinstance(stat_kwargs, dict)
    assert stat_kwargs["core_root"] == corefs_client._corefs_root  # type: ignore[attr-defined]
    assert stat_kwargs["core_id"] == "core-test"
    assert stat_kwargs["selected"] == selected
    assert stat_kwargs["path"] == "Diary/today.md"


def test_logical_paths_preserve_surrounding_whitespace(
    corefs_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    selected = logical.CoreFsValidationSnapshot(generation=9, catalog_hash="catalog-hash")

    monkeypatch.setattr(corefs_route.logical, "select_validation_snapshot", lambda **_: selected)

    def fake_stat(**kwargs: object) -> bytes:
        calls.append(kwargs)
        return b'{"version":"corefs-logical-v1","result":{"path":"Diary/Secret "}}'

    monkeypatch.setattr(corefs_route.logical, "stat_v1", fake_stat)

    response = corefs_client.post(
        "/api/corefs/operation",
        headers=_unlock_headers(),
        json={"operation": "stat", "path": "Diary/Secret "},
    )

    assert response.status_code == 200
    assert calls[0]["path"] == "Diary/Secret "


def test_anima_principal_is_distinct_from_user(
    corefs_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = logical.CoreFsValidationSnapshot(generation=1, catalog_hash="hash")
    monkeypatch.setattr(corefs_route.logical, "select_validation_snapshot", lambda **_: selected)
    monkeypatch.setattr(
        corefs_route.logical,
        "list_v1",
        lambda **_: b'{"version":"corefs-logical-v1","result":{"entries":[]}}',
    )

    response = corefs_client.post(
        "/api/corefs/operation",
        headers={**_unlock_headers(), "x-anima-corefs-principal": "anima"},
        json={"operation": "list", "path": "Diary"},
    )

    assert response.status_code == 200
    assert response.json()["principal"] == {"kind": "anima", "id": "anima:42", "userId": 42}


def test_client_principal_fails_closed_without_grant(corefs_client: TestClient) -> None:
    response = corefs_client.post(
        "/api/corefs/operation",
        headers={
            **_unlock_headers(),
            "x-anima-corefs-principal": "client",
            "x-anima-corefs-client-id": "notes-extension",
            "x-anima-corefs-install-digest": "sha256:abc",
        },
        json={"operation": "stat", "path": "Diary/today.md"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == {
        "code": "corefs_client_grant_required",
        "principal": {
            "kind": "client",
            "id": "notes-extension",
            "userId": 42,
            "installDigest": "sha256:abc",
        },
    }


def test_client_identity_headers_imply_client_principal(corefs_client: TestClient) -> None:
    response = corefs_client.post(
        "/api/corefs/operation",
        headers={
            **_unlock_headers(),
            "x-anima-corefs-client-id": "notes-extension",
            "x-anima-corefs-install-digest": "sha256:abc",
        },
        json={"operation": "stat", "path": "Diary/today.md"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "corefs_client_grant_required"
    assert response.json()["detail"]["principal"] == {
        "kind": "client",
        "id": "notes-extension",
        "userId": 42,
        "installDigest": "sha256:abc",
    }


@pytest.mark.parametrize(
    "bad_path",
    [
        "file:///tmp/x",
        "fs/secrets",
        "Diary/\u202esecret.md",
        "Diary/control\u0001.md",
    ],
)
def test_rejects_native_invalid_logical_paths(
    corefs_client: TestClient,
    bad_path: str,
) -> None:
    response = corefs_client.post(
        "/api/corefs/operation",
        headers=_unlock_headers(),
        json={"operation": "stat", "path": bad_path},
    )

    assert response.status_code == 422


def test_search_readiness_requires_generation_for_non_missing_state(
    corefs_client: TestClient,
) -> None:
    response = corefs_client.post(
        "/api/corefs/operation",
        headers=_unlock_headers(),
        json={"operation": "search_readiness", "searchState": "ready"},
    )

    assert response.status_code == 422


def test_write_operations_are_frozen_before_native_mutators(
    corefs_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_mutator(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("native mutator must not be called while migration writes are frozen")

    monkeypatch.setattr(corefs_route.logical, "mkdir", fail_mutator)

    response = corefs_client.post(
        "/api/corefs/operation",
        headers=_unlock_headers(),
        json={"operation": "mkdir", "path": "Diary"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "principal": {"kind": "user", "id": "42", "userId": 42},
        "operation": "mkdir",
        "result": {
            "ok": False,
            "operation": "mkdir",
            "code": logical.CORE_FS_MIGRATION_WRITE_FROZEN,
        },
    }


def test_rejects_host_filesystem_paths(corefs_client: TestClient) -> None:
    response = corefs_client.post(
        "/api/corefs/operation",
        headers=_unlock_headers(),
        json={"operation": "stat", "path": "C:\\Users\\leoca\\secret.txt"},
    )

    assert response.status_code == 422
