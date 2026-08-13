from __future__ import annotations

import inspect
from collections.abc import Generator
from types import SimpleNamespace

import pytest
from anima_server.api.routes import corefs as corefs_route
from anima_server.services.corefs import logical
from anima_server.services.sessions import unlock_session_store
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


@pytest.fixture()
def corefs_client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    unlock_session_store.start()
    app = FastAPI()
    app.include_router(corefs_route.router)
    calls: list[tuple[str, object]] = []
    native_session = object()

    def fake_context(session: object) -> corefs_route.CoreFsRequestContext:
        calls.append(("context", session))
        return corefs_route.CoreFsRequestContext(
            corefs_session=native_session,
            keys={"memories": b"unit-test-dek"},
        )

    monkeypatch.setattr(corefs_route, "_resolve_request_context", fake_context)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            client._corefs_calls = calls  # type: ignore[attr-defined]
            client._corefs_session = native_session  # type: ignore[attr-defined]
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


def test_corefs_operation_runs_sync_native_work_in_fastapi_threadpool() -> None:
    assert not inspect.iscoroutinefunction(corefs_route.run_corefs_operation)


def test_request_context_uses_corefs_session_subkeys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corefs_keys = object()
    native_session = object()
    session = SimpleNamespace(
        deks={"memories": b"soul-domain-dek"},
        corefs_keys=corefs_keys,
        corefs_session=native_session,
    )
    monkeypatch.setattr(
        corefs_route,
        "get_core_dir",
        lambda: pytest.fail("route must not select a CoreFS root"),
        raising=False,
    )
    monkeypatch.setattr(
        corefs_route,
        "get_core_id",
        lambda: pytest.fail("route must not select a CoreFS ID"),
        raising=False,
    )

    context = corefs_route._resolve_request_context(session)  # type: ignore[arg-type]
    second_context = corefs_route._resolve_request_context(session)  # type: ignore[arg-type]

    assert context.keys is corefs_keys
    assert context.corefs_session is native_session
    assert second_context.corefs_session is native_session


@pytest.mark.parametrize(
    ("corefs_keys", "native_session"),
    [(None, object()), (object(), None), (None, None)],
)
def test_request_context_rejects_incomplete_corefs_authority(
    corefs_keys: object | None,
    native_session: object | None,
) -> None:
    session = SimpleNamespace(
        deks={"memories": b"soul-domain-dek"},
        corefs_keys=corefs_keys,
        corefs_session=native_session,
    )

    with pytest.raises(HTTPException) as exc_info:
        corefs_route._resolve_request_context(session)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 423
    assert exc_info.value.detail["code"] == "corefs_key_material_unavailable"


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
            "corefs_session": corefs_client._corefs_session,  # type: ignore[attr-defined]
            "keys": {"memories": b"unit-test-dek"},
        },
    )
    stat_kwargs = calls[1][1]
    assert isinstance(stat_kwargs, dict)
    assert stat_kwargs["corefs_session"] is corefs_client._corefs_session  # type: ignore[attr-defined]
    assert stat_kwargs["selected"] == selected
    assert stat_kwargs["path"] == "Diary/today.md"


