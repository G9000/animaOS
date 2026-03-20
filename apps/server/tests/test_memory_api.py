from __future__ import annotations

from fastapi.testclient import TestClient

from conftest import managed_test_client


def _register_user(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/auth/register",
        json={"username": "memtest", "password": "pw123456", "name": "Mem Test"},
    )
    assert response.status_code == 201
    return response.json()


def test_memory_crud_lifecycle() -> None:
    with managed_test_client("anima-memory-test-") as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.get(f"/api/memory/{user_id}", headers=headers)
        assert resp.status_code == 200
        overview = resp.json()
        assert overview["totalItems"] == 0
        assert overview["currentFocus"] is None

        resp = client.post(
            f"/api/memory/{user_id}/items",
            headers=headers,
            json={"content": "Works as a designer", "category": "fact", "importance": 4},
        )
        assert resp.status_code == 201
        item = resp.json()
        assert item["content"] == "Works as a designer"
        assert item["category"] == "fact"
        assert item["importance"] == 4
        assert item["source"] == "user"
        item_id = item["id"]

        resp = client.post(
            f"/api/memory/{user_id}/items",
            headers=headers,
            json={"content": "Likes dark mode", "category": "preference"},
        )
        assert resp.status_code == 201

        resp = client.get(f"/api/memory/{user_id}", headers=headers)
        overview = resp.json()
        assert overview["totalItems"] == 2
        assert overview["factCount"] == 1
        assert overview["preferenceCount"] == 1

        resp = client.get(f"/api/memory/{user_id}/items?category=fact", headers=headers)
        assert resp.status_code == 200
        facts = resp.json()
        assert len(facts) == 1
        assert facts[0]["content"] == "Works as a designer"

        resp = client.put(
            f"/api/memory/{user_id}/items/{item_id}",
            headers=headers,
            json={"content": "Works as a product manager"},
        )
        assert resp.status_code == 200
        updated = resp.json()
        assert updated["content"] == "Works as a product manager"
        assert updated["id"] != item_id

        resp = client.get(f"/api/memory/{user_id}/items?category=fact", headers=headers)
        facts = resp.json()
        assert len(facts) == 1
        assert facts[0]["content"] == "Works as a product manager"

        resp = client.delete(
            f"/api/memory/{user_id}/items/{updated['id']}",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

        resp = client.get(f"/api/memory/{user_id}/items?category=fact", headers=headers)
        assert len(resp.json()) == 0


def test_memory_duplicate_rejected() -> None:
    with managed_test_client("anima-memory-test-") as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.post(
            f"/api/memory/{user_id}/items",
            headers=headers,
            json={"content": "Likes tea", "category": "preference"},
        )
        assert resp.status_code == 201

        resp = client.post(
            f"/api/memory/{user_id}/items",
            headers=headers,
            json={"content": "Likes tea", "category": "preference"},
        )
        assert resp.status_code == 409


def test_memory_requires_auth() -> None:
    with managed_test_client("anima-memory-test-") as client:
        resp = client.get("/api/memory/1", headers={})
        assert resp.status_code == 401


def test_memory_episodes_empty() -> None:
    with managed_test_client("anima-memory-test-") as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.get(f"/api/memory/{user_id}/episodes", headers=headers)
        assert resp.status_code == 200
        assert resp.json() == []
