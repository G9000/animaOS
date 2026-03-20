from __future__ import annotations

from fastapi.testclient import TestClient

from conftest import managed_test_client


def _register_user(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/auth/register",
        json={"username": "dashtest", "password": "pw123456", "name": "Dash Test"},
    )
    assert response.status_code == 201
    return response.json()


def test_brief_endpoint() -> None:
    with managed_test_client("anima-dashboard-test-") as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.get(f"/api/chat/brief?userId={user_id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "message" in data
        assert "context" in data
        assert "currentFocus" in data["context"]
        assert "openTaskCount" in data["context"]


def test_greeting_endpoint() -> None:
    with managed_test_client("anima-dashboard-test-") as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.get(f"/api/chat/greeting?userId={user_id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "message" in data
        assert isinstance(data["message"], str)
        assert len(data["message"]) > 0
        assert "context" in data
        assert "openTaskCount" in data["context"]
        assert "overdueTasks" in data["context"]
        assert "upcomingDeadlines" in data["context"]
        assert isinstance(data["llmGenerated"], bool)


def test_nudges_endpoint() -> None:
    with managed_test_client("anima-dashboard-test-") as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.get(f"/api/chat/nudges?userId={user_id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "nudges" in data
        assert isinstance(data["nudges"], list)


def test_home_endpoint() -> None:
    with managed_test_client("anima-dashboard-test-") as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.get(f"/api/chat/home?userId={user_id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "currentFocus" in data
        assert "tasks" in data
        assert "memoryCount" in data
        assert "messageCount" in data
        assert data["memoryCount"] == 0
        assert data["messageCount"] == 0


def test_config_providers() -> None:
    with managed_test_client("anima-dashboard-test-") as client:
        resp = client.get("/api/config/providers")
        assert resp.status_code == 200
        providers = resp.json()
        assert len(providers) >= 2
        names = [p["name"] for p in providers]
        assert "scaffold" in names
        assert "ollama" in names


def test_config_get_update() -> None:
    with managed_test_client("anima-dashboard-test-") as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.get(f"/api/config/{user_id}", headers=headers)
        assert resp.status_code == 200
        config = resp.json()
        assert "provider" in config
        assert "model" in config


def test_home_journal_streak() -> None:
    with managed_test_client("anima-dashboard-test-") as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        client.post(
            f"/api/memory/{user_id}/items",
            headers=headers,
            json={"content": "Loves hiking in mountains", "category": "preference"},
        )

        resp = client.get(f"/api/chat/home?userId={user_id}", headers=headers)
        data = resp.json()
        assert data["journalStreak"] == 0
        assert data["journalTotal"] == 0
        assert data["memoryCount"] == 1


def test_memory_search() -> None:
    with managed_test_client("anima-dashboard-test-") as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        client.post(
            f"/api/memory/{user_id}/items",
            headers=headers,
            json={"content": "Works as a designer", "category": "fact"},
        )
        client.post(
            f"/api/memory/{user_id}/items",
            headers=headers,
            json={"content": "Likes dark mode", "category": "preference"},
        )

        resp = client.get(f"/api/memory/{user_id}/search?q=designer", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["results"][0]["content"] == "Works as a designer"

        resp = client.get(f"/api/memory/{user_id}/search?q=dark", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

        resp = client.get(f"/api/memory/{user_id}/search?q=zzzzz", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["count"] == 0


def test_user_directive_get_put() -> None:
    with managed_test_client("anima-dashboard-test-") as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.get(f"/api/soul/{user_id}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["content"] == ""
        assert resp.json()["source"] == "database"

        resp = client.put(
            f"/api/soul/{user_id}",
            headers=headers,
            json={"content": "I am a helpful companion."},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"

        resp = client.get(f"/api/soul/{user_id}", headers=headers)
        assert resp.json()["content"] == "I am a helpful companion."
        assert resp.json()["source"] == "database"
