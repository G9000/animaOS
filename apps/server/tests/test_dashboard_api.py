from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from anima_server import config as config_module
from anima_server.config import settings
from conftest import managed_test_client
from fastapi.testclient import TestClient


async def _async_unlocked_ok(request: object, user_id: object) -> None:
    return None


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


def test_greeting_endpoint_uses_runtime_history_for_days_since(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.db.runtime import get_runtime_session_factory
    from anima_server.models.runtime import RuntimeMessage, RuntimeThread

    monkeypatch.setattr(settings, "agent_provider", "scaffold")

    with managed_test_client("anima-dashboard-test-") as client:
        reg = _register_user(client)
        user_id = int(reg["id"])
        headers = {"x-anima-unlock": reg["unlockToken"]}

        last_chat_at = datetime.now(UTC) - timedelta(days=1, minutes=5)
        runtime_factory = get_runtime_session_factory()
        with runtime_factory() as runtime_db:
            runtime_thread = RuntimeThread(
                user_id=user_id,
                status="active",
                created_at=last_chat_at,
                updated_at=last_chat_at,
                last_message_at=last_chat_at,
            )
            runtime_db.add(runtime_thread)
            runtime_db.flush()
            runtime_db.add(
                RuntimeMessage(
                    thread_id=runtime_thread.id,
                    user_id=user_id,
                    sequence_id=1,
                    role="user",
                    content_text="hello from runtime history",
                    created_at=last_chat_at,
                )
            )
            runtime_db.commit()

        resp = client.get(
            f"/api/chat/greeting?userId={user_id}",
            headers=headers,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["context"]["daysSinceLastChat"] == 1
        assert data["message"] == "Good to see you today."


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


def test_proactive_notice_endpoint_accepts_custom_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "agent_provider", "scaffold")

    with managed_test_client("anima-dashboard-test-") as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.get(
            f"/api/chat/proactive-notice?userId={user_id}&instruction=mention%20Tappy",
            headers=headers,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["notice"] is not None
        notice = data["notice"]
        assert notice["source"] == "proactive_notice"
        assert "Tappy" in notice["message"]
        assert notice["contextMessages"] == [
            {
                "role": "assistant",
                "content": notice["message"],
                "source": "proactive_notice",
            }
        ]


def test_presence_config_defaults_and_update() -> None:
    with managed_test_client("anima-dashboard-test-") as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.get(f"/api/presence/{user_id}", headers=headers)

        assert resp.status_code == 200
        assert resp.json() == {
            "userId": user_id,
            "enabled": True,
            "mainChatEnabled": True,
            "homeGreetingContextEnabled": True,
            "taskNudgesEnabled": True,
            "memoryNudgesEnabled": True,
            "checkInNudgesEnabled": True,
            "customInstruction": None,
            "initiativeEnabled": False,
            "quietHoursStart": None,
            "quietHoursEnd": None,
            "dreamSharing": "on_ask",
        }

        resp = client.put(
            f"/api/presence/{user_id}",
            headers=headers,
            json={
                "enabled": True,
                "mainChatEnabled": False,
                "homeGreetingContextEnabled": False,
                "taskNudgesEnabled": False,
                "memoryNudgesEnabled": True,
                "checkInNudgesEnabled": False,
                "customInstruction": "mention Tappy gently",
                "initiativeEnabled": True,
                "quietHoursStart": 22,
                "quietHoursEnd": 7,
                "dreamSharing": "ambient",
            },
        )

        assert resp.status_code == 200
        assert resp.json() == {
            "userId": user_id,
            "enabled": True,
            "mainChatEnabled": False,
            "homeGreetingContextEnabled": False,
            "taskNudgesEnabled": False,
            "memoryNudgesEnabled": True,
            "checkInNudgesEnabled": False,
            "customInstruction": "mention Tappy gently",
            "initiativeEnabled": True,
            "quietHoursStart": 22,
            "quietHoursEnd": 7,
            "dreamSharing": "ambient",
        }


def test_proactive_notice_respects_disabled_main_chat_config() -> None:
    with managed_test_client("anima-dashboard-test-") as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        client.put(
            f"/api/presence/{user_id}",
            headers=headers,
            json={"mainChatEnabled": False},
        )

        resp = client.get(
            f"/api/chat/proactive-notice?userId={user_id}&instruction=mention%20Tappy",
            headers=headers,
        )

        assert resp.status_code == 200
        assert resp.json() == {"notice": None}


def test_proactive_notice_uses_saved_custom_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "agent_provider", "scaffold")

    with managed_test_client("anima-dashboard-test-") as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        client.put(
            f"/api/presence/{user_id}",
            headers=headers,
            json={"customInstruction": "mention Tappy gently"},
        )

        resp = client.get(
            f"/api/chat/proactive-notice?userId={user_id}",
            headers=headers,
        )

        assert resp.status_code == 200
        notice = resp.json()["notice"]
        assert notice is not None
        assert "Tappy" in notice["message"]


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
        doubleword = next(p for p in providers if p["name"] == "doubleword")
        assert doubleword == {
            "name": "doubleword",
            "defaultModel": "Qwen/Qwen3.6-35B-A3B-FP8",
            "requiresApiKey": True,
        }


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


@pytest.mark.asyncio
async def test_config_update_validates_ollama_completion_targets_once(monkeypatch) -> None:
    from anima_server.api.routes import config as config_route
    from starlette.requests import Request

    calls: list[tuple[str, dict[str, str]]] = []

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"capabilities": ["completion", "tools"]}

    class _Client:
        def __init__(self, *, timeout: float) -> None:
            assert timeout == 5.0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url: str, *, json: dict[str, str]) -> _Response:
            calls.append((url, json))
            return _Response()

    original = (
        settings.agent_provider,
        settings.agent_model,
        settings.agent_extraction_provider,
        settings.agent_extraction_model,
        settings.agent_base_url,
    )
    monkeypatch.setattr(config_route.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(config_route, "persist_runtime_settings", lambda: None)
    monkeypatch.setattr(config_route, "require_unlocked_user_async", _async_unlocked_ok)

    try:
        settings.agent_extraction_provider = ""
        result = await config_route.update_config(
            1,
            config_route.AgentConfigUpdateRequest(
                provider="ollama",
                model="qwen3:14b",
                extractionModel="qwen3:14b",
                ollamaUrl="http://localhost:11434/v1",
            ),
            Request({"type": "http", "method": "PUT", "path": "/"}),
            _mode=None,
            db=None,
        )

        assert result == {"status": "updated"}
        assert calls == [
            ("http://localhost:11434/api/show", {"model": "qwen3:14b"})
        ]
    finally:
        (
            settings.agent_provider,
            settings.agent_model,
            settings.agent_extraction_provider,
            settings.agent_extraction_model,
            settings.agent_base_url,
        ) = original


@pytest.mark.asyncio
async def test_config_update_validates_cleared_ollama_url_against_default(
    monkeypatch,
) -> None:
    from anima_server.api.routes import config as config_route
    from starlette.requests import Request

    calls: list[str] = []

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"capabilities": ["completion"]}

    class _Client:
        def __init__(self, *, timeout: float) -> None:
            assert timeout == 5.0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url: str, *, json: dict[str, str]) -> _Response:
            calls.append(url)
            return _Response()

    original = (
        settings.agent_provider,
        settings.agent_model,
        settings.agent_extraction_provider,
        settings.agent_extraction_model,
        settings.agent_base_url,
    )
    monkeypatch.setattr(config_route.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(config_route, "persist_runtime_settings", lambda: None)
    monkeypatch.setattr(config_route, "require_unlocked_user_async", _async_unlocked_ok)

    try:
        settings.agent_provider = "ollama"
        settings.agent_base_url = "http://old-ollama.local:11434"

        result = await config_route.update_config(
            1,
            config_route.AgentConfigUpdateRequest(
                provider="ollama",
                model="qwen3:14b",
                ollamaUrl="",
            ),
            Request({"type": "http", "method": "PUT", "path": "/"}),
            _mode=None,
            db=None,
        )

        assert result == {"status": "updated"}
        assert calls == ["http://127.0.0.1:11434/api/show"]
        assert settings.agent_base_url == ""
    finally:
        (
            settings.agent_provider,
            settings.agent_model,
            settings.agent_extraction_provider,
            settings.agent_extraction_model,
            settings.agent_base_url,
        ) = original


@pytest.mark.asyncio
async def test_config_update_refreshes_unlocked_corefs_semantic_search(
    monkeypatch,
) -> None:
    from anima_server.api.routes import config as config_route
    from starlette.requests import Request

    session = object()
    sibling_session = object()
    refresh_calls: list[object] = []

    async def unlocked(_request: object, _user_id: object) -> object:
        return session

    original = (
        settings.agent_provider,
        settings.agent_model,
        settings.agent_embedding_provider,
        settings.agent_embedding_model,
        settings.agent_embedding_api_key,
        settings.agent_embedding_base_url,
    )
    monkeypatch.setattr(config_route, "persist_runtime_settings", lambda: None)
    monkeypatch.setattr(config_route, "require_unlocked_user_async", unlocked)
    monkeypatch.setattr(
        config_route,
        "active_unlock_sessions",
        lambda _user_id: (session, sibling_session),
        raising=False,
    )
    monkeypatch.setattr(
        config_route,
        "refresh_unlocked_semantic_search",
        lambda current: refresh_calls.append(current),
        raising=False,
    )

    try:
        settings.agent_provider = "openai"
        settings.agent_model = "gpt-4o-mini"
        settings.agent_embedding_provider = "openai"
        settings.agent_embedding_model = "text-embedding-3-small"
        settings.agent_embedding_api_key = ""
        settings.agent_embedding_base_url = ""

        result = await config_route.update_config(
            1,
            config_route.AgentConfigUpdateRequest(
                provider="openai",
                model="gpt-4o-mini",
                embeddingProvider="fastembed",
                embeddingModel="BAAI/bge-small-en-v1.5",
            ),
            Request({"type": "http", "method": "PUT", "path": "/"}),
            _mode=None,
            db=None,
        )

        assert result == {"status": "updated"}
        assert refresh_calls == [session, sibling_session]
    finally:
        (
            settings.agent_provider,
            settings.agent_model,
            settings.agent_embedding_provider,
            settings.agent_embedding_model,
            settings.agent_embedding_api_key,
            settings.agent_embedding_base_url,
        ) = original


@pytest.mark.asyncio
async def test_config_update_rejects_embedding_only_ollama_without_mutation(monkeypatch) -> None:
    from anima_server.api.routes import config as config_route
    from fastapi import HTTPException
    from starlette.requests import Request

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"capabilities": ["embedding"]}

    class _Client:
        def __init__(self, *, timeout: float) -> None:
            assert timeout == 5.0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url: str, *, json: dict[str, str]) -> _Response:
            return _Response()

    original = (
        settings.agent_provider,
        settings.agent_model,
        settings.agent_extraction_provider,
        settings.agent_extraction_model,
        settings.agent_api_key,
        settings.agent_api_keys_json,
        settings.agent_base_url,
    )
    monkeypatch.setattr(config_route.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(config_route, "persist_runtime_settings", lambda: None)
    monkeypatch.setattr(config_route, "require_unlocked_user_async", _async_unlocked_ok)

    try:
        with pytest.raises(HTTPException) as rejected:
            await config_route.update_config(
                1,
                config_route.AgentConfigUpdateRequest(
                    provider="ollama",
                    model="all-minilm:latest",
                    extractionModel="all-minilm:latest",
                    apiKey="must-not-stick",
                    ollamaUrl="http://localhost:11434/v1",
                ),
                Request({"type": "http", "method": "PUT", "path": "/"}),
                _mode=None,
                db=None,
            )

        assert rejected.value.status_code == 422
        assert "completion" in str(rejected.value.detail).lower()
        assert (
            settings.agent_provider,
            settings.agent_model,
            settings.agent_extraction_provider,
            settings.agent_extraction_model,
            settings.agent_api_key,
            settings.agent_api_keys_json,
            settings.agent_base_url,
        ) == original
    finally:
        (
            settings.agent_provider,
            settings.agent_model,
            settings.agent_extraction_provider,
            settings.agent_extraction_model,
            settings.agent_api_key,
            settings.agent_api_keys_json,
            settings.agent_base_url,
        ) = original


@pytest.mark.asyncio
async def test_validate_ollama_completion_model_maps_unreachable_and_malformed(
    monkeypatch,
) -> None:
    import httpx
    from anima_server.api.routes import config as config_route
    from fastapi import HTTPException

    class _UnreachableClient:
        def __init__(self, *, timeout: float) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url: str, *, json: dict[str, str]):
            raise httpx.ConnectError("offline")

    monkeypatch.setattr(config_route.httpx, "AsyncClient", _UnreachableClient)
    with pytest.raises(HTTPException) as unreachable:
        await config_route._validate_ollama_completion_model(
            "http://localhost:11434", "qwen3:14b"
        )
    assert unreachable.value.status_code == 503

    class _MalformedResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[object]:
            return []

    class _MalformedClient(_UnreachableClient):
        async def post(self, url: str, *, json: dict[str, str]):
            return _MalformedResponse()

    monkeypatch.setattr(config_route.httpx, "AsyncClient", _MalformedClient)
    with pytest.raises(HTTPException) as malformed:
        await config_route._validate_ollama_completion_model(
            "http://localhost:11434", "qwen3:14b"
        )
    assert malformed.value.status_code == 422


