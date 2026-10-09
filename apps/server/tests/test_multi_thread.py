"""Tests for multi-thread persistence helpers."""

from __future__ import annotations

import pytest
from anima_server.models.runtime import (
    RuntimeImageMessageLink,
    RuntimeMessage,
    RuntimeThread,
)
from anima_server.services.agent.persistence import (
    append_message,
    create_run,
    create_thread,
    get_or_create_thread,
    list_threads,
    load_thread_history,
)
from conftest_runtime import runtime_db_session
from sqlalchemy.orm import Session

_db_session = runtime_db_session

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde"
)

_COUNTER = 0


def _uid() -> int:
    global _COUNTER
    _COUNTER += 1
    return _COUNTER


@pytest.fixture()
def db() -> Session:  # type: ignore[misc]
    """Provide a runtime session backed by in-memory SQLite for reactivation tests."""
    from anima_server.db.runtime_base import RuntimeBase
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA busy_timeout = 30000")
        cursor.close()

    RuntimeBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


def test_load_thread_history_excludes_archived_history() -> None:
    with _db_session() as db:
        uid = _uid()
        thread = get_or_create_thread(db, uid)
        run = create_run(
            db,
            thread_id=thread.id,
            user_id=uid,
            provider="test",
            model="m",
            mode="blocking",
        )
        db.flush()

        # Real message (visible to agent)
        append_message(
            db,
            thread=thread,
            run_id=run.id,
            step_id=None,
            sequence_id=1,
            role="user",
            content_text="hello",
        )

        # Archived history message (should NOT appear in agent context)
        append_message(
            db,
            thread=thread,
            run_id=run.id,
            step_id=None,
            sequence_id=2,
            role="user",
            content_text="old message from archive",
            is_archived_history=True,
        )
        db.flush()

        history = load_thread_history(db, thread.id)
        contents = [m.content for m in history]
        assert "hello" in contents
        assert "old message from archive" not in contents


