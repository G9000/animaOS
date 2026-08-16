from __future__ import annotations

import pytest
from anima_server.config import settings
from anima_server.db.runtime import get_runtime_session_factory
from anima_server.models.runtime import RuntimeMessage, RuntimeThread
from anima_server.services.agent import invalidate_agent_runtime_cache
from anima_server.services.corefs.conversation_mutations import (
    ConversationMutationError,
    append_canonical_message,
    delete_canonical_message,
    edit_canonical_message,
)
from anima_server.services.sessions import unlock_session_store
from conftest import managed_test_client
from fastapi.testclient import TestClient
from sqlalchemy import select


def _register_user(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/auth/register",
        json={
            "username": "thread-authority",
            "password": "pw123456",
            "name": "Thread Authority",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_greenfield_thread_lifecycle_is_corefs_authoritative() -> None:
    with managed_test_client("anima-threads-greenfield-") as client:
        registered = _register_user(client)
        token = str(registered["unlockToken"])
        headers = {"x-anima-unlock": token}
        session = unlock_session_store.resolve(token)
        assert session is not None
        assert session.content_authority is not None
        assert session.content_authority["state"] == "authoritative"

        created = client.post("/api/threads", headers=headers)
        assert created.status_code == 201, created.text
        thread_id = int(created.json()["threadId"])

        user_message = append_canonical_message(
            session=session,
            thread_id=thread_id,
            role="user",
            content="CoreFS-only user message",
        )
        assistant_message = append_canonical_message(
            session=session,
            thread_id=thread_id,
            role="assistant",
            content="CoreFS-only assistant message",
        )
        messages = client.get(f"/api/threads/{thread_id}/messages", headers=headers)
        assert messages.status_code == 200, messages.text
        assert [item["content"] for item in messages.json()["messages"]] == [
            "CoreFS-only user message",
            "CoreFS-only assistant message",
        ]

        edited = edit_canonical_message(
            session=session,
            thread_id=thread_id,
            message_id=user_message.message_id,
            content="Edited CoreFS-only user message",
            expected_event_id=user_message.current_event_id,
            expected_version=user_message.version,
        )
        assert edited.version == 2
        with pytest.raises(ConversationMutationError, match="precondition is stale"):
            edit_canonical_message(
                session=session,
                thread_id=thread_id,
                message_id=user_message.message_id,
                content="Stale edit",
                expected_event_id=user_message.current_event_id,
                expected_version=user_message.version,
            )
        assert delete_canonical_message(
            session=session,
            thread_id=thread_id,
            message_id=assistant_message.message_id,
            expected_event_id=assistant_message.current_event_id,
            expected_version=assistant_message.version,
        )

        closed = client.post(f"/api/threads/{thread_id}/close", headers=headers)
        assert closed.status_code == 200, closed.text
        assert closed.json()["status"] == "closed"
        deleted = client.delete(f"/api/threads/{thread_id}", headers=headers)
        assert deleted.status_code == 200, deleted.text
        assert client.get(f"/api/threads/{thread_id}/messages", headers=headers).status_code == 404


def test_greenfield_chat_persists_only_thin_runtime_references(monkeypatch) -> None:
    monkeypatch.setattr(settings, "agent_provider", "scaffold")
    monkeypatch.setattr(settings, "agent_model", "llama3.2")
    monkeypatch.setattr(settings, "agent_base_url", "")
    monkeypatch.setattr(settings, "agent_api_key", "")
    invalidate_agent_runtime_cache()

    with managed_test_client("anima-chat-greenfield-") as client:
        registered = _register_user(client)
        user_id = int(registered["id"])
        headers = {"x-anima-unlock": str(registered["unlockToken"])}
        response = client.post(
            "/api/chat",
            headers=headers,
            json={"message": "Persist this turn in CoreFS", "userId": user_id},
        )
        assert response.status_code == 200, response.text

        history = client.get(
            "/api/chat/history",
            headers=headers,
            params={"userId": user_id, "limit": 10},
        )
        assert history.status_code == 200, history.text
        assert [item["content"] for item in history.json()] == [
            "Persist this turn in CoreFS",
            response.json()["response"],
        ]

        with get_runtime_session_factory()() as runtime_db:
            threads = list(
                runtime_db.scalars(
                    select(RuntimeThread).where(RuntimeThread.user_id == user_id)
                ).all()
            )
            messages = list(
                runtime_db.scalars(
                    select(RuntimeMessage).where(RuntimeMessage.user_id == user_id)
                ).all()
            )
        assert len(threads) == 1
        visible = [message for message in messages if message.corefs_message_id is not None]
        assert len(visible) == 2
        assert all(message.content_text is None for message in visible)
        assert all(message.content_json is None for message in visible)