def test_config_get_update() -> None:
    original_provider = settings.agent_provider
    original_model = settings.agent_model
    original_api_key = settings.agent_api_key
    original_api_keys_json = settings.agent_api_keys_json
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
            assert settings.agent_api_key == ""
            assert config_module.get_provider_api_key("openai") == "test-openai-key"

            settings.agent_provider = "ollama"
            settings.agent_model = "vaultbox/qwen3.5-uncensored:35b"
            settings.agent_api_key = ""
            settings.agent_api_keys_json = "{}"
            settings.agent_base_url = ""

            config_module.load_persisted_runtime_settings()

            assert settings.agent_provider == "openai"
            assert settings.agent_model == "gpt-4o-mini"
            assert settings.agent_api_key == ""
            assert config_module.get_provider_api_key("openai") == "test-openai-key"
            assert settings.agent_base_url == ""
        finally:
            settings.agent_provider = original_provider
            settings.agent_model = original_model
            settings.agent_api_key = original_api_key
            settings.agent_api_keys_json = original_api_keys_json
            settings.agent_base_url = original_base_url


def test_config_update_accepts_doubleword_and_clears_endpoint() -> None:
    original_provider = settings.agent_provider
    original_model = settings.agent_model
    original_extraction_model = settings.agent_extraction_model
    original_api_key = settings.agent_api_key
    original_api_keys_json = settings.agent_api_keys_json
    original_base_url = settings.agent_base_url

    with managed_test_client("anima-dashboard-test-") as client:
        try:
            reg = _register_user(client)
            user_id = reg["id"]
            headers = {"x-anima-unlock": reg["unlockToken"]}
            settings.agent_base_url = "http://127.0.0.1:8000/v1"

            resp = client.put(
                f"/api/config/{user_id}",
                headers=headers,
                json={
                    "provider": "doubleword",
                    "model": "Qwen/Qwen3.6-35B-A3B-FP8",
                    "apiKey": "test-doubleword-key",
                    "ollamaUrl": "http://should-not-stick.local/v1",
                },
            )

            assert resp.status_code == 200
            assert settings.agent_provider == "doubleword"
            assert settings.agent_model == "Qwen/Qwen3.6-35B-A3B-FP8"
            assert settings.agent_api_key == ""
            assert (
                config_module.get_provider_api_key("doubleword")
                == "test-doubleword-key"
            )
            assert settings.agent_base_url == ""
        finally:
            settings.agent_provider = original_provider
            settings.agent_model = original_model
            settings.agent_extraction_model = original_extraction_model
            settings.agent_api_key = original_api_key
            settings.agent_api_keys_json = original_api_keys_json
            settings.agent_base_url = original_base_url