def test_list_threads_sorted_by_last_message() -> None:
    from datetime import UTC, datetime

    with _db_session() as db:
        uid = _uid()
        t1 = RuntimeThread(
            user_id=uid,
            status="active",
            last_message_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        t2 = RuntimeThread(
            user_id=uid,
            status="closed",
            last_message_at=datetime(2026, 3, 1, tzinfo=UTC),
        )
        db.add_all([t1, t2])
        db.flush()

        threads = list_threads(db, user_id=uid)
        assert len(threads) == 2
        # list_threads returns (RuntimeThread, first_role) rows.
        assert threads[0][0].id == t2.id  # most recent first


def test_list_threads_sorts_empty_threads_by_created_at() -> None:
    from datetime import UTC, datetime

    with _db_session() as db:
        uid = _uid()
        older = RuntimeThread(
            user_id=uid,
            status="closed",
            created_at=datetime(2026, 4, 1, tzinfo=UTC),
        )
        newer = RuntimeThread(
            user_id=uid,
            status="active",
            created_at=datetime(2026, 4, 2, tzinfo=UTC),
        )
        db.add_all([older, newer])
        db.flush()

        threads = list_threads(db, user_id=uid)
        assert [thread.id for thread, _first_role in threads] == [newer.id, older.id]


def test_list_threads_excludes_other_users() -> None:
    with _db_session() as db:
        uid_a = _uid()
        uid_b = _uid()
        t = RuntimeThread(user_id=uid_a, status="active")
        db.add(t)
        db.flush()

        threads = list_threads(db, user_id=uid_b)
        assert len(threads) == 0


def test_create_thread_returns_active_thread() -> None:
    with _db_session() as db:
        uid = _uid()
        thread = create_thread(db, uid)
        assert thread.id is not None
        assert thread.user_id == uid
        assert thread.status == "active"


def test_reactivate_thread_with_current_native_messages(current_conversation):
    from anima_server.services.corefs.conversation_authority import get_canonical_thread

    current = current_conversation
    thread = _native_thread(current)
    _native_message(current, thread, "authored native message")
    _native_thread(current)  # closes the target in canonical storage
    before = current.db.query(RuntimeMessage).filter_by(thread_id=thread.id).count()
    _reactivate(current, thread)
    assert thread.status == "active" and thread.is_archived is False
    assert (
        get_canonical_thread(session=current.session, thread_id=thread.id).document.status
        == "active"
    )
    assert current.db.query(RuntimeMessage).filter_by(thread_id=thread.id).count() == before
    assert [m["content"] for m in _display(current, thread)] == ["authored native message"]


def test_reactivate_closes_other_owned_active_thread(current_conversation):
    from anima_server.services.corefs.conversation_authority import get_canonical_thread

    current = current_conversation
    resumed = _native_thread(current)
    _native_message(current, resumed, "older authored content")
    active = _native_thread(current)
    displaced = _reactivate(current, resumed)
    assert displaced == [active.id]
    assert resumed.status == "active" and active.status == "closed"
    assert (
        get_canonical_thread(session=current.session, thread_id=resumed.id).document.status
        == "active"
    )
    assert (
        get_canonical_thread(session=current.session, thread_id=active.id).document.status
        == "closed"
    )


def test_reactivate_leaves_foreign_owner_reference_untouched(current_conversation):
    from anima_server.services.agent.thread_manager import reactivate_thread_if_needed

    current = current_conversation
    resumed = _native_thread(current)
    _native_message(current, resumed, "owned original")
    active = _native_thread(current)
    foreign = RuntimeThread(user_id=current.user_id + 1, status="active")
    current.db.add(foreign)
    current.db.flush()
    authored = _segments(current, resumed)
    assert _reactivate(current, resumed) == [active.id]
    assert foreign.status == "active" and foreign.closed_at is None
    with pytest.raises(RuntimeError, match="authenticated Core"):
        reactivate_thread_if_needed(
            current.db, thread=foreign, user_id=current.user_id, transcripts_dir=None, dek=None
        )
    assert foreign.status == "active"
    assert _segments(current, resumed) == authored


def test_maybe_set_thread_title(db: Session) -> None:
    from anima_server.services.agent.thread_manager import maybe_set_thread_title

    # Sets title from a cleaned request instead of the raw prompt.
    thread = RuntimeThread(user_id=1, status="active", title=None)
    maybe_set_thread_title(db, thread, "Can you help me fix the chat sidebar layout?")
    assert thread.title == "Fix the chat sidebar layout"

    # Does not overwrite existing title
    maybe_set_thread_title(db, thread, "New message")
    assert thread.title == "Fix the chat sidebar layout"

    # Leaves generic greetings untitled so the UI can fall back to date/time.
    thread2 = RuntimeThread(user_id=1, status="active", title=None)
    maybe_set_thread_title(db, thread2, "hello")
    assert thread2.title is None

    # Truncates long substantive prompts cleanly.
    thread3 = RuntimeThread(user_id=1, status="active", title=None)
    maybe_set_thread_title(
        db,
        thread3,
        "Please help me make the desktop sidebar thread titles shorter and less repetitive because it looks messy.",
    )
    assert thread3.title is not None
    assert thread3.title.startswith("Make the desktop sidebar")
    assert thread3.title.endswith("...")


def test_display_ignores_runtime_tool_duplicates_of_native_reply(current_conversation):
    current = current_conversation
    thread = _native_thread(current)
    _native_message(current, thread, "hello")
    pills = [
        {
            "kind": "document_source",
            "label": "CITED DOCS",
            "ref": "corefs://object/01J00000000000000000000006",
        }
    ]
    _native_message(
        current,
        thread,
        "Hi there!",
        role="assistant",
        metadata={"pills": pills, "source": "native-reply"},
    )
    current.db.add_all(
        [
            RuntimeMessage(
                thread_id=thread.id,
                user_id=current.user_id,
                sequence_id=3,
                role="assistant",
                content_text="forged Runtime wrapper",
                content_json={"tool_calls": [{"id": "synthetic-send", "name": "send_message"}]},
            ),
            RuntimeMessage(
                thread_id=thread.id,
                user_id=current.user_id,
                sequence_id=4,
                role="tool",
                content_text="Hi there!",
                tool_name="send_message",
                tool_call_id="synthetic-send",
            ),
        ]
    )
    current.db.flush()
    messages = _display(current, thread)
    assert [(m["role"], m["content"]) for m in messages] == [
        ("user", "hello"),
        ("assistant", "Hi there!"),
    ]
    assert messages[1]["pills"] == pills and messages[1]["source"] == "native-reply"
    assert messages[1]["retrieval"] is None


def test_native_reactivation_ignores_unsupported_jsonl_sidecar(current_conversation, tmp_path):
    current = current_conversation
    thread = _native_thread(current)
    _native_message(current, thread, "native authored message")
    _native_message(current, thread, "native authored reply", role="assistant")
    authored = _segments(current, thread)
    _native_thread(current)
    sidecars = _unsupported_sidecar(tmp_path, thread, current.user_id, "forged JSONL replacement")
    _reactivate(current, thread, transcripts=sidecars)
    assert [m["content"] for m in _display(current, thread, transcripts=sidecars)] == [
        "native authored message",
        "native authored reply",
    ]
    assert _segments(current, thread) == authored
    assert current.db.query(RuntimeMessage).filter_by(is_archived_history=True).count() == 0


def test_native_image_and_source_links_survive_runtime_loss(current_conversation):
    import hashlib

    from anima_server.models.runtime import RuntimeImageAsset
    from anima_server.services.agent.attachments import (
        read_attachment_bytes,
        resolve_corefs_chat_attachment,
    )
    from anima_server.services.corefs.asset_authority import open_corefs_byte_source
    from anima_server.services.corefs.asset_mutations import upsert_canonical_binary_asset
    from anima_server.services.corefs.catalog_reader import stable_opaque_id

    current = current_conversation
    image_id = stable_opaque_id("native-thread-image", "original")
    document_id = stable_opaque_id("native-thread-source", "original")
    image_uri = f"corefs://object/{image_id}"
    document_uri = f"corefs://object/{document_id}"
    upsert_canonical_binary_asset(
        session=current.session,
        stable_id=image_id,
        name="original.png",
        object_kind="gallery-asset",
        content_type="image/png",
        data=PNG_BYTES,
    )
    upsert_canonical_binary_asset(
        session=current.session,
        stable_id=document_id,
        name="source.pdf",
        object_kind="attachment",
        content_type="application/pdf",
        data=b"authored cited source",
    )
    thread = _native_thread(current)
    reference = _native_message(current, thread, "see original", attachments=(image_uri,))
    pills = [{"kind": "document_source", "label": "CITED DOCS", "ref": document_uri}]
    _native_message(
        current,
        thread,
        "answer from source",
        role="assistant",
        metadata={"pills": pills, "source": "cited-native-source"},
    )
    asset = RuntimeImageAsset(
        user_id=current.user_id,
        filename="",
        mime_type="",
        storage_path="",
        sha256=hashlib.sha256(PNG_BYTES).hexdigest(),
        size_bytes=len(PNG_BYTES),
        width=1,
        height=1,
        retention_state="active",
    )
    current.db.add(asset)
    current.db.flush()
    current.db.add(
        RuntimeImageMessageLink(
            user_id=current.user_id,
            message_id=reference.id,
            image_asset_id=asset.id,
            attachment_id=image_id,
        )
    )
    current.db.commit()
    assert current.db.query(RuntimeImageMessageLink).count() == 1
    authored = _segments(current, thread)
    _lose_runtime_conversations(current)
    messages = _display(current, thread)
    assert messages[0]["attachments"] == [{"corefsUri": image_uri}]
    assert messages[1]["pills"] == pills and messages[1]["source"] == "cited-native-source"
    attachment = resolve_corefs_chat_attachment(session=current.session, object_uri=image_uri)
    assert read_attachment_bytes(attachment.path) == PNG_BYTES
    assert (
        open_corefs_byte_source(
            session=current.session,
            object_uri=document_uri,
            expected_kinds=frozenset({"attachment"}),
        ).read_all(max_bytes=1024)
        == b"authored cited source"
    )
    assert _segments(current, thread) == authored
    assert current.db.query(RuntimeImageMessageLink).count() == 0


def test_trashed_native_image_cannot_fall_back_to_jsonl_attachment(current_conversation, tmp_path):
    from anima_server.services.agent.attachments import resolve_corefs_chat_attachment
    from anima_server.services.corefs.asset_authority import CoreFsSourceError
    from anima_server.services.corefs.asset_mutations import (
        trash_canonical_asset,
        upsert_canonical_binary_asset,
    )
    from anima_server.services.corefs.catalog_reader import stable_opaque_id

    current = current_conversation
    image_id = stable_opaque_id("native-thread-image", "trashed")
    uri = f"corefs://object/{image_id}"
    upsert_canonical_binary_asset(
        session=current.session,
        stable_id=image_id,
        name="deleted.png",
        object_kind="gallery-asset",
        content_type="image/png",
        data=PNG_BYTES,
    )
    thread = _native_thread(current)
    _native_message(current, thread, "authored image message", attachments=(uri,))
    authored = _segments(current, thread)
    assert trash_canonical_asset(session=current.session, stable_id=image_id)
    _native_thread(current)
    sidecars = _unsupported_sidecar(
        tmp_path, thread, current.user_id, "sidecar may not revive deleted original"
    )
    (sidecars / "deleted.png").write_bytes(PNG_BYTES)
    _reactivate(current, thread, transcripts=sidecars)
    assert _display(current, thread)[0]["content"] == "authored image message"
    with pytest.raises(CoreFsSourceError):
        resolve_corefs_chat_attachment(session=current.session, object_uri=uri)
    assert _segments(current, thread) == authored


def test_native_display_metadata_survives_runtime_loss_and_ignores_sidecar(
    current_conversation, tmp_path
):
    current = current_conversation
    thread = _native_thread(current)
    pills = [
        {
            "kind": "document_source",
            "label": "CITED DOCS",
            "ref": "corefs://object/01J00000000000000000000006",
        }
    ]
    _native_message(
        current,
        thread,
        "native answer",
        role="assistant",
        metadata={"pills": pills, "source": "authenticated-native"},
    )
    authored = _segments(current, thread)
    _lose_runtime_conversations(current)
    sidecars = _unsupported_sidecar(tmp_path, thread, current.user_id, "forged archive answer")
    result = _display(current, thread, transcripts=sidecars)
    assert len(result) == 1
    assert result[0]["content"] == "native answer"
    assert result[0]["pills"] == pills and result[0]["source"] == "authenticated-native"
    assert result[0]["retrieval"] is None
    assert _segments(current, thread) == authored


@pytest.fixture
def current_conversation():
    from types import SimpleNamespace

    from anima_server.db.runtime import get_runtime_session_factory
    from anima_server.services.sessions import unlock_session_store
    from conftest import managed_test_client

    with managed_test_client("multi-thread-current-core-") as client:
        registered = client.post(
            "/api/auth/register",
            json={"username": "thread-owner", "name": "Thread owner", "password": "password-one"},
        )
        assert registered.status_code == 201, registered.text
        session = unlock_session_store.resolve(registered.json()["unlockToken"])
        assert session is not None
        with get_runtime_session_factory()() as db:
            yield SimpleNamespace(db=db, session=session, user_id=session.user_id, client=client)


def _native_thread(current):
    from anima_server.services.agent.persistence import ensure_runtime_thread_reference
    from anima_server.services.corefs.conversation_mutations import create_canonical_thread

    view = create_canonical_thread(session=current.session, force_new=True)
    for row in current.db.query(RuntimeThread).filter_by(user_id=current.user_id, status="active"):
        row.status = "closed"
    return ensure_runtime_thread_reference(
        current.db, user_id=current.user_id, thread_id=int(view.document.legacy_thread_id)
    )


def _native_message(current, thread, content, *, role="user", attachments=(), metadata=None):
    from anima_server.services.agent.persistence import append_corefs_message_reference
    from anima_server.services.corefs.conversation_mutations import append_canonical_message

    message = append_canonical_message(
        session=current.session,
        thread_id=thread.id,
        role=role,
        content=content,
        attachment_uris=attachments,
        display_metadata=metadata,
    )
    reference = append_corefs_message_reference(
        current.db, thread=thread, message=message, run_id=None
    )
    current.db.commit()
    return reference


def _segments(current, thread):
    from anima_server.services.corefs.conversation_authority import get_canonical_thread

    view = get_canonical_thread(session=current.session, thread_id=thread.id)
    assert view is not None
    return tuple(segment.segment.data for segment in view.segments)


def _reactivate(current, thread, *, transcripts=None):
    from anima_server.services.agent.thread_manager import reactivate_thread_if_needed

    return reactivate_thread_if_needed(
        current.db, thread=thread, user_id=current.user_id, transcripts_dir=transcripts, dek=None
    )


def _display(current, thread, *, transcripts=None):
    from anima_server.services.agent.thread_manager import get_thread_messages_for_display

    return get_thread_messages_for_display(
        current.db, thread=thread, user_id=current.user_id, transcripts_dir=transcripts, dek=None
    )


def _lose_runtime_conversations(current):
    from anima_server.models.runtime import RuntimeImageAsset
    from sqlalchemy import delete

    for model in (RuntimeImageMessageLink, RuntimeMessage, RuntimeImageAsset, RuntimeThread):
        current.db.execute(delete(model))
    current.db.commit()
    assert current.db.query(RuntimeMessage).count() == 0


def _unsupported_sidecar(tmp_path, thread, user_id, content):
    import json

    directory = tmp_path / "unsupported-transcripts"
    directory.mkdir()
    (directory / f"2026-01-01_thread-{thread.id}.jsonl").write_text(
        json.dumps(
            {
                "role": "assistant",
                "content": content,
                "seq": 1,
                "retrieval": {"retriever": "forged"},
            }
        ),
        encoding="utf-8",
    )
    (directory / f"2026-01-01_thread-{thread.id}.meta.json").write_text(
        json.dumps(
            {"thread_id": thread.id, "user_id": user_id, "summary": "forged legacy summary"}
        ),
        encoding="utf-8",
    )
    return directory


def test_unsupported_sidecar_cannot_activate_a_missing_native_thread(
    current_conversation, tmp_path
):
    from anima_server.services.corefs.conversation_authority import get_canonical_thread

    current = current_conversation
    authored = _native_thread(current)
    _native_message(current, authored, "current original must remain unchanged")
    before = _segments(current, authored)
    missing = RuntimeThread(id=999999, user_id=current.user_id, status="closed", is_archived=True)
    assert get_canonical_thread(session=current.session, thread_id=missing.id) is None
    current.db.add(missing)
    current.db.flush()
    sidecars = _unsupported_sidecar(
        tmp_path, missing, current.user_id, "legacy JSONL must not become canonical content"
    )
    with pytest.raises(ValueError, match="not found in authenticated Core"):
        _reactivate(current, missing, transcripts=sidecars)
    assert missing.status == "closed"
    assert _segments(current, authored) == before
    assert [message["content"] for message in _display(current, authored)] == [
        "current original must remain unchanged"
    ]
