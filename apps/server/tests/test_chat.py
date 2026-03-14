from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from anima_server.config import settings
from anima_server.db.base import Base
from anima_server.db.session import get_db
from anima_server.main import create_app
from anima_server.models import AgentMessage, AgentRun, AgentStep, AgentThread
from anima_server.services.agent import invalidate_agent_runtime_cache
from anima_server.services.agent.openai_compatible_client import (
    OpenAICompatibleResponse,
    OpenAICompatibleStreamChunk,
)
from anima_server.services.sessions import unlock_session_store


def _build_session_factory() -> tuple[Engine, sessionmaker]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=Session,
    )
    return engine, factory


def _register_user(client: TestClient, username: str = "alice") -> dict[str, object]:
    response = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "password": "pw1234",
            "name": "Alice",
        },
    )
    assert response.status_code == 201
    return response.json()


@contextmanager
def _client() -> Generator[TestClient, None, None]:
    engine, factory = _build_session_factory()
    Base.metadata.create_all(bind=engine)

    app = create_app()
    original_data_dir = settings.data_dir

    def override_get_db() -> Generator[Session, None, None]:
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.state.test_session_factory = factory
    unlock_session_store.clear()
    invalidate_agent_runtime_cache()

    with TemporaryDirectory() as temp_dir:
        settings.data_dir = Path(temp_dir) / "anima-data"

        try:
            with TestClient(app) as test_client:
                yield test_client
        finally:
            settings.data_dir = original_data_dir
            invalidate_agent_runtime_cache()
            unlock_session_store.clear()
            app.dependency_overrides.clear()
            Base.metadata.drop_all(bind=engine)
            engine.dispose()


def test_chat_requires_unlocked_session() -> None:
    with _client() as client:
        response = client.post(
            "/api/chat",
            json={"message": "hello", "userId": 1},
        )

    assert response.status_code == 401
    assert response.json() == {"error": "Session locked. Please sign in again."}


def test_chat_returns_scaffold_response_and_tracks_turns() -> None:
    with _client() as client:
        user = _register_user(client)
        headers = {"x-anima-unlock": str(user["unlockToken"])}
        user_id = int(user["id"])

        first = client.post(
            "/api/chat",
            headers=headers,
            json={"message": "hello", "userId": user_id},
        )
        invalidate_agent_runtime_cache()
        second = client.post(
            "/api/chat",
            headers=headers,
            json={"message": "still there?", "userId": user_id},
        )

    assert first.status_code == 200
    assert first.json()["provider"] == "scaffold"
    assert first.json()["model"] == "python-agent-scaffold"
    assert "turn 1" in first.json()["response"]
    assert "Last message: hello" in first.json()["response"]

    assert second.status_code == 200
    assert "turn 2" in second.json()["response"]
    assert second.json()["toolsUsed"] == []


def test_chat_reset_clears_scaffold_thread_state() -> None:
    with _client() as client:
        user = _register_user(client, username="reset-me")
        headers = {"x-anima-unlock": str(user["unlockToken"])}
        user_id = int(user["id"])

        client.post(
            "/api/chat",
            headers=headers,
            json={"message": "first", "userId": user_id},
        )
        reset_response = client.post(
            "/api/chat/reset",
            headers=headers,
            json={"userId": user_id},
        )
        after_reset = client.post(
            "/api/chat",
            headers=headers,
            json={"message": "again", "userId": user_id},
        )
        session = client.app.state.test_session_factory()
        try:
            assert session.query(AgentThread).count() == 1
            assert session.query(AgentMessage).count() == 2
            assert session.query(AgentRun).count() == 1
            assert session.query(AgentStep).count() == 1
        finally:
            session.close()

    assert reset_response.status_code == 200
    assert reset_response.json() == {"status": "reset"}
    assert "turn 1" in after_reset.json()["response"]


def test_chat_persists_runtime_rows() -> None:
    with _client() as client:
        user = _register_user(client, username="persist-me")
        headers = {"x-anima-unlock": str(user["unlockToken"])}
        user_id = int(user["id"])

        response = client.post(
            "/api/chat",
            headers=headers,
            json={"message": "hello", "userId": user_id},
        )

        session = client.app.state.test_session_factory()
        try:
            thread = session.query(AgentThread).one()
            run = session.query(AgentRun).one()
            step = session.query(AgentStep).one()
            messages = (
                session.query(AgentMessage)
                .order_by(AgentMessage.sequence_id)
                .all()
            )
        finally:
            session.close()

    assert response.status_code == 200
    assert thread.user_id == user_id
    assert run.thread_id == thread.id
    assert run.status == "completed"
    assert run.mode == "blocking"
    assert run.stop_reason == "end_turn"
    assert step.run_id == run.id
    assert step.step_index == 0
    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[0].content_text == "hello"
    assert "turn 1" in (messages[1].content_text or "")