def test_config_update_preserves_legacy_key_when_api_key_omitted() -> None:
    original_provider = settings.agent_provider
    original_model = settings.agent_model
    original_api_key = settings.agent_api_key
    original_api_keys_json = settings.agent_api_keys_json
    original_base_url = settings.agent_base_url

    with managed_test_client("anima-dashboard-test-") as client:
        try:
            reg = _register_user(client)
            user_id = reg["id"]
            headers = {"x-anima-unlock": reg["unlockToken"]}
            settings.agent_api_key = "legacy-openai-key"
            settings.agent_api_keys_json = "{}"

            resp = client.put(
                f"/api/config/{user_id}",
                headers=headers,
                json={
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                },
            )

            assert resp.status_code == 200
            assert settings.agent_provider == "openai"
            assert settings.agent_model == "gpt-4o-mini"
            assert settings.agent_api_key == "legacy-openai-key"
            assert config_module.get_provider_api_key("openai") == ""

            resp = client.get(f"/api/config/{user_id}", headers=headers)
            assert resp.status_code == 200
            assert resp.json()["hasApiKey"] is True
        finally:
            settings.agent_provider = original_provider
            settings.agent_model = original_model
            settings.agent_api_key = original_api_key
            settings.agent_api_keys_json = original_api_keys_json
            settings.agent_base_url = original_base_url


# Every settings field the config PUT route can mutate. The embedding tests
# below snapshot/restore all of them — a PUT also writes agent_provider /
# agent_model / agent_api_keys_json etc., and leaking those (especially a
# non-empty agent_api_keys_json, which disables the legacy agent_api_key
# fallback) breaks unrelated provider tests later in the session.
_CONFIG_MUTATED_SETTINGS = (
    "agent_provider",
    "agent_model",
    "agent_extraction_model",
    "agent_api_key",
    "agent_api_keys_json",
    "agent_base_url",
    "agent_embedding_provider",
    "agent_embedding_model",
    "agent_embedding_api_key",
    "agent_embedding_base_url",
)


def _snapshot_config_settings() -> dict[str, str]:
    return {field: getattr(settings, field) for field in _CONFIG_MUTATED_SETTINGS}


def _restore_config_settings(snapshot: dict[str, str]) -> None:
    for field, value in snapshot.items():
        setattr(settings, field, value)


def test_config_get_returns_resolved_embedding_fields_at_bundled_default() -> None:
    original = _snapshot_config_settings()

    with managed_test_client("anima-dashboard-test-") as client:
        try:
            reg = _register_user(client)
            user_id = reg["id"]
            headers = {"x-anima-unlock": reg["unlockToken"]}

            settings.agent_embedding_provider = ""
            settings.agent_embedding_model = ""
            settings.agent_embedding_api_key = ""
            settings.agent_embedding_base_url = ""

            resp = client.get(f"/api/config/{user_id}", headers=headers)
            assert resp.status_code == 200
            config = resp.json()
            assert config["embeddingProvider"] == "fastembed"
            assert config["embeddingModel"] == "BAAI/bge-small-en-v1.5"
            assert config["embeddingIsExplicit"] is False
            assert config["hasEmbeddingApiKey"] is False
        finally:
            _restore_config_settings(original)