def test_missing_validation_snapshot_maps_to_stable_not_ready_response(
    corefs_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_select(**_kwargs: object) -> logical.CoreFsValidationSnapshot:
        raise ValueError("CoreFS validation snapshot is missing")

    monkeypatch.setattr(corefs_route.logical, "select_validation_snapshot", fail_select)

    response = corefs_client.post(
        "/api/corefs/operation",
        headers=_unlock_headers(),
        json={"operation": "stat", "path": "Diary/today.md"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "corefs_validation_snapshot_missing",
        "message": "CoreFS validation snapshot is missing",
    }


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


@pytest.mark.parametrize(
    "path",
    [
        "Gallery/family\u200dphoto.png",
        "Notes/private\ue000.md",
        "é:notes.md",
        "aé:notes.md",
    ],
)
def test_logical_paths_allow_native_valid_unicode_categories(
    corefs_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    selected = logical.CoreFsValidationSnapshot(generation=9, catalog_hash="catalog-hash")
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(corefs_route.logical, "select_validation_snapshot", lambda **_: selected)
    monkeypatch.setattr(
        corefs_route.logical,
        "stat_v1",
        lambda **kwargs: (
            calls.append(kwargs) or b'{"version":"corefs-logical-v1","result":{"kind":"file"}}'
        ),
    )

    response = corefs_client.post(
        "/api/corefs/operation",
        headers=_unlock_headers(),
        json={"operation": "stat", "path": path},
    )

    assert response.status_code == 200
    assert calls[0]["path"] == path


def test_caller_cannot_claim_anima_principal(
    corefs_client: TestClient,
) -> None:
    response = corefs_client.post(
        "/api/corefs/operation",
        headers={**_unlock_headers(), "x-anima-corefs-principal": "anima"},
        json={"operation": "list", "path": "Diary"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "corefs_caller_identity_forbidden"
    assert corefs_client._corefs_calls == []  # type: ignore[attr-defined]


def test_authenticated_broker_can_derive_anima_principal(
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
    monkeypatch.setattr(
        corefs_route,
        "_principal_from_authenticated_broker",
        lambda _request, session: corefs_route.CoreFsPrincipal(
            kind="anima",
            id=f"anima:{session.user_id}",
            user_id=session.user_id,
        ),
        raising=False,
    )

    response = corefs_client.post(
        "/api/corefs/operation",
        headers=_unlock_headers(),
        json={"operation": "list", "path": "Diary"},
    )

    assert response.status_code == 200
    assert response.json()["principal"] == {"kind": "anima", "id": "anima:42", "userId": 42}


@pytest.mark.parametrize(
    "payload",
    [
        {"operation": "stat", "path": "Diary/today.md"},
        {"operation": "mkdir", "path": "Diary/New"},
    ],
)
def test_authenticated_client_requires_grant_before_any_dispatch(
    corefs_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
) -> None:
    monkeypatch.setattr(
        corefs_route,
        "_principal_from_authenticated_broker",
        lambda _request, session: corefs_route.CoreFsPrincipal(
            kind="client",
            id="notes-extension",
            user_id=session.user_id,
            install_digest="sha256:abc",
        ),
    )

    response = corefs_client.post(
        "/api/corefs/operation",
        headers=_unlock_headers(),
        json=payload,
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "corefs_client_grant_required"
    assert corefs_client._corefs_calls == []  # type: ignore[attr-defined]


def test_one_shot_client_capability_is_folder_checked_before_and_after_dispatch(
    corefs_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = logical.CoreFsValidationSnapshot(generation=9, catalog_hash="catalog-hash")
    identity = corefs_route.ClientCapabilityIdentity(
        installation_id="installation-1",
        client_id="notes-extension",
        package_id="com.example.notes",
        install_digest=f"sha256:{'a' * 64}",
        user_id=42,
    )
    folders = (corefs_route.CoreFsFolderGrantTarget("folder-notes", "Notes", "core.notes"),)
    authorizations: list[dict[str, object]] = []

    monkeypatch.setattr(
        corefs_route.client_capability_broker,
        "consume",
        lambda **kwargs: (
            identity
            if kwargs["token"] == "one-shot-capability"
            else pytest.fail("unexpected capability")
        ),
    )
    monkeypatch.setattr(corefs_route.logical, "select_validation_snapshot", lambda **_: selected)
    monkeypatch.setattr(
        corefs_route,
        "list_corefs_grant_folders",
        lambda _session, *, selected: folders,
    )
    monkeypatch.setattr(
        corefs_route,
        "authorize_client_path",
        lambda _identity, **kwargs: authorizations.append(kwargs) or "folder-notes",
    )
    monkeypatch.setattr(
        corefs_route.logical,
        "stat_v1",
        lambda **_: b'{"version":"corefs-logical-v1","result":{"path":"Notes/today.md"}}',
    )

    response = corefs_client.post(
        "/api/corefs/operation",
        headers={
            **_unlock_headers(),
            "x-anima-corefs-client-capability": "one-shot-capability",
        },
        json={"operation": "stat", "path": "Notes/today.md"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["principal"] == {
        "kind": "client",
        "id": "notes-extension",
        "userId": 42,
        "installDigest": f"sha256:{'a' * 64}",
        "installationId": "installation-1",
        "packageId": "com.example.notes",
    }
    assert [item.get("record_use", False) for item in authorizations] == [False, True]
    assert all(item["logical_path"] == "Notes/today.md" for item in authorizations)
    assert all(item["required_scope"] == "read" for item in authorizations)


def test_caller_cannot_supply_client_identity(corefs_client: TestClient) -> None:
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

    assert response.status_code == 400
    assert response.json()["detail"] == {
        "code": "corefs_caller_identity_forbidden",
        "message": "CoreFS principals are derived from authenticated server state.",
    }


def test_client_identity_headers_cannot_downgrade_to_user(corefs_client: TestClient) -> None:
    response = corefs_client.post(
        "/api/corefs/operation",
        headers={
            **_unlock_headers(),
            "x-anima-corefs-client-id": "notes-extension",
            "x-anima-corefs-install-digest": "sha256:abc",
        },
        json={"operation": "stat", "path": "Diary/today.md"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "corefs_caller_identity_forbidden"
    assert corefs_client._corefs_calls == []  # type: ignore[attr-defined]


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


def test_search_readiness_rejects_caller_supplied_runtime_state(
    corefs_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = logical.CoreFsValidationSnapshot(generation=9, catalog_hash="catalog-hash")
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(corefs_route.logical, "select_validation_snapshot", lambda **_: selected)
    monkeypatch.setattr(
        corefs_route.logical,
        "search_readiness_v1",
        lambda **kwargs: (
            calls.append(kwargs)
            or b'{"version":"corefs-logical-v1","result":{"status":{"state":"ready"}}}'
        ),
    )

    response = corefs_client.post(
        "/api/corefs/operation",
        headers=_unlock_headers(),
        json={
            "operation": "search_readiness",
            "searchState": "ready",
            "indexGeneration": 9,
        },
    )

    assert response.status_code == 422
    assert calls == []


def test_search_readiness_uses_server_runtime_state(
    corefs_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = logical.CoreFsValidationSnapshot(generation=9, catalog_hash="catalog-hash")
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(corefs_route.logical, "select_validation_snapshot", lambda **_: selected)
    monkeypatch.setattr(
        corefs_route,
        "_resolve_search_runtime_state",
        lambda **_: SimpleNamespace(state="building", index_generation=8),
        raising=False,
    )
    monkeypatch.setattr(
        corefs_route.logical,
        "search_readiness_v1",
        lambda **kwargs: (
            calls.append(kwargs)
            or b'{"version":"corefs-logical-v1","result":{"status":{"state":"not_ready"}}}'
        ),
    )

    response = corefs_client.post(
        "/api/corefs/operation",
        headers=_unlock_headers(),
        json={"operation": "search_readiness"},
    )

    assert response.status_code == 200
    assert calls[0]["state"] == "building"
    assert calls[0]["index_generation"] == 8


def test_search_readiness_maps_progressive_index_state_without_private_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.corefs.indexer import CoreFSProgressiveIndex

    index = CoreFSProgressiveIndex("core-index")
    index.unlock(sqlcipher_key=b"s" * 32, local_instance_id="instance-a")
    context = corefs_route.CoreFsRequestContext(
        corefs_session=object(),
        keys=object(),
        runtime_index=index,
    )
    selected = logical.CoreFsValidationSnapshot(
        generation=9,
        catalog_hash="catalog-hash",
    )
    initialized: list[tuple[CoreFSProgressiveIndex, int]] = []
    monkeypatch.setattr(
        corefs_route,
        "initialize_catalog_if_idle",
        lambda candidate, generation: (
            initialized.append((candidate, generation))
            or candidate.begin_catalog()
            or candidate.publish_catalog(
                catalog_generation=generation,
                families={},
            )
            or True
        ),
    )

    building = corefs_route._resolve_search_runtime_state(
        context=context,
        selected=selected,
    )
    assert building.state == "building"
    assert building.index_generation == 9
    assert initialized == [(index, 9)]

    index.begin_catalog()
    index.publish_catalog(
        catalog_generation=9,
        families={"notes": 2},
        degraded={"notes": ("object-hash",)},
    )
    degraded = corefs_route._resolve_search_runtime_state(
        context=context,
        selected=selected,
    )
    assert degraded.state == "degraded"
    assert degraded.index_generation == 9


def test_search_operation_queries_unlock_scoped_exact_text_and_semantic_index(
    corefs_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.corefs.indexer import CoreFSProgressiveIndex

    index = CoreFSProgressiveIndex("core-index")
    index.unlock(sqlcipher_key=b"s" * 32, local_instance_id="instance-a")
    index.begin_catalog()
    index.publish_catalog(catalog_generation=9, families={"notes": 2})
    index.begin_blind_generation(generation=9, expected_count=1)
    index.add_blind_token(generation=9, value="alpha", object_id="note-1")
    index.commit_blind_generation(9)
    index.begin_text_indexing()
    index.index_text(
        family="notes",
        object_id="note-1",
        revision="rev-1",
        text="alpha private note",
    )
    index.index_text(
        family="notes",
        object_id="note-2",
        revision="rev-1",
        text="beta private note",
    )
    index.begin_semantic_indexing()
    index.index_vector(object_id="note-1", vector=(1.0, 0.0))
    index.index_vector(object_id="note-2", vector=(0.0, 1.0))
    index.finish()

    selected = logical.CoreFsValidationSnapshot(
        generation=9,
        catalog_hash="catalog-hash",
    )
    monkeypatch.setattr(
        corefs_route.logical,
        "select_validation_snapshot",
        lambda **_: selected,
    )
    monkeypatch.setattr(
        corefs_route,
        "_resolve_request_context",
        lambda _session: corefs_route.CoreFsRequestContext(
            corefs_session=object(),
            keys=object(),
            runtime_index=index,
        ),
    )
    monkeypatch.setattr(
        corefs_route,
        "embed_configured_query",
        lambda query: (1.0, 0.0),
        raising=False,
    )

    text_response = corefs_client.post(
        "/api/corefs/operation",
        headers=_unlock_headers(),
        json={
            "operation": "search",
            "query": "alpha",
            "searchMode": "text",
            "maxResults": 1,
        },
    )
    exact_response = corefs_client.post(
        "/api/corefs/operation",
        headers=_unlock_headers(),
        json={
            "operation": "search",
            "query": "alpha",
            "searchMode": "exact",
            "maxResults": 1,
        },
    )
    semantic_response = corefs_client.post(
        "/api/corefs/operation",
        headers=_unlock_headers(),
        json={
            "operation": "search",
            "query": "related concept",
            "searchMode": "semantic",
            "maxResults": 1,
        },
    )

    assert text_response.status_code == 200
    assert text_response.json()["result"] == {
        "generation": 9,
        "mode": "text",
        "objectIds": ["note-1"],
    }
    assert exact_response.status_code == 200
    assert exact_response.json()["result"] == {
        "generation": 9,
        "mode": "exact",
        "objectIds": ["note-1"],
    }
    assert semantic_response.status_code == 200
    assert semantic_response.json()["result"] == {
        "generation": 9,
        "mode": "semantic",
        "objectIds": ["note-1"],
    }


def test_cursor_requires_generation(corefs_client: TestClient) -> None:
    response = corefs_client.post(
        "/api/corefs/operation",
        headers=_unlock_headers(),
        json={"operation": "list", "path": "Notes", "cursorAfter": "Notes/A.md"},
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {
            "operation": "read",
            "path": "Notes/A.md",
            "offset": 1 << 64,
        },
        {
            "operation": "grep",
            "root": "Notes",
            "query": "ANIMA",
            "grepCursorPath": "Notes/A.md",
            "grepCursorByteOffset": 1 << 64,
            "cursorGeneration": 9,
        },
    ],
)
def test_rejects_offsets_above_native_u64_before_dispatch(
    corefs_client: TestClient,
    payload: dict[str, object],
) -> None:
    response = corefs_client.post(
        "/api/corefs/operation",
        headers=_unlock_headers(),
        json=payload,
    )

    assert response.status_code == 422
    assert corefs_client._corefs_calls == []  # type: ignore[attr-defined]


def test_stale_cursor_generation_is_rejected_before_native_dispatch(
    corefs_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = logical.CoreFsValidationSnapshot(generation=9, catalog_hash="catalog-hash")
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(corefs_route.logical, "select_validation_snapshot", lambda **_: selected)
    monkeypatch.setattr(
        corefs_route.logical,
        "list_v1",
        lambda **kwargs: (
            calls.append(kwargs) or b'{"version":"corefs-logical-v1","result":{"entries":[]}}'
        ),
    )

    response = corefs_client.post(
        "/api/corefs/operation",
        headers=_unlock_headers(),
        json={
            "operation": "list",
            "path": "Notes",
            "cursorAfter": "Notes/A.md",
            "cursorGeneration": 8,
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "corefs_cursor_generation_mismatch",
        "cursorGeneration": 8,
        "selectedGeneration": 9,
    }
    assert calls == []


@pytest.mark.parametrize(
    ("native_message", "expected_status", "expected_code"),
    [
        ("logical path was not found: Diary/missing.md", 404, "corefs_path_not_found"),
        ("logical path is not a directory: Diary/today.md", 409, "corefs_not_directory"),
        (
            "CoreFS validation snapshot no longer matches selected generation/catalog hash",
            409,
            "corefs_validation_snapshot_stale",
        ),
        (
            "logical_list response item requires 2048 bytes; maximum is 1024",
            413,
            "corefs_response_too_large",
        ),
        (
            "invalid operation limit: list limit must be between 1 and 100",
            422,
            "corefs_invalid_request",
        ),
        ("invalid glob pattern: unterminated character class", 422, "corefs_invalid_request"),
        ("invalid grep pattern: pattern exceeds maximum bytes", 422, "corefs_invalid_request"),
        (
            "invalid grep_limit pattern: responseBytes below maxLineBytes",
            422,
            "corefs_invalid_request",
        ),
    ],
)
def test_native_logical_errors_map_to_stable_client_responses(
    corefs_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    native_message: str,
    expected_status: int,
    expected_code: str,
) -> None:
    selected = logical.CoreFsValidationSnapshot(generation=9, catalog_hash="catalog-hash")
    monkeypatch.setattr(corefs_route.logical, "select_validation_snapshot", lambda **_: selected)

    def fail_stat(**_kwargs: object) -> bytes:
        raise ValueError(native_message)

    monkeypatch.setattr(corefs_route.logical, "stat_v1", fail_stat)

    response = corefs_client.post(
        "/api/corefs/operation",
        headers=_unlock_headers(),
        json={"operation": "stat", "path": "Diary/missing.md"},
    )

    assert response.status_code == expected_status
    assert response.json()["detail"]["code"] == expected_code


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