def test_chat_stream_returns_sse_events() -> None:
    with _client() as client:
        user = _register_user(client, username="stream-me")
        headers = {"x-anima-unlock": str(user["unlockToken"])}
        user_id = int(user["id"])

        with client.stream(
            "POST",
            "/api/chat",
            headers=headers,
            json={"message": "stream this", "userId": user_id, "stream": True},
        ) as response:
            body = "".join(response.iter_text())

    assert response.status_code == 200
    assert "event: chunk" in body
    assert "event: done" in body
    assert "stream this" in body


def test_chat_stream_ollama_emits_live_chunks(monkeypatch) -> None:
    original_provider = settings.agent_provider
    original_model = settings.agent_model
    original_persona_template = settings.agent_persona_template
    original_base_url = settings.agent_base_url
    original_api_key = settings.agent_api_key

    class FakeStreamingChatClient:
        def __init__(self) -> None:
            self.bound_tools: list[object] = []
            self.tool_choice: str | None = None

        def bind_tools(
            self,
            tools: list[object],
            *,
            tool_choice: str | None = None,
            **_: object,
        ) -> "FakeStreamingChatClient":
            self.bound_tools = list(tools)
            self.tool_choice = tool_choice
            return self

        async def ainvoke(self, input: list[object]) -> OpenAICompatibleResponse:
            assert input
            return OpenAICompatibleResponse(content="hello world")

        async def astream(self, input: list[object]):
            assert input
            yield OpenAICompatibleStreamChunk(content_delta="hello ")
            yield OpenAICompatibleStreamChunk(
                content_delta="world",
                usage_metadata={
                    "prompt_tokens": 5,
                    "completion_tokens": 3,
                    "total_tokens": 8,
                },
                done=True,
            )

    fake_client = FakeStreamingChatClient()

    try:
        settings.agent_provider = "ollama"
        settings.agent_model = "llama3.2"
        settings.agent_persona_template = "default"
        settings.agent_base_url = ""
        settings.agent_api_key = ""
        invalidate_agent_runtime_cache()
        monkeypatch.setattr(
            "anima_server.services.agent.adapters.openai_compatible.create_llm",
            lambda: fake_client,
        )

        with _client() as client:
            user = _register_user(client, username="ollama-stream")
            headers = {"x-anima-unlock": str(user["unlockToken"])}
            user_id = int(user["id"])

            with client.stream(
                "POST",
                "/api/chat",
                headers=headers,
                json={"message": "hello", "userId": user_id, "stream": True},
            ) as response:
                body = "".join(response.iter_text())
    finally:
        settings.agent_provider = original_provider
        settings.agent_model = original_model
        settings.agent_persona_template = original_persona_template
        settings.agent_base_url = original_base_url
        settings.agent_api_key = original_api_key
        invalidate_agent_runtime_cache()

    assert response.status_code == 200
    assert body.count("event: chunk") == 2
    assert "hello " in body
    assert "world" in body
    assert "event: usage" in body
    assert "event: done" in body


def test_chat_ollama_provider_uses_live_adapter_surface(monkeypatch) -> None:
    original_provider = settings.agent_provider
    original_model = settings.agent_model
    original_persona_template = settings.agent_persona_template
    original_base_url = settings.agent_base_url
    original_api_key = settings.agent_api_key

    class FakeLiveChatClient:
        def __init__(self) -> None:
            self.bound_tools: list[object] = []
            self.tool_choice: str | None = None

        def bind_tools(
            self,
            tools: list[object],
            *,
            tool_choice: str | None = None,
            **_: object,
        ) -> "FakeLiveChatClient":
            self.bound_tools = list(tools)
            self.tool_choice = tool_choice
            return self

        async def ainvoke(self, input: list[object]) -> OpenAICompatibleResponse:
            assert input
            return OpenAICompatibleResponse(
                content="hello from ollama adapter",
                usage_metadata={
                    "prompt_tokens": 5,
                    "completion_tokens": 3,
                    "total_tokens": 8,
                },
            )

    fake_client = FakeLiveChatClient()

    try:
        settings.agent_provider = "ollama"
        settings.agent_model = "llama3.2"
        settings.agent_persona_template = "default"
        settings.agent_base_url = ""
        settings.agent_api_key = ""
        invalidate_agent_runtime_cache()
        monkeypatch.setattr(
            "anima_server.services.agent.adapters.openai_compatible.create_llm",
            lambda: fake_client,
        )

        with _client() as client:
            user = _register_user(client, username="ollama-loop")
            headers = {"x-anima-unlock": str(user["unlockToken"])}
            user_id = int(user["id"])

            response = client.post(
                "/api/chat",
                headers=headers,
                json={"message": "hello", "userId": user_id},
            )
    finally:
        settings.agent_provider = original_provider
        settings.agent_model = original_model
        settings.agent_persona_template = original_persona_template
        settings.agent_base_url = original_base_url
        settings.agent_api_key = original_api_key
        invalidate_agent_runtime_cache()

    assert response.status_code == 200
    assert response.json()["provider"] == "ollama"
    assert response.json()["model"] == "llama3.2"
    assert response.json()["response"] == "hello from ollama adapter"
    assert fake_client.tool_choice == "auto"
    assert [getattr(tool, "name", "") for tool in fake_client.bound_tools] == [
        "current_datetime",
        "send_message",
    ]