def test_config_get_has_embedding_api_key_reflects_legacy_chat_key_piggyback() -> None:
    """MEDIUM audit fix: hasEmbeddingApiKey must reflect the RICH key
    resolution (``_resolve_embedding_api_key``) the embedding path actually
    uses, not just ``bool(agent_embedding_api_key.strip())``. A legacy
    install with only the flat ``agent_api_key`` set (no per-provider store,
    no dedicated embedding key) that piggybacks embeddings onto the chat
    provider DOES have a usable key for the embedding call — the naive
    check reported False here even though embeddings would actually work."""
    original = _snapshot_config_settings()

    with managed_test_client("anima-dashboard-test-") as client:
        try:
            reg = _register_user(client)
            user_id = reg["id"]
            headers = {"x-anima-unlock": reg["unlockToken"]}

            settings.agent_provider = "openai"
            settings.agent_api_key = "sk-legacy-chat-key"
            settings.agent_api_keys_json = "{}"
            settings.agent_embedding_provider = ""
            settings.agent_embedding_model = "text-embedding-3-small"
            settings.agent_embedding_api_key = ""
            settings.agent_embedding_base_url = ""

            resp = client.get(f"/api/config/{user_id}", headers=headers)
            assert resp.status_code == 200
            config = resp.json()
            # Piggyback resolves the embedding provider to "openai" (the
            # chat provider), which then legitimately reuses the chat key.
            assert config["embeddingProvider"] == "openai"
            assert config["hasEmbeddingApiKey"] is True
        finally:
            _restore_config_settings(original)


def test_config_get_normalizes_legacy_unsupported_piggyback_embedding_provider() -> None:
    """P2 fix: a legacy install that piggybacked embedding intent (model/key
    set, no explicit agent_embedding_provider) onto a chat provider with no
    usable embeddings endpoint for this API (openrouter/anthropic/moonshot)
    used to have resolve_embedding_provider() return that chat provider
    verbatim. The desktop form echoes embeddingProvider on every save, and
    VALID_EMBEDDING_PROVIDERS rejects it -> the user could not save ANY AI
    setting until discovering they had to disable Embeddings Advanced.
    get_config must normalize the response to the bundled default so the
    echoed value is always savable; this must not touch stored settings."""
    original = _snapshot_config_settings()

    with managed_test_client("anima-dashboard-test-") as client:
        try:
            reg = _register_user(client)
            user_id = reg["id"]
            headers = {"x-anima-unlock": reg["unlockToken"]}

            settings.agent_provider = "openrouter"
            settings.agent_embedding_provider = ""
            settings.agent_embedding_model = "x"
            settings.agent_embedding_api_key = ""
            settings.agent_embedding_base_url = ""

            resp = client.get(f"/api/config/{user_id}", headers=headers)
            assert resp.status_code == 200
            config = resp.json()
            assert config["embeddingProvider"] == "fastembed"
            assert config["embeddingIsExplicit"] is False
            # The normalized provider must be paired with ITS OWN default
            # model, not the stale legacy agent_embedding_model ("x") — else
            # echoing the response back on save would pin an invalid model to
            # fastembed and break dense-retrieval load.
            assert config["embeddingModel"] == "BAAI/bge-small-en-v1.5"

            # GET is read-only: the legacy piggyback settings are untouched.
            assert settings.agent_embedding_provider == ""
            assert settings.agent_embedding_model == "x"

            # A subsequent PUT echoing the normalized value must succeed —
            # no save lockout.
            resp = client.put(
                f"/api/config/{user_id}",
                headers=headers,
                json={
                    "provider": "openrouter",
                    "model": "google/gemma-3-27b-it",
                    "embeddingProvider": config["embeddingProvider"],
                    "embeddingModel": config["embeddingModel"],
                },
            )
            assert resp.status_code == 200
        finally:
            _restore_config_settings(original)


def test_config_get_normalizes_legacy_moonshot_hides_leftover_embedding_api_key() -> None:
    """STRUCTURAL FIX 2: after get_config normalizes a legacy moonshot
    embedding config's DISPLAY value to fastembed (moonshot has no default
    embedding model — see VALID_EMBEDDING_PROVIDERS / Fix 1), a leftover
    ``agent_embedding_api_key`` that actually belongs to the REAL resolved
    provider (moonshot) must not leak into ``hasEmbeddingApiKey`` for the
    normalized "fastembed" display value — fastembed has no key concept at
    all, and there is no dropdown path in the UI to clear a key attributed
    to a provider it no longer shows as selected."""
    original = _snapshot_config_settings()

    with managed_test_client("anima-dashboard-test-") as client:
        try:
            reg = _register_user(client)
            user_id = reg["id"]
            headers = {"x-anima-unlock": reg["unlockToken"]}

            settings.agent_provider = "openai"
            settings.agent_embedding_provider = "moonshot"
            settings.agent_embedding_model = ""
            settings.agent_embedding_api_key = "sk-leftover-moonshot-key"
            settings.agent_embedding_base_url = ""

            resp = client.get(f"/api/config/{user_id}", headers=headers)
            assert resp.status_code == 200
            config = resp.json()
            assert config["embeddingProvider"] == "fastembed"
            assert config["embeddingIsExplicit"] is False
            assert config["hasEmbeddingApiKey"] is False

            # GET is read-only: stored settings are untouched.
            assert settings.agent_embedding_provider == "moonshot"
            assert settings.agent_embedding_api_key == "sk-leftover-moonshot-key"
        finally:
            _restore_config_settings(original)


def test_config_get_leaves_explicit_valid_embedding_provider_unchanged() -> None:
    """Regression guard: normalization must only kick in for the unsupported
    legacy-piggyback case — a normal, explicit, valid embedding provider must
    be reported as-is."""
    original = _snapshot_config_settings()

    with managed_test_client("anima-dashboard-test-") as client:
        try:
            reg = _register_user(client)
            user_id = reg["id"]
            headers = {"x-anima-unlock": reg["unlockToken"]}

            settings.agent_provider = "ollama"
            settings.agent_embedding_provider = "openai"
            settings.agent_embedding_model = "text-embedding-3-small"
            settings.agent_embedding_api_key = "sk-embed-test"
            settings.agent_embedding_base_url = ""

            resp = client.get(f"/api/config/{user_id}", headers=headers)
            assert resp.status_code == 200
            config = resp.json()
            assert config["embeddingProvider"] == "openai"
            assert config["embeddingIsExplicit"] is True
            assert config["hasEmbeddingApiKey"] is True
        finally:
            _restore_config_settings(original)


def test_config_update_round_trips_explicit_embedding_provider() -> None:
    original = _snapshot_config_settings()

    with managed_test_client("anima-dashboard-test-") as client:
        try:
            reg = _register_user(client)
            user_id = reg["id"]
            headers = {"x-anima-unlock": reg["unlockToken"]}

            resp = client.put(
                f"/api/config/{user_id}",
                headers=headers,
                json={
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "apiKey": "test-openai-chat-key",
                    "embeddingProvider": "vllm",
                    "embeddingModel": "custom-embed-model",
                    "embeddingApiKey": "sk-embed-test",
                },
            )
            assert resp.status_code == 200
            assert settings.agent_embedding_provider == "vllm"
            assert settings.agent_embedding_model == "custom-embed-model"
            assert settings.agent_embedding_api_key == "sk-embed-test"

            resp = client.get(f"/api/config/{user_id}", headers=headers)
            assert resp.status_code == 200
            config = resp.json()
            assert config["embeddingProvider"] == "vllm"
            assert config["embeddingModel"] == "custom-embed-model"
            assert config["embeddingIsExplicit"] is True
            assert config["hasEmbeddingApiKey"] is True
        finally:
            _restore_config_settings(original)


