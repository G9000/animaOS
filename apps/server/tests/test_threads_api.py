from __future__ import annotations

from anima_server.config import settings
from anima_server.db.runtime import get_runtime_session_factory
from anima_server.db.session import get_user_session_factory
from anima_server.models.runtime import RuntimeMessage, RuntimeThread
from anima_server.services.agent import invalidate_agent_runtime_cache
from anima_server.services.corefs import logical
from anima_server.services.corefs.conversation_migration import (
    prepare_conversation_validation_catalog,
)
from anima_server.services.corefs.conversation_mutations import append_canonical_message
from anima_server.services.corefs.cutover import (
    approve_validation_cutover,
    begin_migration,
    publish_validation_readonly,
    reconcile_cutover_authority,
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
    assert response.status_code == 201
    return response.json()


def test_global_cutover_routes_thread_lifecycle_only_through_corefs(monkeypatch) -> None:
    monkeypatch.setattr(settings, "agent_provider", "scaffold")
    monkeypatch.setattr(settings, "agent_model", "llama3.2")
    monkeypatch.setattr(settings, "agent_base_url", "")
    monkeypatch.setattr(settings, "agent_api_key", "")
    invalidate_agent_runtime_cache()
    with managed_test_client("anima-threads-corefs-authority-") as client:
        registered = _register_user(client)
        user_id = int(registered["id"])
        token = str(registered["unlockToken"])
        headers = {"x-anima-unlock": token}

        initial = client.post("/api/threads", headers=headers)
        assert initial.status_code == 201, initial.text
        legacy_thread_id = int(initial.json()["threadId"])

        runtime_factory = get_runtime_session_factory()
        with runtime_factory() as runtime_db:
            thread = runtime_db.get(RuntimeThread, legacy_thread_id)
            assert thread is not None
            thread.next_message_sequence = 2
            runtime_db.add(
                RuntimeMessage(
                    thread_id=legacy_thread_id,
                    user_id=user_id,
                    sequence_id=1,
                    role="user",
                    content_text="Preserve this canonical message",
                )
            )
            runtime_db.commit()

        session = unlock_session_store.resolve(token)
        assert session is not None
        with (
            get_user_session_factory(user_id)() as soul_db,
            runtime_factory() as runtime_db,
        ):
            prepared, shadow = prepare_conversation_validation_catalog(
                session=session,
                soul_db=soul_db,
                runtime_db=runtime_db,
                transcripts_dir=settings.data_dir / "transcripts",
            )
        assert prepared.published is True
        assert shadow.thread_count == 1
        assert shadow.message_count == 1

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
                int(selected["generation"]),
                str(selected["catalogHash"]),
            ),
            principal="user",
            mutation={"operation": "mkdir", "path": "Conversation activation proof"},
        )
        marker = reconcile_cutover_authority(
            corefs_session=session.corefs_session,
            keys=session.corefs_keys,
        )
        assert marker is not None
        object.__setattr__(session, "content_authority", marker)

        listed = client.get("/api/threads", headers=headers)
        assert listed.status_code == 200, listed.text
        assert [item["id"] for item in listed.json()["threads"]] == [legacy_thread_id]
        messages = client.get(
            f"/api/threads/{legacy_thread_id}/messages",
            headers=headers,
        )
        assert messages.status_code == 200, messages.text
        assert [item["content"] for item in messages.json()["messages"]] == [
            "Preserve this canonical message"
        ]

        created = client.post("/api/threads", headers=headers)
        assert created.status_code == 201, created.text
        canonical_thread_id = int(created.json()["threadId"])
        assert canonical_thread_id != legacy_thread_id
        before_messages = session.corefs_session.validation_snapshot(session.corefs_keys)
        user_message = append_canonical_message(
            session=session,
            thread_id=canonical_thread_id,
            role="user",
            content="CoreFS-only user message",
        )
        assistant_message = append_canonical_message(
            session=session,
            thread_id=canonical_thread_id,
            role="assistant",
            content="CoreFS-only assistant message",
        )
        assert user_message.sequence == 1
        assert assistant_message.sequence == 2
        after_messages = session.corefs_session.validation_snapshot(session.corefs_keys)
        assert int(after_messages["generation"]) == int(before_messages["generation"]) + 2
        canonical_messages = client.get(
            f"/api/threads/{canonical_thread_id}/messages",
            headers=headers,
        )
        assert canonical_messages.status_code == 200, canonical_messages.text
        assert [item["content"] for item in canonical_messages.json()["messages"]] == [
            "CoreFS-only user message",
            "CoreFS-only assistant message",
        ]

        reset = client.post(
            "/api/chat/reset",
            headers=headers,
            json={"userId": user_id},
        )
        assert reset.status_code == 200, reset.text
        assert reset.json() == {"status": "reset"}
        after_reset = client.get("/api/threads", headers=headers)
        active = next(item for item in after_reset.json()["threads"] if item["status"] == "active")
        reset_thread_id = int(active["id"])
        assert reset_thread_id not in {legacy_thread_id, canonical_thread_id}

        chat = client.post(
            "/api/chat",
            headers=headers,
            json={
                "message": "Persist this turn only in CoreFS",
                "userId": user_id,
            },
        )
        assert chat.status_code == 200, chat.text
        assert "turn" in chat.json()["response"]
        canonical_turn = client.get(
            f"/api/threads/{reset_thread_id}/messages",
            headers=headers,
        )
        assert canonical_turn.status_code == 200, canonical_turn.text
        assert [item["content"] for item in canonical_turn.json()["messages"]] == [
            "Persist this turn only in CoreFS",
            chat.json()["response"],
        ]
        with client.stream(
            "POST",
            "/api/chat",
            headers=headers,
            json={
                "message": "Stream this turn only through CoreFS",
                "userId": user_id,
                "stream": True,
            },
        ) as streamed:
            streamed_body = "".join(streamed.iter_text())
        assert streamed.status_code == 200
        assert "event: done" in streamed_body
        after_stream = client.get(
            f"/api/threads/{reset_thread_id}/messages",
            headers=headers,
        )
        assert [item["role"] for item in after_stream.json()["messages"]] == [
            "user",
            "assistant",
            "user",
            "assistant",
        ], streamed_body

        closed = client.post(
            f"/api/threads/{reset_thread_id}/close",
            headers=headers,
        )
        assert closed.status_code == 200, closed.text
        assert closed.json() == {"status": "closed", "threadId": reset_thread_id}
        closed_again = client.post(
            f"/api/threads/{reset_thread_id}/close",
            headers=headers,
        )
        assert closed_again.status_code == 200, closed_again.text
        assert closed_again.json() == {
            "status": "already_closed",
            "threadId": reset_thread_id,
        }

        deleted = client.delete(
            f"/api/threads/{legacy_thread_id}",
            headers=headers,
        )
        assert deleted.status_code == 200, deleted.text
        assert deleted.json() == {
            "status": "deleted",
            "threadId": legacy_thread_id,
            "assetsDeleted": 0,
            "filesDeleted": 0,
        }
        assert (
            client.get(
                f"/api/threads/{legacy_thread_id}/messages",
                headers=headers,
            ).status_code
            == 404
        )

        with runtime_factory() as runtime_db:
            runtime_threads = list(
                runtime_db.scalars(
                    select(RuntimeThread).where(RuntimeThread.user_id == user_id)
                ).all()
            )
            runtime_messages = list(
                runtime_db.scalars(
                    select(RuntimeMessage).where(RuntimeMessage.user_id == user_id)
                ).all()
            )
        runtime_threads_by_id = {thread.id: thread for thread in runtime_threads}
        assert runtime_threads_by_id[legacy_thread_id].status == "active"
        assert reset_thread_id in runtime_threads_by_id
        legacy_messages = [
            message for message in runtime_messages if message.thread_id == legacy_thread_id
        ]
        assert [message.content_text for message in legacy_messages] == [
            "Preserve this canonical message"
        ]
        canonical_references = [
            message for message in runtime_messages if message.thread_id == reset_thread_id
        ]
        assert len(canonical_references) == 4
        assert all(message.content_text is None for message in canonical_references)
        assert all(message.content_json is None for message in canonical_references)
        assert all(message.corefs_message_id for message in canonical_references)
        assert all(message.corefs_event_id for message in canonical_references)