def test_chat_openrouter_without_api_key_returns_error() -> None:
    original_provider = settings.agent_provider
    original_model = settings.agent_model
    original_api_key = settings.agent_api_key

    try:
        settings.agent_provider = "openrouter"
        settings.agent_model = "openai/gpt-4.1-mini"
        settings.agent_api_key = ""
        invalidate_agent_runtime_cache()

        with _client() as client:
            user = _register_user(client, username="openrouter-missing-key")
            headers = {"x-anima-unlock": str(user["unlockToken"])}
            user_id = int(user["id"])

            response = client.post(
                "/api/chat",
                headers=headers,
                json={"message": "hello", "userId": user_id},
            )
    finally:
        settings.agent_provider = original_provider
        settings.agent_model = original_model
        settings.agent_api_key = original_api_key
        invalidate_agent_runtime_cache()

    assert response.status_code == 503
    assert "ANIMA_AGENT_API_KEY is required" in response.json()["error"]


def test_chat_invalid_persona_template_returns_error() -> None:
    original_provider = settings.agent_provider
    original_persona_template = settings.agent_persona_template

    try:
        settings.agent_provider = "scaffold"
        settings.agent_persona_template = "../broken"
        invalidate_agent_runtime_cache()

        with _client() as client:
            user = _register_user(client, username="bad-persona")
            headers = {"x-anima-unlock": str(user["unlockToken"])}
            user_id = int(user["id"])

            response = client.post(
                "/api/chat",
                headers=headers,
                json={"message": "hello", "userId": user_id},
            )
    finally:
        settings.agent_provider = original_provider
        settings.agent_persona_template = original_persona_template
        invalidate_agent_runtime_cache()

    assert response.status_code == 503
    assert "Invalid persona template name" in response.json()["error"]


def test_chat_compacts_thread_context_into_summary() -> None:
    original_provider = settings.agent_provider
    original_model = settings.agent_model
    original_max_tokens = settings.agent_max_tokens
    original_trigger_ratio = settings.agent_compaction_trigger_ratio
    original_keep_last_messages = settings.agent_compaction_keep_last_messages

    try:
        settings.agent_provider = "scaffold"
        settings.agent_model = "llama3.2"
        settings.agent_max_tokens = 60
        settings.agent_compaction_trigger_ratio = 0.5
        settings.agent_compaction_keep_last_messages = 2
        invalidate_agent_runtime_cache()

        with _client() as client:
            user = _register_user(client, username="compact-me")
            headers = {"x-anima-unlock": str(user["unlockToken"])}
            user_id = int(user["id"])

            first = client.post(
                "/api/chat",
                headers=headers,
                json={
                    "message": "first message with enough text to trigger compaction later",
                    "userId": user_id,
                },
            )
            second = client.post(
                "/api/chat",
                headers=headers,
                json={
                    "message": "second message with enough text to trigger compaction later",
                    "userId": user_id,
                },
            )
            third = client.post(
                "/api/chat",
                headers=headers,
                json={
                    "message": "third message with enough text to trigger compaction later",
                    "userId": user_id,
                },
            )

            session = client.app.state.test_session_factory()
            try:
                in_context_messages = (
                    session.query(AgentMessage)
                    .filter(AgentMessage.is_in_context.is_(True))
                    .order_by(AgentMessage.sequence_id)
                    .all()
                )
                compacted_messages = (
                    session.query(AgentMessage)
                    .filter(AgentMessage.is_in_context.is_(False))
                    .order_by(AgentMessage.sequence_id)
                    .all()
                )
            finally:
                session.close()
    finally:
        settings.agent_provider = original_provider
        settings.agent_model = original_model
        settings.agent_max_tokens = original_max_tokens
        settings.agent_compaction_trigger_ratio = original_trigger_ratio
        settings.agent_compaction_keep_last_messages = original_keep_last_messages
        invalidate_agent_runtime_cache()

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 200
    assert "turn 3" in third.json()["response"]
    summary_messages = [message for message in in_context_messages if message.role == "summary"]
    assert len(summary_messages) == 1
    assert "Conversation summary:" in (summary_messages[0].content_text or "")
    assert any(message.role == "user" for message in compacted_messages)
    assert any(message.role == "assistant" for message in compacted_messages)