def test_config_update_resets_embedding_provider_to_bundled_default() -> None:
    original = _snapshot_config_settings()

    with managed_test_client("anima-dashboard-test-") as client:
        try:
            reg = _register_user(client)
            user_id = reg["id"]
            headers = {"x-anima-unlock": reg["unlockToken"]}

            settings.agent_embedding_provider = "openai"
            settings.agent_embedding_model = "text-embedding-3-small"
            settings.agent_embedding_api_key = "sk-embed-test"

            resp = client.put(
                f"/api/config/{user_id}",
                headers=headers,
                json={
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "embeddingProvider": "",
                },
            )
            assert resp.status_code == 200
            assert settings.agent_embedding_provider == ""
            assert settings.agent_embedding_model == ""
            assert settings.agent_embedding_api_key == ""

            resp = client.get(f"/api/config/{user_id}", headers=headers)
            assert resp.status_code == 200
            config = resp.json()
            assert config["embeddingProvider"] == "fastembed"
            assert config["embeddingIsExplicit"] is False
            assert config["hasEmbeddingApiKey"] is False
        finally:
            _restore_config_settings(original)


def test_config_update_reset_also_clears_embedding_base_url(tmp_path) -> None:
    """Regression: resetting embeddingProvider to "" must fully return to
    the bundled fastembed default — including a previously-set
    agent_embedding_base_url. Without clearing it,
    has_embedding_piggyback_intent() still sees a truthy base URL, so
    resolve_embedding_provider() keeps piggybacking on the chat provider and
    the "reset" is a no-op."""
    original = _snapshot_config_settings()
    original_data_dir = settings.data_dir

    with managed_test_client("anima-dashboard-test-") as client:
        try:
            settings.data_dir = tmp_path
            reg = _register_user(client)
            user_id = reg["id"]
            headers = {"x-anima-unlock": reg["unlockToken"]}

            settings.agent_embedding_base_url = "http://x:11434"

            resp = client.put(
                f"/api/config/{user_id}",
                headers=headers,
                json={
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "embeddingProvider": "",
                },
            )
            assert resp.status_code == 200
            assert settings.agent_embedding_base_url == ""
            assert settings.agent_embedding_provider == ""
            assert settings.agent_embedding_model == ""
            assert settings.agent_embedding_api_key == ""

            resp = client.get(f"/api/config/{user_id}", headers=headers)
            assert resp.status_code == 200
            config = resp.json()
            assert config["embeddingProvider"] == "fastembed"
            assert config["embeddingIsExplicit"] is False

            # Persisted, not just in-memory: a restart must not silently
            # revert to the piggyback behavior.
            persisted = json.loads(
                config_module.get_runtime_settings_path().read_text(encoding="utf-8")
            )
            assert persisted["agent_embedding_base_url"] == ""
        finally:
            _restore_config_settings(original)
            settings.data_dir = original_data_dir


def test_config_update_rejects_invalid_embedding_provider() -> None:
    original = _snapshot_config_settings()

    with managed_test_client("anima-dashboard-test-") as client:
        try:
            reg = _register_user(client)
            user_id = reg["id"]
            headers = {"x-anima-unlock": reg["unlockToken"]}

            resp = client.put(
                f"/api/config/{user_id}",
                headers=headers,
                json={
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "embeddingProvider": "not-a-real-provider",
                },
            )
            assert resp.status_code == 400
            assert settings.agent_embedding_provider == original["agent_embedding_provider"]
            assert settings.agent_embedding_model == original["agent_embedding_model"]
            assert settings.agent_embedding_api_key == original["agent_embedding_api_key"]
        finally:
            _restore_config_settings(original)


def test_config_update_accepts_fastembed_as_embedding_provider_but_not_chat_provider() -> None:
    """fastembed is embeddings-only: valid for embeddingProvider, still 400 for
    the chat ``provider`` field — regression guard for both directions."""
    original = _snapshot_config_settings()

    with managed_test_client("anima-dashboard-test-") as client:
        try:
            reg = _register_user(client)
            user_id = reg["id"]
            headers = {"x-anima-unlock": reg["unlockToken"]}

            resp = client.put(
                f"/api/config/{user_id}",
                headers=headers,
                json={"provider": "fastembed", "model": "whatever"},
            )
            assert resp.status_code == 400

            resp = client.put(
                f"/api/config/{user_id}",
                headers=headers,
                json={
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "embeddingProvider": "fastembed",
                    "embeddingModel": "BAAI/bge-small-en-v1.5",
                },
            )
            assert resp.status_code == 200
            assert settings.agent_embedding_provider == "fastembed"
        finally:
            _restore_config_settings(original)


def test_valid_embedding_providers_is_derived_from_skip_reason_and_default_model() -> None:
    """VALID_EMBEDDING_PROVIDERS must be computed from BOTH
    ``embeddings._embedding_skip_reason`` (has an embeddings endpoint) AND
    membership in ``DEFAULT_EMBEDDING_MODELS`` (has a known default
    embedding model) rather than a hand-maintained list, so a provider
    gaining/losing embeddings support — or gaining/losing a default model —
    can't drift silently."""
    from anima_server.api.routes.config import VALID_EMBEDDING_PROVIDERS
    from anima_server.services.agent.embedding_resolution import (
        DEFAULT_EMBEDDING_MODELS,
    )
    from anima_server.services.agent.embeddings import _embedding_skip_reason
    from anima_server.services.agent.llm import SUPPORTED_PROVIDERS

    expected = {
        provider
        for provider in SUPPORTED_PROVIDERS
        if _embedding_skip_reason(provider) is None and provider in DEFAULT_EMBEDDING_MODELS
    } | {"fastembed", ""}
    assert expected == VALID_EMBEDDING_PROVIDERS
    # openrouter/anthropic have no embeddings endpoint (real skip reasons) —
    # assert against the derivation rule, not a hardcoded name list.
    assert "openrouter" not in VALID_EMBEDDING_PROVIDERS
    assert "anthropic" not in VALID_EMBEDDING_PROVIDERS
    assert "ollama" in VALID_EMBEDDING_PROVIDERS
    assert "fastembed" in VALID_EMBEDDING_PROVIDERS
    # moonshot passes the endpoint check but has no default embedding model
    # (P2 audit finding): accepted-but-unusable must not slip through.
    assert "moonshot" not in VALID_EMBEDDING_PROVIDERS
    # Matches the desktop UI's EMBEDDING_PROVIDERS list in AiSettings.tsx
    # exactly: ["fastembed", "ollama", "openai", "vllm", "doubleword"].
    assert VALID_EMBEDDING_PROVIDERS - {""} == {
        "fastembed",
        "ollama",
        "openai",
        "vllm",
        "doubleword",
    }


