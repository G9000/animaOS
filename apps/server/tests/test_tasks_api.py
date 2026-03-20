from __future__ import annotations

from fastapi.testclient import TestClient

from conftest import managed_test_client


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

        resp = client.put(
            f"/api/tasks/{task_id}",
            headers=headers,
            json={"done": True},
        )
        assert resp.status_code == 200
        updated = resp.json()
        assert updated["done"] is True
        assert updated["completedAt"] is not None

        resp = client.delete(f"/api/tasks/{task_id}", headers=headers)
        assert resp.status_code == 200

        resp = client.get(f"/api/tasks?userId={user_id}", headers=headers)
        assert resp.json() == []


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
