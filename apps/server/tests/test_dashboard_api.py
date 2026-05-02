from __future__ import annotations

import pytest
from anima_server import config as config_module
from anima_server.config import settings
from conftest import managed_test_client
from fastapi.testclient import TestClient


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

        resp = client.get(
            f"/api/chat/greeting?userId={user_id}", headers=headers)
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

        resp = client.get(
            f"/api/chat/nudges?userId={user_id}", headers=headers)
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
        assert "anthropic" in names


def test_config_ollama_models(monkeypatch) -> None:
    from anima_server.api.routes import config as config_route

    async def fake_list_ollama_models(base_url: str) -> list[config_route.OllamaModelInfo]:
        assert base_url == "http://localhost:11434"
        return [
            config_route.OllamaModelInfo(
                name="gemma4:31b",
                size=19_000_000_000,
                details=config_route.OllamaModelDetails(
                    family="gemma",
                    parameterSize="31B",
                ),
            )
        ]

    monkeypatch.setattr(config_route, "_list_ollama_models",
                        fake_list_ollama_models)

    with managed_test_client("anima-dashboard-test-") as client:
        resp = client.get(
            "/api/config/ollama-models?baseUrl=http://localhost:11434")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload == [
            {
                "name": "gemma4:31b",
                "modifiedAt": None,
                "size": 19_000_000_000,
                "digest": None,
                "details": {
                    "format": None,
                    "family": "gemma",
                    "families": None,
                    "parameterSize": "31B",
                    "quantizationLevel": None,
                },
            }
        ]


@pytest.mark.asyncio
async def test_list_ollama_models_reads_payload_before_client_close(monkeypatch) -> None:
    from anima_server.api.routes import config as config_route

    class _FakeResponse:
        def __init__(self, *, client) -> None:
            self._client = client

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            if self._client.closed:
                raise RuntimeError("response closed")
            return {"models": [{"name": "gemma4:31b"}]}

    class _FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            assert timeout == 5.0
            self.closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            self.closed = True

        async def get(self, url: str) -> _FakeResponse:
            assert url == "http://localhost:11434/api/tags"
            return _FakeResponse(client=self)

    monkeypatch.setattr(config_route.httpx, "AsyncClient", _FakeAsyncClient)

    models = await config_route._list_ollama_models("http://localhost:11434")

    assert [model.name for model in models] == ["gemma4:31b"]


def test_config_get_update() -> None:
    original_provider = settings.agent_provider
    original_model = settings.agent_model
    original_api_key = settings.agent_api_key
    original_base_url = settings.agent_base_url

    with managed_test_client("anima-dashboard-test-") as client:
        try:
            reg = _register_user(client)
            user_id = reg["id"]
            headers = {"x-anima-unlock": reg["unlockToken"]}

            resp = client.get(f"/api/config/{user_id}", headers=headers)
            assert resp.status_code == 200
            config = resp.json()
            assert "provider" in config
            assert "model" in config
            assert "extractionModel" in config

            resp = client.put(
                f"/api/config/{user_id}",
                headers=headers,
                json={
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "extractionModel": "qwen3:14b",
                    "apiKey": "test-openai-key",
                },
            )
            assert resp.status_code == 200
            assert settings.agent_provider == "openai"
            assert settings.agent_model == "gpt-4o-mini"
            assert settings.agent_extraction_model == "qwen3:14b"
            assert settings.agent_api_key == "test-openai-key"

            settings.agent_provider = "ollama"
            settings.agent_model = "vaultbox/qwen3.5-uncensored:35b"
            settings.agent_api_key = ""
            settings.agent_base_url = ""

            config_module.load_persisted_runtime_settings()

            assert settings.agent_provider == "openai"
            assert settings.agent_model == "gpt-4o-mini"
            assert settings.agent_api_key == "test-openai-key"
            assert settings.agent_base_url == ""
        finally:
            settings.agent_provider = original_provider
            settings.agent_model = original_model
            settings.agent_api_key = original_api_key
            settings.agent_base_url = original_base_url


def test_runtime_settings_persist_and_reload(tmp_path) -> None:
    original_data_dir = settings.data_dir
    original_provider = settings.agent_provider
    original_model = settings.agent_model
    original_extraction_model = settings.agent_extraction_model
    original_api_key = settings.agent_api_key
    original_base_url = settings.agent_base_url

    try:
        settings.data_dir = tmp_path
        settings.agent_provider = "openai"
        settings.agent_model = "gpt-4o-mini"
        settings.agent_extraction_model = "qwen3:14b"
        settings.agent_api_key = "persisted-openai-key"
        settings.agent_base_url = ""

        config_path = config_module.persist_runtime_settings()
        assert config_path.exists()

        settings.agent_provider = "ollama"
        settings.agent_model = "vaultbox/qwen3.5-uncensored:35b"
        settings.agent_extraction_model = ""
        settings.agent_api_key = ""
        settings.agent_base_url = "http://127.0.0.1:11434"

        config_module.load_persisted_runtime_settings()

        assert settings.agent_provider == "openai"
        assert settings.agent_model == "gpt-4o-mini"
        assert settings.agent_extraction_model == "qwen3:14b"
        assert settings.agent_api_key == "persisted-openai-key"
        assert settings.agent_base_url == ""
    finally:
        settings.data_dir = original_data_dir
        settings.agent_provider = original_provider
        settings.agent_model = original_model
        settings.agent_extraction_model = original_extraction_model
        settings.agent_api_key = original_api_key
        settings.agent_base_url = original_base_url


def test_home_journal_streak() -> None:
    with managed_test_client("anima-dashboard-test-") as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        client.post(
            f"/api/memory/{user_id}/items",
            headers=headers,
            json={"content": "Loves hiking in mountains",
                  "category": "preference"},
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

        resp = client.get(
            f"/api/memory/{user_id}/search?q=designer", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["results"][0]["content"] == "Works as a designer"

        resp = client.get(
            f"/api/memory/{user_id}/search?q=dark", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

        resp = client.get(
            f"/api/memory/{user_id}/search?q=zzzzz", headers=headers)
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