def test_config_update_rejects_moonshot_as_embedding_provider() -> None:
    """moonshot has an embeddings-shaped endpoint but no known default
    embedding model — accepting it would resolve to a wrong (Ollama)
    catch-all model and 404 while reporting healthy (P2 audit finding)."""
    original = _snapshot_config_settings()

    with managed_test_client("anima-dashboard-test-") as client:
        try:
            reg = _register_user(client)
            user_id = reg["id"]
            headers = {"x-anima-unlock": reg["unlockToken"]}

            resp = client.put(
                f"/api/config/{user_id}",
                headers=headers,
                json={
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "embeddingProvider": "moonshot",
                },
            )
            assert resp.status_code == 400
            assert settings.agent_embedding_provider == original["agent_embedding_provider"]
        finally:
            _restore_config_settings(original)


def test_config_update_rejects_anthropic_as_embedding_provider() -> None:
    """anthropic has no embeddings endpoint; selecting it for embeddings must
    not silently disable dense retrieval (P2 regression guard)."""
    original = _snapshot_config_settings()

    with managed_test_client("anima-dashboard-test-") as client:
        try:
            reg = _register_user(client)
            user_id = reg["id"]
            headers = {"x-anima-unlock": reg["unlockToken"]}

            resp = client.put(
                f"/api/config/{user_id}",
                headers=headers,
                json={
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "embeddingProvider": "anthropic",
                },
            )
            assert resp.status_code == 400
            assert settings.agent_embedding_provider == original["agent_embedding_provider"]
        finally:
            _restore_config_settings(original)


def test_config_update_rejects_openrouter_as_embedding_provider() -> None:
    """openrouter has no embeddings endpoint; selecting it for embeddings must
    not silently disable dense retrieval (P2 regression guard)."""
    original = _snapshot_config_settings()

    with managed_test_client("anima-dashboard-test-") as client:
        try:
            reg = _register_user(client)
            user_id = reg["id"]
            headers = {"x-anima-unlock": reg["unlockToken"]}

            resp = client.put(
                f"/api/config/{user_id}",
                headers=headers,
                json={
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "embeddingProvider": "openrouter",
                },
            )
            assert resp.status_code == 400
            assert settings.agent_embedding_provider == original["agent_embedding_provider"]
        finally:
            _restore_config_settings(original)


def test_config_update_accepts_ollama_as_embedding_provider() -> None:
    original = _snapshot_config_settings()

    with managed_test_client("anima-dashboard-test-") as client:
        try:
            reg = _register_user(client)
            user_id = reg["id"]
            headers = {"x-anima-unlock": reg["unlockToken"]}

            resp = client.put(
                f"/api/config/{user_id}",
                headers=headers,
                json={
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "embeddingProvider": "ollama",
                },
            )
            assert resp.status_code == 200
            assert settings.agent_embedding_provider == "ollama"
        finally:
            _restore_config_settings(original)


def test_config_update_openrouter_valid_as_chat_provider_but_not_embedding() -> None:
    """Regression guard for the asymmetry: openrouter is a fine chat provider
    (it just can't serve embeddings)."""
    original = _snapshot_config_settings()

    with managed_test_client("anima-dashboard-test-") as client:
        try:
            reg = _register_user(client)
            user_id = reg["id"]
            headers = {"x-anima-unlock": reg["unlockToken"]}

            resp = client.put(
                f"/api/config/{user_id}",
                headers=headers,
                json={
                    "provider": "openrouter",
                    "model": "google/gemma-3-27b-it",
                },
            )
            assert resp.status_code == 200
            assert settings.agent_provider == "openrouter"

            resp = client.put(
                f"/api/config/{user_id}",
                headers=headers,
                json={
                    "provider": "openrouter",
                    "model": "google/gemma-3-27b-it",
                    "embeddingProvider": "openrouter",
                },
            )
            assert resp.status_code == 400
        finally:
            _restore_config_settings(original)


def test_config_update_rejects_embedding_model_without_provider_field() -> None:
    """Audit fix (was: "applies_embedding_model_without_provider_field"): a
    PUT carrying embeddingModel with embeddingProvider OMITTED must be
    rejected, not silently applied against whatever embedding provider
    happens to already be configured — see
    test_config_update_without_embedding_fields_leaves_embedding_config_alone
    for the still-valid "send neither field" no-op case. The desktop UI
    always sends embeddingProvider alongside embeddingModel (AiSettings.tsx
    buildEmbeddingUpdate), so this is not a UI regression."""
    original = _snapshot_config_settings()

    with managed_test_client("anima-dashboard-test-") as client:
        try:
            reg = _register_user(client)
            user_id = reg["id"]
            headers = {"x-anima-unlock": reg["unlockToken"]}

            settings.agent_embedding_provider = "vllm"
            settings.agent_embedding_model = "old-embed-model"
            settings.agent_embedding_api_key = ""

            resp = client.put(
                f"/api/config/{user_id}",
                headers=headers,
                json={
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "embeddingModel": "new-embed-model",
                },
            )
            assert resp.status_code == 400
            assert settings.agent_embedding_provider == "vllm"
            assert settings.agent_embedding_model == "old-embed-model"
        finally:
            _restore_config_settings(original)


def test_config_update_rejects_embedding_api_key_without_provider_field() -> None:
    """Audit fix (was: "applies_embedding_api_key_without_provider_field"): a
    PUT carrying embeddingApiKey with embeddingProvider OMITTED must be
    rejected — see the model-without-provider test above for the full
    rationale (has_embedding_piggyback_intent would otherwise force the key
    onto whatever provider the CHAT side is currently using)."""
    original = _snapshot_config_settings()

    with managed_test_client("anima-dashboard-test-") as client:
        try:
            reg = _register_user(client)
            user_id = reg["id"]
            headers = {"x-anima-unlock": reg["unlockToken"]}

            settings.agent_embedding_provider = "openai"
            settings.agent_embedding_model = "text-embedding-3-small"
            settings.agent_embedding_api_key = ""

            resp = client.put(
                f"/api/config/{user_id}",
                headers=headers,
                json={
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "embeddingApiKey": "sk-embed-new",
                },
            )
            assert resp.status_code == 400
            assert settings.agent_embedding_provider == "openai"
            assert settings.agent_embedding_api_key == ""
        finally:
            _restore_config_settings(original)


