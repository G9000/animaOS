from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
    monkeypatch.setattr(config_route, "require_unlocked_user", lambda request, user_id: None)

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
    monkeypatch.setattr(config_route, "require_unlocked_user", lambda request, user_id: None)

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
    monkeypatch.setattr(config_route, "require_unlocked_user", lambda request, user_id: None)

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


def test_config_update_applies_embedding_model_without_provider_field() -> None:
    """A PUT carrying only embeddingModel must update the current embedding
    config — not silently no-op because embeddingProvider was omitted."""
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
            assert resp.status_code == 200
            assert settings.agent_embedding_provider == "vllm"
            assert settings.agent_embedding_model == "new-embed-model"

            resp = client.get(f"/api/config/{user_id}", headers=headers)
            assert resp.status_code == 200
            config = resp.json()
            assert config["embeddingProvider"] == "vllm"
            assert config["embeddingModel"] == "new-embed-model"
        finally:
            _restore_config_settings(original)


def test_config_update_applies_embedding_api_key_without_provider_field() -> None:
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
            assert resp.status_code == 200
            assert settings.agent_embedding_provider == "openai"
            assert settings.agent_embedding_api_key == "sk-embed-new"

            resp = client.get(f"/api/config/{user_id}", headers=headers)
            assert resp.status_code == 200
            config = resp.json()
            assert config["embeddingProvider"] == "openai"
            assert config["hasEmbeddingApiKey"] is True
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
