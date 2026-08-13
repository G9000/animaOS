from __future__ import annotations

from anima_server.db.session import get_user_session_factory
from anima_server.models import Task
from anima_server.services.corefs import logical
from anima_server.services.corefs.cutover import (
    approve_validation_cutover,
    begin_migration,
    publish_validation_readonly,
    reconcile_cutover_authority,
)
from anima_server.services.corefs.diary_migration import (
    read_prepared_writing_body,
    read_prepared_writing_snapshot,
)
from anima_server.services.corefs.formats import decode_task_document
from anima_server.services.sessions import unlock_session_store
from conftest import managed_test_client
from fastapi.testclient import TestClient


def _register_user(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/auth/register",
        json={"username": "tasktest", "password": "pw123456", "name": "Task Test"},
    )
    assert response.status_code == 201
    return response.json()


def test_tasks_crud_lifecycle() -> None:
    with managed_test_client("anima-tasks-test-") as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.get(f"/api/tasks?userId={user_id}", headers=headers)
        assert resp.status_code == 200
        assert resp.json() == []

        resp = client.post(
            "/api/tasks",
            headers=headers,
            json={"userId": user_id, "text": "Buy groceries", "priority": 3},
        )
        assert resp.status_code == 201
        task = resp.json()
        assert task["text"] == "Buy groceries"
        assert task["priority"] == 3
        assert task["done"] is False
        task_id = task["id"]
        session = unlock_session_store.resolve(str(reg["unlockToken"]))
        assert session is not None
        prepared = next(
            item
            for item in read_prepared_writing_snapshot(session=session).objects
            if item.kind == "task"
        )
        assert decode_task_document(
            read_prepared_writing_body(session=session, item=prepared)
        ).text == "Buy groceries"

        resp = client.put(
            f"/api/tasks/{task_id}",
            headers=headers,
            json={"done": True},
        )
        assert resp.status_code == 200
        updated = resp.json()
        assert updated["done"] is True
        assert updated["completedAt"] is not None
        prepared = next(
            item
            for item in read_prepared_writing_snapshot(session=session).objects
            if item.kind == "task"
        )
        assert decode_task_document(
            read_prepared_writing_body(session=session, item=prepared)
        ).done is True

        resp = client.delete(f"/api/tasks/{task_id}", headers=headers)
        assert resp.status_code == 200

        resp = client.get(f"/api/tasks?userId={user_id}", headers=headers)
        assert resp.json() == []
        assert not any(
            item.kind == "task"
            for item in read_prepared_writing_snapshot(session=session).objects
        )


def test_global_cutover_routes_task_crud_only_through_corefs() -> None:
    with managed_test_client("anima-tasks-corefs-authority-") as client:
        reg = _register_user(client)
        user_id = int(reg["id"])
        headers = {"x-anima-unlock": str(reg["unlockToken"])}
        created = client.post(
            "/api/tasks",
            headers=headers,
            json={"userId": user_id, "text": "Encrypted authority", "priority": 4},
        )
        assert created.status_code == 201, created.text

        session = unlock_session_store.resolve(str(reg["unlockToken"]))
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
            mutation={"operation": "mkdir", "path": "Task activation proof"},
        )
        marker = reconcile_cutover_authority(
            corefs_session=session.corefs_session,
            keys=session.corefs_keys,
        )
        assert marker is not None
        object.__setattr__(session, "content_authority", marker)
        with get_user_session_factory(user_id)() as db:
            row = db.get(Task, int(created.json()["id"]))
            assert row is not None
            row.text = "Divergent legacy value"
            db.commit()

        listed = client.get(f"/api/tasks?userId={user_id}", headers=headers)
        assert listed.status_code == 200
        assert [task["text"] for task in listed.json()] == ["Encrypted authority"]

        canonical_created = client.post(
            "/api/tasks",
            headers=headers,
            json={"userId": user_id, "text": "CoreFS only", "priority": 2},
        )
        assert canonical_created.status_code == 201, canonical_created.text
        canonical_id = int(canonical_created.json()["id"])
        with get_user_session_factory(user_id)() as db:
            assert db.get(Task, canonical_id) is None
            legacy = db.get(Task, int(created.json()["id"]))
            assert legacy is not None and legacy.text == "Divergent legacy value"

        updated = client.put(
            f"/api/tasks/{canonical_id}",
            headers=headers,
            json={"done": True, "dueDate": "2026-08-20"},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["done"] is True
        assert updated.json()["dueDate"] == "2026-08-20"

        deleted = client.delete(f"/api/tasks/{canonical_id}", headers=headers)
        assert deleted.status_code == 200, deleted.text
        listed = client.get(f"/api/tasks?userId={user_id}", headers=headers)
        assert [task["text"] for task in listed.json()] == ["Encrypted authority"]


def test_tasks_require_auth() -> None:
    with managed_test_client("anima-tasks-test-") as client:
        resp = client.get("/api/tasks?userId=1")
        assert resp.status_code == 401


def test_tasks_reject_invalid_due_date() -> None:
    with managed_test_client("anima-tasks-test-") as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.post(
            "/api/tasks",
            headers=headers,
            json={"userId": user_id, "text": "Buy groceries", "dueDate": "tomorrow"},
        )
        assert resp.status_code == 422


def test_tasks_reject_blank_text() -> None:
    with managed_test_client("anima-tasks-test-") as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.post(
            "/api/tasks",
            headers=headers,
            json={"userId": user_id, "text": "   "},
        )
        assert resp.status_code == 422