def test_config_update_without_embedding_fields_leaves_embedding_config_alone() -> None:
    original = _snapshot_config_settings()

    with managed_test_client("anima-dashboard-test-") as client:
        try:
            reg = _register_user(client)
            user_id = reg["id"]
            headers = {"x-anima-unlock": reg["unlockToken"]}

            settings.agent_embedding_provider = "vllm"
            settings.agent_embedding_model = "keep-this-model"
            settings.agent_embedding_api_key = "sk-keep-this"

            resp = client.put(
                f"/api/config/{user_id}",
                headers=headers,
                json={"provider": "openai", "model": "gpt-4o-mini"},
            )
            assert resp.status_code == 200
            assert settings.agent_embedding_provider == "vllm"
            assert settings.agent_embedding_model == "keep-this-model"
            assert settings.agent_embedding_api_key == "sk-keep-this"
        finally:
            _restore_config_settings(original)


def test_config_update_clears_embedding_key_when_provider_switches_without_new_key() -> None:
    """Switching embeddingProvider without supplying a new embeddingApiKey
    must clear the previously-stored key rather than let the old provider's
    secret be reused against the new provider."""
    original = _snapshot_config_settings()

    with managed_test_client("anima-dashboard-test-") as client:
        try:
            reg = _register_user(client)
            user_id = reg["id"]
            headers = {"x-anima-unlock": reg["unlockToken"]}

            settings.agent_embedding_provider = "openai"
            settings.agent_embedding_api_key = "sk-old"

            resp = client.put(
                f"/api/config/{user_id}",
                headers=headers,
                json={
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "embeddingProvider": "doubleword",
                },
            )
            assert resp.status_code == 200
            assert settings.agent_embedding_provider == "doubleword"
            assert settings.agent_embedding_api_key != "sk-old"
            assert settings.agent_embedding_api_key == ""

            resp = client.get(f"/api/config/{user_id}", headers=headers)
            assert resp.status_code == 200
            config = resp.json()
            assert config["embeddingProvider"] == "doubleword"
            assert config["hasEmbeddingApiKey"] is False
        finally:
            _restore_config_settings(original)


def test_config_update_provider_switch_with_new_key_uses_new_key() -> None:
    """Switching embeddingProvider WITH a fresh embeddingApiKey must apply
    the new key, not clear it."""
    original = _snapshot_config_settings()

    with managed_test_client("anima-dashboard-test-") as client:
        try:
            reg = _register_user(client)
            user_id = reg["id"]
            headers = {"x-anima-unlock": reg["unlockToken"]}

            settings.agent_embedding_provider = "openai"
            settings.agent_embedding_api_key = "sk-old"

            resp = client.put(
                f"/api/config/{user_id}",
                headers=headers,
                json={
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "embeddingProvider": "doubleword",
                    "embeddingApiKey": "sk-new",
                },
            )
            assert resp.status_code == 200
            assert settings.agent_embedding_provider == "doubleword"
            assert settings.agent_embedding_api_key == "sk-new"
        finally:
            _restore_config_settings(original)


def test_config_update_same_provider_without_new_key_leaves_key_unchanged() -> None:
    """A PUT that re-sends the SAME embeddingProvider already stored, with no
    embeddingApiKey, must not spuriously clear the existing key — only an
    actual provider change should trigger the clear."""
    original = _snapshot_config_settings()

    with managed_test_client("anima-dashboard-test-") as client:
        try:
            reg = _register_user(client)
            user_id = reg["id"]
            headers = {"x-anima-unlock": reg["unlockToken"]}

            settings.agent_embedding_provider = "openai"
            settings.agent_embedding_api_key = "sk-old"

            resp = client.put(
                f"/api/config/{user_id}",
                headers=headers,
                json={
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "embeddingProvider": "openai",
                },
            )
            assert resp.status_code == 200
            assert settings.agent_embedding_provider == "openai"
            assert settings.agent_embedding_api_key == "sk-old"
        finally:
            _restore_config_settings(original)


def test_config_update_provider_switch_does_not_leak_legacy_chat_key() -> None:
    """A legacy config with only a global chat key (no per-provider store)
    that switches embeddingProvider away from the chat provider must not
    let the embedding resolver fall back to that global key — otherwise the
    NEW embedding provider gets authorized with the OLD chat provider's
    secret. Root-caused in ``_resolve_embedding_api_key``; this is the
    end-to-end regression check through the route."""
    original = _snapshot_config_settings()

    with managed_test_client("anima-dashboard-test-") as client:
        try:
            reg = _register_user(client)
            user_id = reg["id"]
            headers = {"x-anima-unlock": reg["unlockToken"]}

            # Legacy config: global chat key, no per-provider store, no
            # embedding provider configured yet (piggybacks on chat=openai).
            settings.agent_provider = "openai"
            settings.agent_api_key = "sk-legacy-openai"
            settings.agent_api_keys_json = "{}"
            settings.agent_embedding_provider = ""
            settings.agent_embedding_api_key = ""

            resp = client.put(
                f"/api/config/{user_id}",
                headers=headers,
                json={
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "embeddingProvider": "doubleword",
                },
            )
            assert resp.status_code == 200
            assert settings.agent_embedding_provider == "doubleword"
            assert settings.agent_embedding_api_key == ""

            from anima_server.services.agent import embeddings as embeddings_module

            assert embeddings_module._resolve_embedding_api_key("doubleword") == ""

            resp = client.get(f"/api/config/{user_id}", headers=headers)
            assert resp.status_code == 200
            assert resp.json()["hasEmbeddingApiKey"] is False
        finally:
            _restore_config_settings(original)


def test_config_update_provider_switch_clears_stale_embedding_model() -> None:
    """Switching embeddingProvider without an explicit embeddingModel must
    clear the previous provider's stale model — otherwise resolve_embedding_
    model treats it as an explicit override for the NEW provider, which
    likely can't serve it, silently disabling dense embeddings."""
    original = _snapshot_config_settings()

    with managed_test_client("anima-dashboard-test-") as client:
        try:
            reg = _register_user(client)
            user_id = reg["id"]
            headers = {"x-anima-unlock": reg["unlockToken"]}

            settings.agent_embedding_provider = "openai"
            settings.agent_embedding_model = "text-embedding-3-small"
            settings.agent_embedding_api_key = ""

            resp = client.put(
                f"/api/config/{user_id}",
                headers=headers,
                json={
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "embeddingProvider": "fastembed",
                },
            )
            assert resp.status_code == 200
            assert settings.agent_embedding_provider == "fastembed"
            assert settings.agent_embedding_model == ""

            from anima_server.services.agent.embedding_resolution import (
                resolve_embedding_model,
            )

            assert resolve_embedding_model("fastembed") == "BAAI/bge-small-en-v1.5"

            resp = client.get(f"/api/config/{user_id}", headers=headers)
            assert resp.status_code == 200
            config = resp.json()
            assert config["embeddingProvider"] == "fastembed"
            assert config["embeddingModel"] == "BAAI/bge-small-en-v1.5"
        finally:
            _restore_config_settings(original)


def test_config_update_provider_switch_with_explicit_model_keeps_it() -> None:
    """Switching embeddingProvider WITH an explicit embeddingModel must keep
    that model, not clear it."""
    original = _snapshot_config_settings()

    with managed_test_client("anima-dashboard-test-") as client:
        try:
            reg = _register_user(client)
            user_id = reg["id"]
            headers = {"x-anima-unlock": reg["unlockToken"]}

            settings.agent_embedding_provider = "openai"
            settings.agent_embedding_model = "text-embedding-3-small"

            resp = client.put(
                f"/api/config/{user_id}",
                headers=headers,
                json={
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "embeddingProvider": "fastembed",
                    "embeddingModel": "custom-fastembed-model",
                },
            )
            assert resp.status_code == 200
            assert settings.agent_embedding_provider == "fastembed"
            assert settings.agent_embedding_model == "custom-fastembed-model"
        finally:
            _restore_config_settings(original)


def test_config_update_same_provider_without_model_leaves_model_unchanged() -> None:
    """Re-sending the SAME embeddingProvider already stored, with no
    embeddingModel, must not spuriously clear the existing model — only an
    actual provider change should trigger the reset."""
    original = _snapshot_config_settings()

    with managed_test_client("anima-dashboard-test-") as client:
        try:
            reg = _register_user(client)
            user_id = reg["id"]
            headers = {"x-anima-unlock": reg["unlockToken"]}

            settings.agent_embedding_provider = "openai"
            settings.agent_embedding_model = "text-embedding-3-small"

            resp = client.put(
                f"/api/config/{user_id}",
                headers=headers,
                json={
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "embeddingProvider": "openai",
                },
            )
            assert resp.status_code == 200
            assert settings.agent_embedding_provider == "openai"
            assert settings.agent_embedding_model == "text-embedding-3-small"
        finally:
            _restore_config_settings(original)


def test_config_update_provider_switch_clears_stale_embedding_base_url() -> None:
    """A base URL left over from a previous (e.g. local/custom) embedding
    provider must not be replayed against a newly-selected provider —
    ``_resolve_embedding_base_url`` returns ``agent_embedding_base_url``
    verbatim whenever it is set, regardless of which provider is active, so
    an un-cleared stale value would silently misroute the new provider's
    requests."""
    original = _snapshot_config_settings()

    with managed_test_client("anima-dashboard-test-") as client:
        try:
            reg = _register_user(client)
            user_id = reg["id"]
            headers = {"x-anima-unlock": reg["unlockToken"]}

            settings.agent_embedding_provider = "ollama"
            settings.agent_embedding_base_url = "http://stale-ollama-host:11434"

            resp = client.put(
                f"/api/config/{user_id}",
                headers=headers,
                json={
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "embeddingProvider": "openai",
                },
            )
            assert resp.status_code == 200
            assert settings.agent_embedding_provider == "openai"
            assert settings.agent_embedding_base_url == ""
        finally:
            _restore_config_settings(original)


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


def test_config_update_piggyback_echo_is_not_treated_as_provider_switch() -> None:
    """P2: a legacy piggyback config (no raw agent_embedding_provider; embedding
    intent via a set key) resolves GET's embeddingProvider to the chat
    provider, which the desktop echoes on ANY save. That echo must NOT be read
    as a provider switch — doing so would clear the piggyback key/base-URL/
    model on an unrelated settings save and break dense retrieval."""
    original = _snapshot_config_settings()

    with managed_test_client("anima-dashboard-test-") as client:
        try:
            reg = _register_user(client)
            user_id = reg["id"]
            headers = {"x-anima-unlock": reg["unlockToken"]}

            settings.agent_provider = "openai"
            settings.agent_embedding_provider = ""  # piggyback: raw empty
            settings.agent_embedding_model = ""
            settings.agent_embedding_api_key = "sk-piggyback"
            settings.agent_embedding_base_url = ""

            config = client.get(f"/api/config/{user_id}", headers=headers).json()
            # Resolved via piggyback onto the chat provider.
            assert config["embeddingProvider"] == "openai"

            # Save unrelated chat settings, echoing the resolved
            # embeddingProvider with NO fresh embedding key.
            resp = client.put(
                f"/api/config/{user_id}",
                headers=headers,
                json={
                    "provider": "openai",
                    "model": "gpt-4o",
                    "embeddingProvider": config["embeddingProvider"],
                },
            )
            assert resp.status_code == 200

            # The piggyback key survived — the echo was not a switch.
            assert settings.agent_embedding_api_key == "sk-piggyback"
        finally:
            _restore_config_settings(original)


def test_config_update_piggyback_survives_chat_provider_change_in_same_put() -> None:
    """P2: changing the CHAT provider in the same PUT that echoes a piggyback
    embeddingProvider must not clear the piggyback credential. The 'provider
    changed?' check compares against the embedding provider effective BEFORE
    the handler mutates agent_provider — not a fresh resolve that would see the
    already-updated chat provider and misdetect a switch."""
    original = _snapshot_config_settings()

    with managed_test_client("anima-dashboard-test-") as client:
        try:
            reg = _register_user(client)
            user_id = reg["id"]
            headers = {"x-anima-unlock": reg["unlockToken"]}

            settings.agent_provider = "openai"
            settings.agent_embedding_provider = ""  # piggyback onto chat=openai
            settings.agent_embedding_model = ""
            settings.agent_embedding_api_key = "sk-piggyback"
            settings.agent_embedding_base_url = ""

            config = client.get(f"/api/config/{user_id}", headers=headers).json()
            assert config["embeddingProvider"] == "openai"

            # Change the CHAT provider (openai -> vllm) while the form still
            # echoes the stale resolved embeddingProvider ("openai").
            resp = client.put(
                f"/api/config/{user_id}",
                headers=headers,
                json={
                    "provider": "vllm",
                    "model": "some-vllm-model",
                    "ollamaUrl": "http://localhost:8000/v1",
                    "embeddingProvider": config["embeddingProvider"],
                },
            )
            assert resp.status_code == 200

            # The piggyback key survived — the chat change did not trip a false
            # embedding-provider switch.
            assert settings.agent_embedding_api_key == "sk-piggyback"
        finally:
            _restore_config_settings(original)
