"""P5: Transcript Archive tests."""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from anima_server.db.base import Base
from anima_server.db.runtime_base import RuntimeBase
from anima_server.models import runtime as _runtime_models  # noqa: F401
from anima_server.services import anima_core_retrieval as retrieval_module
from cryptography.exceptions import InvalidTag
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@compiles(BigInteger, "sqlite")
def _compile_biginteger_sqlite(type_: BigInteger, compiler: object, **kw: object) -> str:
    return "INTEGER"


@pytest.fixture()
def runtime_db() -> Session:
    """In-memory SQLite session with runtime tables."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    RuntimeBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def soul_db() -> Session:
    """In-memory SQLite session with soul tables."""
    from anima_server.models.user import User

    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    session.add(User(id=1, username="test",
                display_name="Test", password_hash="x"))
    session.commit()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def transcripts_dir(managed_tmp_path: Path) -> Path:
    directory = managed_tmp_path / "transcripts"
    directory.mkdir()
    return directory


@pytest.fixture()
def test_dek() -> bytes:
    return os.urandom(32)


class TestRuntimeThreadSchema:
    def test_closed_at_field_exists(self, runtime_db: Session) -> None:
        from datetime import UTC, datetime

        from anima_server.models.runtime import RuntimeThread

        thread = RuntimeThread(user_id=1, status="active")
        runtime_db.add(thread)
        runtime_db.flush()

        assert thread.closed_at is None

        thread.closed_at = datetime.now(UTC)
        runtime_db.flush()
        assert thread.closed_at is not None

    def test_is_archived_field_defaults_false(self, runtime_db: Session) -> None:
        from anima_server.models.runtime import RuntimeThread

        thread = RuntimeThread(user_id=1, status="active")
        runtime_db.add(thread)
        runtime_db.flush()

        assert thread.is_archived is False

    def test_multiple_threads_per_user(self, runtime_db: Session) -> None:
        """After P5, unique constraint on user_id is removed."""
        from anima_server.models.runtime import RuntimeThread

        t1 = RuntimeThread(user_id=1, status="closed")
        t2 = RuntimeThread(user_id=1, status="active")
        runtime_db.add_all([t1, t2])
        runtime_db.flush()

        threads = runtime_db.scalars(
            select(RuntimeThread).where(RuntimeThread.user_id == 1)
        ).all()
        assert len(threads) == 2

    def test_only_one_active_thread_per_user(self, runtime_db: Session) -> None:
        from anima_server.models.runtime import RuntimeThread

        runtime_db.add(RuntimeThread(user_id=1, status="active"))
        runtime_db.flush()

        runtime_db.add(RuntimeThread(user_id=1, status="active"))
        with pytest.raises(IntegrityError):
            runtime_db.flush()

        runtime_db.rollback()


class TestSettingsSchema:
    def test_transcript_retention_days_defaults_forever(self) -> None:
        from anima_server.config import settings

        assert settings.transcript_retention_days == -1


class TestMemoryEpisodeSchema:
    def test_transcript_ref_field_exists(self, soul_db: Session) -> None:
        from anima_server.models.agent_runtime import MemoryEpisode

        episode = MemoryEpisode(
            user_id=1,
            date="2026-03-28",
            summary="Discussed archival design.",
        )
        soul_db.add(episode)
        soul_db.flush()

        assert episode.transcript_ref is None

        episode.transcript_ref = "2026-03-28_thread-14.jsonl.enc"
        soul_db.flush()

        loaded = soul_db.scalar(
            select(MemoryEpisode).where(MemoryEpisode.id == episode.id)
        )
        assert loaded is not None
        assert loaded.transcript_ref == "2026-03-28_thread-14.jsonl.enc"


class TestThreadLifecycle:
    def test_get_or_create_thread_finds_active(self, runtime_db: Session) -> None:
        from anima_server.models.runtime import RuntimeThread
        from anima_server.services.agent.persistence import get_or_create_thread

        closed = RuntimeThread(user_id=1, status="closed")
        runtime_db.add(closed)
        runtime_db.flush()

        thread = get_or_create_thread(runtime_db, user_id=1)
        assert thread.status == "active"
        assert thread.id != closed.id

    def test_get_or_create_thread_ignores_closed(self, runtime_db: Session) -> None:
        from anima_server.models.runtime import RuntimeThread
        from anima_server.services.agent.persistence import get_or_create_thread

        closed = RuntimeThread(user_id=1, status="closed")
        runtime_db.add(closed)
        runtime_db.flush()

        thread = get_or_create_thread(runtime_db, user_id=1)
        assert thread.id != closed.id
        assert thread.status == "active"

    def test_close_thread(self, runtime_db: Session) -> None:
        from anima_server.models.runtime import RuntimeThread
        from anima_server.services.agent.persistence import close_thread, get_or_create_thread

        thread = get_or_create_thread(runtime_db, user_id=1)
        original_id = thread.id

        assert close_thread(runtime_db, thread_id=original_id) is True
        runtime_db.flush()

        closed = runtime_db.get(RuntimeThread, original_id)
        assert closed is not None
        assert closed.status == "closed"
        assert closed.closed_at is not None

    def test_close_thread_idempotent(self, runtime_db: Session) -> None:
        from anima_server.services.agent.persistence import close_thread, get_or_create_thread

        thread = get_or_create_thread(runtime_db, user_id=1)
        close_thread(runtime_db, thread_id=thread.id)
        runtime_db.flush()

        result = close_thread(runtime_db, thread_id=thread.id)
        assert result is False

    def test_list_transcript_messages(self, runtime_db: Session) -> None:
        from anima_server.models.runtime import RuntimeMessage
        from anima_server.services.agent.persistence import (
            get_or_create_thread,
            list_transcript_messages,
        )

        thread = get_or_create_thread(runtime_db, user_id=1)

        for i, (role, content) in enumerate(
            [
                ("user", "Hello"),
                ("assistant", "Hi there!"),
                ("tool", "result data"),
                ("system", "System prompt"),
                ("approval", "Approved"),
            ],
            start=1,
        ):
            runtime_db.add(
                RuntimeMessage(
                    thread_id=thread.id,
                    user_id=1,
                    sequence_id=i,
                    role=role,
                    content_text=content,
                )
            )
        runtime_db.flush()

        msgs = list_transcript_messages(runtime_db, thread_id=thread.id)
        roles = [m.role for m in msgs]
        assert "system" not in roles
        assert "approval" not in roles
        assert len(msgs) == 3


class TestBlobEncryption:
    def test_encrypt_decrypt_roundtrip(self) -> None:
        import os

        from anima_server.services.crypto import decrypt_blob, encrypt_blob

        dek = os.urandom(32)
        plaintext = b"Hello, this is a transcript.\nLine two."
        aad = b"transcript:42:2026-03-28"

        ciphertext = encrypt_blob(plaintext, dek, aad=aad)
        assert ciphertext != plaintext
        assert len(ciphertext) == 12 + len(plaintext) + 16

        recovered = decrypt_blob(ciphertext, dek, aad=aad)
        assert recovered == plaintext

    def test_decrypt_wrong_key_fails(self) -> None:
        import os

        from anima_server.services.crypto import decrypt_blob, encrypt_blob

        dek = os.urandom(32)
        wrong_key = os.urandom(32)
        plaintext = b"Secret data"
        aad = b"test:1:2026"

        ciphertext = encrypt_blob(plaintext, dek, aad=aad)

        with pytest.raises(InvalidTag):
            decrypt_blob(ciphertext, wrong_key, aad=aad)

    def test_decrypt_wrong_aad_fails(self) -> None:
        import os

        from anima_server.services.crypto import decrypt_blob, encrypt_blob

        dek = os.urandom(32)
        plaintext = b"Secret data"

        ciphertext = encrypt_blob(plaintext, dek, aad=b"correct-aad")

        with pytest.raises(InvalidTag):
            decrypt_blob(ciphertext, dek, aad=b"wrong-aad")

    def test_empty_plaintext(self) -> None:
        import os

        from anima_server.services.crypto import decrypt_blob, encrypt_blob

        dek = os.urandom(32)
        ciphertext = encrypt_blob(b"", dek, aad=b"test")
        recovered = decrypt_blob(ciphertext, dek, aad=b"test")
        assert recovered == b""


class TestTranscriptExport:
    def test_serialize_messages_to_jsonl(self) -> None:
        from anima_server.services.agent.transcript_archive import serialize_messages_to_jsonl

        messages = [
            {"role": "user", "content": "Hello",
                "ts": "2026-03-28T10:00:00Z", "seq": 1},
            {"role": "assistant", "content": "Hi!",
                "ts": "2026-03-28T10:00:05Z", "seq": 2},
        ]
        result = serialize_messages_to_jsonl(messages)
        lines = result.strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["role"] == "user"
        assert json.loads(lines[1])["content"] == "Hi!"

    def test_export_creates_encrypted_file(
        self, transcripts_dir: Path, test_dek: bytes
    ) -> None:
        from anima_server.services.agent.transcript_archive import export_transcript

        messages = [
            {"role": "user", "content": "Hello",
                "ts": "2026-03-28T10:00:00Z", "seq": 1},
        ]
        result = export_transcript(
            messages=messages,
            thread_id=42,
            user_id=1,
            dek=test_dek,
            transcripts_dir=transcripts_dir,
        )

        assert result.enc_path.exists()
        assert result.enc_path.suffix == ".enc"
        assert result.enc_path.stat().st_size > 0

    def test_export_creates_sidecar(self, transcripts_dir: Path, test_dek: bytes) -> None:
        from anima_server.services.agent.transcript_archive import export_transcript

        messages = [
            {
                "role": "user",
                "content": "Tell me about project deadlines",
                "ts": "2026-03-28T10:00:00Z",
                "seq": 1,
            },
            {
                "role": "assistant",
                "content": "The deadline is April 15",
                "ts": "2026-03-28T10:00:05Z",
                "seq": 2,
            },
        ]
        result = export_transcript(
            messages=messages,
            thread_id=42,
            user_id=1,
            dek=test_dek,
            transcripts_dir=transcripts_dir,
        )

        assert result.meta_path.exists()
        meta = json.loads(result.meta_path.read_text(encoding="utf-8"))
        assert meta["version"] == 1
        assert meta["thread_id"] == 42
        assert meta["user_id"] == 1
        assert meta["message_count"] == 2
        assert isinstance(meta["keywords"], list)
        assert len(meta["keywords"]) <= 10

    def test_export_decrypt_roundtrip(self, transcripts_dir: Path, test_dek: bytes) -> None:
        from anima_server.services.agent.transcript_archive import (
            decrypt_transcript,
            export_transcript,
        )

        messages = [
            {"role": "user", "content": "Hello",
                "ts": "2026-03-28T10:00:00Z", "seq": 1},
        ]
        result = export_transcript(
            messages=messages,
            thread_id=42,
            user_id=1,
            dek=test_dek,
            transcripts_dir=transcripts_dir,
        )

        recovered = decrypt_transcript(
            result.enc_path, dek=test_dek, thread_id=42)
        assert len(recovered) == 1
        assert recovered[0]["content"] == "Hello"

    def test_export_without_dek_creates_plaintext_file(self, transcripts_dir: Path) -> None:
        from anima_server.services.agent.transcript_archive import (
            decrypt_transcript,
            export_transcript,
        )

        messages = [
            {"role": "user", "content": "Hello",
                "ts": "2026-03-28T10:00:00Z", "seq": 1},
        ]
        result = export_transcript(
            messages=messages,
            thread_id=42,
            user_id=1,
            dek=None,
            transcripts_dir=transcripts_dir,
        )

        assert result.enc_path.exists()
        assert result.enc_path.suffix == ".jsonl"

        recovered = decrypt_transcript(result.enc_path, dek=None, thread_id=42)
        assert recovered[0]["content"] == "Hello"

    def test_export_atomic_no_partial_on_error(
        self, transcripts_dir: Path, test_dek: bytes
    ) -> None:
        from anima_server.services.agent.transcript_archive import export_transcript

        enc_files_before = list(transcripts_dir.glob("*.enc"))
        assert len(enc_files_before) == 0

        result = export_transcript(
            messages=[],
            thread_id=99,
            user_id=1,
            dek=test_dek,
            transcripts_dir=transcripts_dir,
        )
        assert result.enc_path.exists()

    def test_export_excludes_nothing_extra(self) -> None:
        from anima_server.services.agent.transcript_archive import serialize_messages_to_jsonl

        messages = [
            {
                "role": "assistant",
                "content": "Response",
                "thinking": "inner thought",
                "ts": "2026-03-28T10:00:05Z",
                "seq": 1,
            },
        ]
        result = serialize_messages_to_jsonl(messages)
        parsed = json.loads(result.strip())
        assert parsed["thinking"] == "inner thought"

    def test_send_message_tool_results_export_as_assistant(self) -> None:
        from datetime import UTC, datetime

        from anima_server.models.runtime import RuntimeMessage
        from anima_server.services.agent.transcript_archive import messages_to_transcript_dicts

        messages = [
            RuntimeMessage(
                thread_id=1,
                user_id=1,
                sequence_id=1,
                role="tool",
                tool_name="send_message",
                tool_call_id="call-1",
                content_text="Visible assistant reply",
                created_at=datetime.now(UTC),
            )
        ]

        exported = messages_to_transcript_dicts(messages)
        assert exported[0]["role"] == "assistant"
        assert exported[0]["content"] == "Visible assistant reply"

    def test_assistant_tool_call_thinking_is_hidden_from_visible_content(self) -> None:
        from datetime import UTC, datetime

        from anima_server.models.runtime import RuntimeMessage
        from anima_server.services.agent.transcript_archive import messages_to_transcript_dicts

        messages = [
            RuntimeMessage(
                thread_id=1,
                user_id=1,
                sequence_id=1,
                role="assistant",
                content_text="private reasoning",
                content_json={
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "name": "send_message",
                            "arguments": {"content": "Visible reply"},
                        }
                    ]
                },
                created_at=datetime.now(UTC),
            )
        ]

        exported = messages_to_transcript_dicts(messages)
        assert exported[0]["thinking"] == "private reasoning"
        assert exported[0]["content"] == ""

    def test_export_updates_rust_transcript_index(
        self,
        monkeypatch: pytest.MonkeyPatch,
        transcripts_dir: Path,
        test_dek: bytes,
    ) -> None:
        from anima_server.services.agent.transcript_archive import export_transcript

        upserts: list[dict[str, object]] = []
        monkeypatch.setattr(
            retrieval_module,
            "transcript_index_upsert",
            lambda **kwargs: upserts.append(kwargs),
        )

        result = export_transcript(
            messages=[
                {
                    "role": "user",
                    "content": "Tell me about project deadlines",
                    "ts": "2026-03-28T10:00:00Z",
                    "seq": 1,
                },
                {
                    "role": "assistant",
                    "content": "The deadline is April 15",
                    "ts": "2026-03-28T10:00:05Z",
                    "seq": 2,
                },
            ],
            thread_id=42,
            user_id=1,
            dek=test_dek,
            transcripts_dir=transcripts_dir,
            summary="Project deadline discussion",
        )

        assert len(upserts) == 1
        assert upserts[0]["thread_id"] == 42
        assert upserts[0]["transcript_ref"] == result.enc_path.name
        assert upserts[0]["summary"] == "Project deadline discussion"

    def test_rebuild_transcript_index_restores_searchability(
        self,
        monkeypatch: pytest.MonkeyPatch,
        transcripts_dir: Path,
        test_dek: bytes,
    ) -> None:
        from anima_server.services.agent.transcript_archive import (
            export_transcript,
            rebuild_transcript_index,
        )

        index_root = transcripts_dir.parent / "indices"
        monkeypatch.setattr(retrieval_module, "get_retrieval_root", lambda: index_root)

        result = export_transcript(
            messages=[
                {
                    "role": "user",
                    "content": "Tell me about project deadlines",
                    "ts": "2026-03-28T10:00:00Z",
                    "seq": 1,
                },
                {
                    "role": "assistant",
                    "content": "The deadline is April 15",
                    "ts": "2026-03-28T10:00:05Z",
                    "seq": 2,
                },
            ],
            thread_id=42,
            user_id=1,
            dek=test_dek,
            transcripts_dir=transcripts_dir,
        )
        retrieval_module.transcript_index_delete(
            root=index_root,
            thread_id=42,
            user_id=1,
        )

        rebuilt = rebuild_transcript_index(
            user_id=1,
            dek=test_dek,
            transcripts_dir=transcripts_dir,
            root=index_root,
        )
        hits = retrieval_module.transcript_index_search(
            root=index_root,
            user_id=1,
            query="deadline",
            limit=5,
        )

        assert rebuilt == 1
        assert hits[0]["transcript_ref"] == result.enc_path.name

    def test_rebuild_transcript_index_clears_dirty_manifest(
        self,
        monkeypatch: pytest.MonkeyPatch,
        transcripts_dir: Path,
        test_dek: bytes,
    ) -> None:
        from anima_server.services.agent.transcript_archive import (
            export_transcript,
            rebuild_transcript_index,
        )

        index_root = transcripts_dir.parent / "indices"
        monkeypatch.setattr(retrieval_module, "get_retrieval_root", lambda: index_root)

        export_transcript(
            messages=[
                {
                    "role": "user",
                    "content": "Tell me about project deadlines",
                    "ts": "2026-03-28T10:00:00Z",
                    "seq": 1,
                },
                {
                    "role": "assistant",
                    "content": "The deadline is April 15",
                    "ts": "2026-03-28T10:00:05Z",
                    "seq": 2,
                },
            ],
            thread_id=42,
            user_id=1,
            dek=test_dek,
            transcripts_dir=transcripts_dir,
        )
        retrieval_module.mark_retrieval_index_dirty(root=index_root, family="transcript")

        rebuilt = rebuild_transcript_index(
            user_id=1,
            dek=test_dek,
            transcripts_dir=transcripts_dir,
            root=index_root,
        )

        assert rebuilt == 1
        assert retrieval_module.is_retrieval_family_dirty(root=index_root, family="transcript") is False

    def test_rebuild_transcript_index_preserves_other_users_entries(
        self,
        monkeypatch: pytest.MonkeyPatch,
        transcripts_dir: Path,
    ) -> None:
        from anima_server.services.agent.transcript_archive import (
            export_transcript,
            rebuild_transcript_index,
        )

        index_root = transcripts_dir.parent / "indices"
        monkeypatch.setattr(retrieval_module, "get_retrieval_root", lambda: index_root)

        export_transcript(
            messages=[
                {
                    "role": "user",
                    "content": "User one asked about deadlines",
                    "ts": "2026-03-28T10:00:00Z",
                    "seq": 1,
                }
            ],
            thread_id=42,
            user_id=1,
            dek=None,
            transcripts_dir=transcripts_dir,
        )
        other_result = export_transcript(
            messages=[
                {
                    "role": "user",
                    "content": "User two asked about bakery orders",
                    "ts": "2026-03-28T11:00:00Z",
                    "seq": 1,
                }
            ],
            thread_id=77,
            user_id=2,
            dek=None,
            transcripts_dir=transcripts_dir,
        )
        retrieval_module.mark_retrieval_index_dirty(root=index_root, family="transcript")

        rebuilt = rebuild_transcript_index(
            user_id=1,
            dek=None,
            transcripts_dir=transcripts_dir,
            root=index_root,
        )
        other_hits = retrieval_module.transcript_index_search(
            root=index_root,
            user_id=2,
            query="bakery",
            limit=5,
        )

        assert rebuilt == 1
        assert [hit["transcript_ref"] for hit in other_hits] == [other_result.enc_path.name]


class TestTranscriptSearch:
    def _create_test_transcript(
        self,
        transcripts_dir: Path,
        test_dek: bytes,
        *,
        thread_id: int,
        messages: list[dict[str, object]],
    ) -> None:
        from anima_server.services.agent.transcript_archive import export_transcript

        export_transcript(
            messages=messages,
            thread_id=thread_id,
            user_id=1,
            dek=test_dek,
            transcripts_dir=transcripts_dir,
        )

    def test_search_finds_matching_transcript(
        self,
        transcripts_dir: Path,
        test_dek: bytes,
    ) -> None:
        from anima_server.services.agent.transcript_search import search_transcripts

        self._create_test_transcript(
            transcripts_dir,
            test_dek,
            thread_id=42,
            messages=[
                {
                    "role": "user",
                    "content": "Tell me about quantum physics",
                    "ts": "2026-03-28T10:00:00Z",
                    "seq": 1,
                },
                {
                    "role": "assistant",
                    "content": "Quantum physics is fascinating",
                    "ts": "2026-03-28T10:00:05Z",
                    "seq": 2,
                },
            ],
        )

        snippets = search_transcripts(
            query="quantum physics",
            user_id=1,
            dek=test_dek,
            transcripts_dir=transcripts_dir,
            days_back=30,
        )

        assert len(snippets) > 0
        assert any("quantum" in snippet.text.lower() for snippet in snippets)

    def test_search_returns_empty_for_no_match(
        self,
        transcripts_dir: Path,
        test_dek: bytes,
    ) -> None:
        from anima_server.services.agent.transcript_search import search_transcripts

        self._create_test_transcript(
            transcripts_dir,
            test_dek,
            thread_id=42,
            messages=[
                {
                    "role": "user",
                    "content": "Talk about cooking",
                    "ts": "2026-03-28T10:00:00Z",
                    "seq": 1,
                },
            ],
        )

        snippets = search_transcripts(
            query="quantum physics",
            user_id=1,
            dek=test_dek,
            transcripts_dir=transcripts_dir,
            days_back=30,
        )

        assert snippets == []

    def test_search_uses_rust_index_when_available(
        self,
        monkeypatch: pytest.MonkeyPatch,
        transcripts_dir: Path,
        test_dek: bytes,
    ) -> None:
        from anima_server.services.agent.transcript_archive import export_transcript
        from anima_server.services.agent.transcript_search import search_transcripts

        search_calls: list[dict[str, object]] = []
        result = export_transcript(
            messages=[
                {
                    "role": "user",
                    "content": "Tell me about quantum physics",
                    "ts": "2026-03-28T10:00:00Z",
                    "seq": 1,
                },
                {
                    "role": "assistant",
                    "content": "Quantum physics is fascinating",
                    "ts": "2026-03-28T10:00:05Z",
                    "seq": 2,
                },
            ],
            thread_id=42,
            user_id=1,
            dek=test_dek,
            transcripts_dir=transcripts_dir,
        )

        monkeypatch.setattr(
            retrieval_module,
            "transcript_index_search",
            lambda **kwargs: search_calls.append(kwargs)
            or [
                {
                    "thread_id": 42,
                    "transcript_ref": result.enc_path.name,
                    "date_start": 1_774_694_400,
                    "score": 2.5,
                }
            ],
        )

        snippets = search_transcripts(
            query="quantum physics",
            user_id=1,
            dek=test_dek,
            transcripts_dir=transcripts_dir,
            days_back=30,
        )

        assert len(search_calls) == 1
        assert len(snippets) > 0
        assert "quantum" in snippets[0].text.lower()

    def test_search_filters_stale_rust_index_hits_by_days_back(
        self,
        monkeypatch: pytest.MonkeyPatch,
        transcripts_dir: Path,
        test_dek: bytes,
    ) -> None:
        from datetime import UTC, datetime, timedelta

        from anima_server.services.agent.transcript_archive import export_transcript
        from anima_server.services.agent.transcript_search import search_transcripts

        old_timestamp = datetime.now(UTC) - timedelta(days=90)
        result = export_transcript(
            messages=[
                {
                    "role": "user",
                    "content": "Tell me about old quantum physics notes",
                    "ts": old_timestamp.isoformat().replace("+00:00", "Z"),
                    "seq": 1,
                },
                {
                    "role": "assistant",
                    "content": "Old quantum physics notes should be outside the search window",
                    "ts": (old_timestamp + timedelta(seconds=5))
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "seq": 2,
                },
            ],
            thread_id=42,
            user_id=1,
            dek=test_dek,
            transcripts_dir=transcripts_dir,
        )

        search_calls: list[dict[str, object]] = []
        monkeypatch.setattr(
            retrieval_module,
            "transcript_index_search",
            lambda **kwargs: search_calls.append(kwargs)
            or [
                {
                    "thread_id": 42,
                    "transcript_ref": result.enc_path.name,
                    "date_start": int(old_timestamp.timestamp()),
                    "score": 2.5,
                }
            ],
        )

        snippets = search_transcripts(
            query="quantum physics",
            user_id=1,
            dek=test_dek,
            transcripts_dir=transcripts_dir,
            days_back=30,
        )

        assert len(search_calls) == 1
        assert snippets == []

    def test_search_falls_back_when_rust_index_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
        transcripts_dir: Path,
        test_dek: bytes,
    ) -> None:
        from anima_server.services.agent.transcript_search import search_transcripts

        self._create_test_transcript(
            transcripts_dir,
            test_dek,
            thread_id=42,
            messages=[
                {
                    "role": "user",
                    "content": "Tell me about quantum physics",
                    "ts": "2026-03-28T10:00:00Z",
                    "seq": 1,
                },
                {
                    "role": "assistant",
                    "content": "Quantum physics is fascinating",
                    "ts": "2026-03-28T10:00:05Z",
                    "seq": 2,
                },
            ],
        )

        search_calls: list[dict[str, object]] = []

        def _failing_search(**kwargs):
            search_calls.append(kwargs)
            raise RuntimeError("transcript index unavailable")

        monkeypatch.setattr(retrieval_module, "transcript_index_search", _failing_search)

        snippets = search_transcripts(
            query="quantum physics",
            user_id=1,
            dek=test_dek,
            transcripts_dir=transcripts_dir,
            days_back=30,
        )

        assert len(search_calls) == 1
        assert len(snippets) > 0
        assert any("quantum" in snippet.text.lower() for snippet in snippets)

    def test_search_falls_back_when_rust_index_returns_no_candidates(
        self,
        monkeypatch: pytest.MonkeyPatch,
        transcripts_dir: Path,
        test_dek: bytes,
    ) -> None:
        from anima_server.services.agent.transcript_search import search_transcripts

        self._create_test_transcript(
            transcripts_dir,
            test_dek,
            thread_id=42,
            messages=[
                {
                    "role": "user",
                    "content": "Tell me about quantum physics",
                    "ts": "2026-03-28T10:00:00Z",
                    "seq": 1,
                },
                {
                    "role": "assistant",
                    "content": "Quantum physics is fascinating",
                    "ts": "2026-03-28T10:00:05Z",
                    "seq": 2,
                },
            ],
        )

        search_calls: list[dict[str, object]] = []
        monkeypatch.setattr(
            retrieval_module,
            "transcript_index_search",
            lambda **kwargs: search_calls.append(kwargs) or [],
        )

        snippets = search_transcripts(
            query="quantum physics",
            user_id=1,
            dek=test_dek,
            transcripts_dir=transcripts_dir,
            days_back=30,
        )

        assert len(search_calls) == 1
        assert len(snippets) > 0
        assert any("quantum" in snippet.text.lower() for snippet in snippets)

    def test_search_rebuilds_dirty_transcript_index(
        self,
        monkeypatch: pytest.MonkeyPatch,
        transcripts_dir: Path,
        test_dek: bytes,
    ) -> None:
        from anima_server.services.agent.transcript_search import search_transcripts

        self._create_test_transcript(
            transcripts_dir,
            test_dek,
            thread_id=42,
            messages=[
                {
                    "role": "user",
                    "content": "Tell me about quantum physics",
                    "ts": "2026-03-28T10:00:00Z",
                    "seq": 1,
                },
                {
                    "role": "assistant",
                    "content": "Quantum physics is fascinating",
                    "ts": "2026-03-28T10:00:05Z",
                    "seq": 2,
                },
            ],
        )

        rebuild_calls: list[dict[str, object]] = []
        monkeypatch.setattr(
            retrieval_module,
            "retrieval_manifest_status",
            lambda **kwargs: {
                "exists": True,
                "version": 1,
                "families": {
                    "memory": {"generation": 0, "dirty": False},
                    "transcript": {"generation": 0, "dirty": True},
                },
            },
        )
        monkeypatch.setattr(
            "anima_server.services.agent.transcript_archive.rebuild_transcript_index",
            lambda **kwargs: rebuild_calls.append(kwargs) or 0,
        )

        snippets = search_transcripts(
            query="quantum physics",
            user_id=1,
            dek=test_dek,
            transcripts_dir=transcripts_dir,
            days_back=30,
        )

        assert len(rebuild_calls) == 1
        assert len(snippets) > 0
        assert any("quantum" in snippet.text.lower() for snippet in snippets)

    def test_search_recovers_from_corrupt_manifest(
        self,
        monkeypatch: pytest.MonkeyPatch,
        transcripts_dir: Path,
        test_dek: bytes,
    ) -> None:
        from anima_server.services.agent.transcript_search import search_transcripts

        index_root = transcripts_dir.parent / "indices"
        monkeypatch.setattr(retrieval_module, "get_retrieval_root", lambda: index_root)

        self._create_test_transcript(
            transcripts_dir,
            test_dek,
            thread_id=42,
            messages=[
                {
                    "role": "user",
                    "content": "Tell me about coffee beans",
                    "ts": "2026-03-28T10:00:00Z",
                    "seq": 1,
                },
                {
                    "role": "assistant",
                    "content": "Coffee beans can be roasted lightly",
                    "ts": "2026-03-28T10:00:05Z",
                    "seq": 2,
                },
            ],
        )
        (index_root / "manifest.json").write_text("{ invalid", encoding="utf-8")
        self._create_test_transcript(
            transcripts_dir,
            test_dek,
            thread_id=43,
            messages=[
                {
                    "role": "user",
                    "content": "Coffee from the tea shop tastes great",
                    "ts": "2026-03-28T11:00:00Z",
                    "seq": 1,
                },
                {
                    "role": "assistant",
                    "content": "The tea shop coffee has floral notes",
                    "ts": "2026-03-28T11:00:05Z",
                    "seq": 2,
                },
            ],
        )

        snippets = search_transcripts(
            query="coffee",
            user_id=1,
            dek=test_dek,
            transcripts_dir=transcripts_dir,
            days_back=30,
        )

        assert len(snippets) > 0
        assert any(snippet.thread_id == 43 for snippet in snippets)

    def test_search_falls_back_once_after_rust_candidate_decrypt_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        transcripts_dir: Path,
        test_dek: bytes,
    ) -> None:
        from anima_server.services.agent import transcript_search
        from anima_server.services.agent.transcript_search import search_transcripts

        bad_path = transcripts_dir / "bad.jsonl.enc"
        bad_path.write_bytes(b"not a valid encrypted transcript")
        root = transcripts_dir.parent / "indices"
        monkeypatch.setattr(retrieval_module, "get_retrieval_root", lambda: root)
        monkeypatch.setattr(transcript_search, "_transcript_index_is_dirty", lambda _root: False)

        rust_calls = 0

        def _rust_candidates(**_kwargs: object) -> list[tuple[Path, int, str]]:
            nonlocal rust_calls
            rust_calls += 1
            if rust_calls > 1:
                raise AssertionError("Rust transcript candidates were retried recursively")
            return [(bad_path, 42, "2026-03-28")]

        sidecar_calls: list[dict[str, object]] = []
        monkeypatch.setattr(
            transcript_search,
            "_candidate_transcripts_from_rust_index",
            _rust_candidates,
        )
        monkeypatch.setattr(
            transcript_search,
            "_candidate_transcripts_from_sidecars",
            lambda **kwargs: sidecar_calls.append(kwargs) or [],
        )

        snippets = search_transcripts(
            query="coffee",
            user_id=1,
            dek=test_dek,
            transcripts_dir=transcripts_dir,
            days_back=30,
        )

        assert snippets == []
        assert rust_calls == 1
        assert len(sidecar_calls) == 1

    def test_search_respects_budget(
        self,
        transcripts_dir: Path,
        test_dek: bytes,
    ) -> None:
        from anima_server.services.agent.transcript_search import search_transcripts

        messages = [
            {
                "role": "user",
                "content": f"Message about topic {i} with relevant keywords",
                "ts": f"2026-03-28T10:{i:02d}:00Z",
                "seq": i,
            }
            for i in range(50)
        ]
        self._create_test_transcript(
            transcripts_dir,
            test_dek,
            thread_id=42,
            messages=messages,
        )

        snippets = search_transcripts(
            query="topic relevant keywords",
            user_id=1,
            dek=test_dek,
            transcripts_dir=transcripts_dir,
            budget_chars=200,
        )

        total_chars = sum(len(snippet.text) for snippet in snippets)
        assert total_chars <= 300

    def test_search_no_transcripts(
        self,
        transcripts_dir: Path,
        test_dek: bytes,
    ) -> None:
        from anima_server.services.agent.transcript_search import search_transcripts

        snippets = search_transcripts(
            query="anything",
            user_id=1,
            dek=test_dek,
            transcripts_dir=transcripts_dir,
        )

        assert snippets == []

    def test_search_finds_plaintext_transcript_without_dek(
        self,
        transcripts_dir: Path,
    ) -> None:
        from anima_server.services.agent.transcript_archive import export_transcript
        from anima_server.services.agent.transcript_search import search_transcripts

        export_transcript(
            messages=[
                {
                    "role": "user",
                    "content": "Please remember the bakery order",
                    "ts": "2026-03-28T10:00:00Z",
                    "seq": 1,
                },
                {
                    "role": "assistant",
                    "content": "The bakery order is for rye bread",
                    "ts": "2026-03-28T10:00:05Z",
                    "seq": 2,
                },
            ],
            thread_id=77,
            user_id=1,
            dek=None,
            transcripts_dir=transcripts_dir,
        )

        snippets = search_transcripts(
            query="bakery order",
            user_id=1,
            dek=None,
            transcripts_dir=transcripts_dir,
        )

        assert len(snippets) > 0
        assert any("bakery" in snippet.text.lower() for snippet in snippets)

    def test_format_snippets_mentions_omitted_matches(
        self,
        transcripts_dir: Path,
        test_dek: bytes,
    ) -> None:
        from anima_server.services.agent.transcript_search import (
            format_snippets,
            search_transcripts,
        )

        self._create_test_transcript(
            transcripts_dir,
            test_dek,
            thread_id=42,
            messages=[
                {
                    "role": "user",
                    "content": f"keyword match number {i} with extra filler text",
                    "ts": f"2026-03-28T10:{i:02d}:00Z",
                    "seq": i,
                }
                for i in range(1, 7)
            ],
        )

        snippets = search_transcripts(
            query="keyword match",
            user_id=1,
            dek=test_dek,
            transcripts_dir=transcripts_dir,
            max_snippets=2,
            snippet_context=0,
            budget_chars=120,
        )

        formatted = format_snippets(snippets)
        assert "more matches found" in formatted


class TestRecallTranscriptTool:
    def test_tool_in_extension_tools(self) -> None:
        from anima_server.services.agent.tools import get_extension_tools

        tools = get_extension_tools()
        tool_names = [tool.name for tool in tools]
        assert "recall_transcript" in tool_names

    def test_tool_has_correct_params(self) -> None:
        from anima_server.services.agent.tools import get_extension_tools

        tools = get_extension_tools()
        tool = next(tool for tool in tools if tool.name == "recall_transcript")
        schema = tool.args_schema.model_json_schema()

        assert "query" in schema["properties"]
        assert "days_back" in schema["properties"]

    def test_invalid_days_back_defaults_to_30(self, managed_tmp_path: Path) -> None:
        from unittest.mock import patch

        from anima_server.services.agent.tools import recall_transcript

        with (
            patch(
                "anima_server.services.agent.tool_context.get_tool_context",
                return_value=SimpleNamespace(user_id=1),
            ),
            patch(
                "anima_server.services.data_crypto.get_active_dek",
                return_value=None,
            ),
            patch(
                "anima_server.services.agent.transcript_search.search_transcripts",
                return_value=[],
            ) as mock_search,
            patch("anima_server.config.settings") as mock_settings,
        ):
            mock_settings.data_dir = managed_tmp_path
            recall_transcript("bakery order", days_back="invalid")

        assert mock_search.call_args.kwargs["days_back"] == 30


class TestThreadCloseEndpoint:
    def test_close_thread_returns_true(self, runtime_db: Session) -> None:
        from anima_server.models.runtime import RuntimeThread
        from anima_server.services.agent.persistence import close_thread, get_or_create_thread

        thread = get_or_create_thread(runtime_db, user_id=1)
        runtime_db.commit()

        result = close_thread(runtime_db, thread_id=thread.id)
        assert result is True

        closed = runtime_db.get(RuntimeThread, thread.id)
        assert closed is not None
        assert closed.status == "closed"

    def test_close_already_closed_thread(self, runtime_db: Session) -> None:
        from anima_server.services.agent.persistence import close_thread, get_or_create_thread

        thread = get_or_create_thread(runtime_db, user_id=1)
        close_thread(runtime_db, thread_id=thread.id)
        runtime_db.commit()

        result = close_thread(runtime_db, thread_id=thread.id)
        assert result is False


class TestEagerConsolidation:
    def test_on_thread_close_exports_transcript(
        self,
        runtime_db: Session,
        transcripts_dir: Path,
        test_dek: bytes,
    ) -> None:
        import asyncio
        from unittest.mock import AsyncMock, patch

        from anima_server.models.runtime import RuntimeMessage
        from anima_server.services.agent.eager_consolidation import on_thread_close
        from anima_server.services.agent.persistence import get_or_create_thread

        thread = get_or_create_thread(runtime_db, user_id=1)
        runtime_db.add(
            RuntimeMessage(
                thread_id=thread.id,
                user_id=1,
                sequence_id=1,
                role="user",
                content_text="Hello world",
            )
        )
        runtime_db.flush()

        with (
            patch(
                "anima_server.services.agent.eager_consolidation.run_soul_writer",
                new_callable=AsyncMock,
            ),
            patch(
                "anima_server.services.agent.eager_consolidation.maybe_generate_episode",
                new_callable=AsyncMock,
            ),
            patch(
                "anima_server.services.agent.eager_consolidation.get_active_dek",
                return_value=test_dek,
            ),
            patch(
                "anima_server.services.agent.eager_consolidation._get_transcripts_dir",
                return_value=transcripts_dir,
            ),
        ):
            asyncio.run(
                on_thread_close(
                    thread_id=thread.id,
                    user_id=1,
                    runtime_db_factory=lambda: runtime_db,
                )
            )

        enc_files = list(transcripts_dir.glob("*.jsonl.enc"))
        assert len(enc_files) == 1

    def test_on_thread_close_marks_archived(
        self,
        runtime_db: Session,
        transcripts_dir: Path,
        test_dek: bytes,
    ) -> None:
        import asyncio
        from unittest.mock import AsyncMock, patch

        from anima_server.models.runtime import RuntimeMessage, RuntimeThread
        from anima_server.services.agent.eager_consolidation import on_thread_close
        from anima_server.services.agent.persistence import get_or_create_thread

        thread = get_or_create_thread(runtime_db, user_id=1)
        runtime_db.add(
            RuntimeMessage(
                thread_id=thread.id,
                user_id=1,
                sequence_id=1,
                role="user",
                content_text="Test message",
            )
        )
        runtime_db.flush()

        with (
            patch(
                "anima_server.services.agent.eager_consolidation.run_soul_writer",
                new_callable=AsyncMock,
            ),
            patch(
                "anima_server.services.agent.eager_consolidation.maybe_generate_episode",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "anima_server.services.agent.eager_consolidation.get_active_dek",
                return_value=test_dek,
            ),
            patch(
                "anima_server.services.agent.eager_consolidation._get_transcripts_dir",
                return_value=transcripts_dir,
            ),
        ):
            asyncio.run(
                on_thread_close(
                    thread_id=thread.id,
                    user_id=1,
                    runtime_db_factory=lambda: runtime_db,
                )
            )

        refreshed = runtime_db.get(RuntimeThread, thread.id)
        assert refreshed is not None
        assert refreshed.is_archived is True

    def test_on_thread_close_links_episode_to_transcript_sidecar(
        self,
        runtime_db: Session,
        soul_db: Session,
        transcripts_dir: Path,
        test_dek: bytes,
    ) -> None:
        import asyncio
        from unittest.mock import AsyncMock, patch

        from anima_server.models.agent_runtime import MemoryEpisode
        from anima_server.models.runtime import RuntimeMessage
        from anima_server.services.agent.eager_consolidation import on_thread_close
        from anima_server.services.agent.persistence import get_or_create_thread

        thread = get_or_create_thread(runtime_db, user_id=1)
        runtime_db.add(
            RuntimeMessage(
                thread_id=thread.id,
                user_id=1,
                sequence_id=1,
                role="user",
                content_text="Let's keep this archived",
            )
        )
        runtime_db.commit()

        episode = MemoryEpisode(
            user_id=1,
            thread_id=thread.id,
            date="2026-03-28",
            summary="Episode summary from consolidation",
        )
        soul_db.add(episode)
        soul_db.commit()

        with (
            patch(
                "anima_server.services.agent.eager_consolidation.run_soul_writer",
                new_callable=AsyncMock,
            ),
            patch(
                "anima_server.services.agent.eager_consolidation.maybe_generate_episode",
                new_callable=AsyncMock,
                return_value=episode,
            ),
            patch(
                "anima_server.services.agent.eager_consolidation.get_active_dek",
                return_value=test_dek,
            ),
            patch(
                "anima_server.services.agent.eager_consolidation._get_transcripts_dir",
                return_value=transcripts_dir,
            ),
        ):
            asyncio.run(
                on_thread_close(
                    thread_id=thread.id,
                    user_id=1,
                    runtime_db_factory=lambda: runtime_db,
                    soul_db_factory=lambda: soul_db,
                )
            )

        meta_path = next(transcripts_dir.glob("*.meta.json"))
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

        refreshed_episode = soul_db.get(MemoryEpisode, episode.id)
        assert refreshed_episode is not None
        assert refreshed_episode.transcript_ref == next(
            transcripts_dir.glob("*.jsonl.enc")).name
        assert meta["episodic_memory_ids"] == [str(episode.id)]
        assert meta["summary"] == "Episode summary from consolidation"


class TestBackgroundSweeps:
    def test_inactivity_sweep_closes_stale_threads(self, runtime_db: Session) -> None:
        import asyncio
        from datetime import UTC, datetime, timedelta
        from unittest.mock import AsyncMock, patch

        from anima_server.models.runtime import RuntimeThread
        from anima_server.services.agent.eager_consolidation import inactivity_sweep

        thread = RuntimeThread(
            user_id=1,
            status="active",
            last_message_at=datetime.now(UTC) - timedelta(minutes=10),
        )
        runtime_db.add(thread)
        runtime_db.commit()

        with patch(
            "anima_server.services.agent.eager_consolidation.on_thread_close",
            new_callable=AsyncMock,
        ):
            count = asyncio.run(
                inactivity_sweep(
                    runtime_db_factory=lambda: runtime_db,
                    inactivity_minutes=5,
                )
            )

        assert count == 1
        refreshed = runtime_db.get(RuntimeThread, thread.id)
        assert refreshed is not None
        assert refreshed.status == "closed"

    def test_inactivity_sweep_skips_recent_threads(self, runtime_db: Session) -> None:
        import asyncio
        from datetime import UTC, datetime
        from unittest.mock import AsyncMock, patch

        from anima_server.models.runtime import RuntimeThread
        from anima_server.services.agent.eager_consolidation import inactivity_sweep

        thread = RuntimeThread(
            user_id=1,
            status="active",
            last_message_at=datetime.now(UTC),
        )
        runtime_db.add(thread)
        runtime_db.commit()

        with patch(
            "anima_server.services.agent.eager_consolidation.on_thread_close",
            new_callable=AsyncMock,
        ):
            count = asyncio.run(
                inactivity_sweep(
                    runtime_db_factory=lambda: runtime_db,
                    inactivity_minutes=5,
                )
            )

        assert count == 0

    def test_inactivity_sweep_retries_closed_unarchived_threads(
        self, runtime_db: Session
    ) -> None:
        import asyncio
        from unittest.mock import AsyncMock, patch

        from anima_server.models.runtime import RuntimeThread
        from anima_server.services.agent.eager_consolidation import inactivity_sweep

        thread = RuntimeThread(user_id=1, status="closed", is_archived=False)
        runtime_db.add(thread)
        runtime_db.commit()

        def runtime_db_factory() -> Session:
            return runtime_db

        with patch(
            "anima_server.services.agent.eager_consolidation.on_thread_close",
            new_callable=AsyncMock,
        ) as mock_on_thread_close:
            count = asyncio.run(
                inactivity_sweep(
                    runtime_db_factory=runtime_db_factory,
                    inactivity_minutes=5,
                )
            )

        assert count == 0
        assert mock_on_thread_close.await_count == 1
        assert mock_on_thread_close.await_args.kwargs["thread_id"] == thread.id
        assert mock_on_thread_close.await_args.kwargs["user_id"] == thread.user_id
        assert mock_on_thread_close.await_args.kwargs["runtime_db_factory"] is runtime_db_factory

    def test_prune_only_archived_messages(self, runtime_db: Session) -> None:
        import asyncio
        from datetime import UTC, datetime, timedelta
        from unittest.mock import patch

        from anima_server.models.runtime import RuntimeMessage, RuntimeThread
        from anima_server.services.agent.eager_consolidation import prune_expired_messages

        archived = RuntimeThread(user_id=1, status="closed", is_archived=True)
        runtime_db.add(archived)
        runtime_db.flush()
        runtime_db.add(
            RuntimeMessage(
                thread_id=archived.id,
                user_id=1,
                sequence_id=1,
                role="user",
                content_text="Old message",
                created_at=datetime.now(UTC) - timedelta(days=60),
            )
        )

        active = RuntimeThread(user_id=2, status="active")
        runtime_db.add(active)
        runtime_db.flush()
        runtime_db.add(
            RuntimeMessage(
                thread_id=active.id,
                user_id=2,
                sequence_id=1,
                role="user",
                content_text="Also old but active",
                created_at=datetime.now(UTC) - timedelta(days=60),
            )
        )
        runtime_db.commit()

        with patch("anima_server.services.agent.eager_consolidation.settings") as mock_settings:
            mock_settings.message_ttl_days = 30
            count = asyncio.run(
                prune_expired_messages(runtime_db_factory=lambda: runtime_db)
            )

        assert count == 1

    def test_transcript_retention_forever(self, transcripts_dir: Path) -> None:
        import asyncio
        from unittest.mock import patch

        from anima_server.services.agent.eager_consolidation import prune_expired_transcripts

        (transcripts_dir / "2025-01-01_thread-1.jsonl.enc").write_bytes(b"data")
        (transcripts_dir / "2025-01-01_thread-1.meta.json").write_text(
            '{"archived_at": "2025-01-01T00:00:00+00:00"}',
            encoding="utf-8",
        )

        with patch("anima_server.services.agent.eager_consolidation.settings") as mock_settings:
            mock_settings.transcript_retention_days = -1
            mock_settings.data_dir = transcripts_dir.parent
            count = asyncio.run(prune_expired_transcripts())

        assert count == 0
        assert (transcripts_dir / "2025-01-01_thread-1.jsonl.enc").exists()

    def test_transcript_retention_deletes_old(self, transcripts_dir: Path) -> None:
        import asyncio
        from unittest.mock import patch

        from anima_server.services.agent.eager_consolidation import prune_expired_transcripts

        transcript_path = transcripts_dir / "2025-01-01_thread-1.jsonl.enc"
        meta_path = transcripts_dir / "2025-01-01_thread-1.meta.json"
        transcript_path.write_bytes(b"data")
        meta_path.write_text(
            '{"archived_at": "2025-01-01T00:00:00+00:00"}',
            encoding="utf-8",
        )

        with patch("anima_server.services.agent.eager_consolidation.settings") as mock_settings:
            mock_settings.transcript_retention_days = 1
            mock_settings.data_dir = transcripts_dir.parent
            count = asyncio.run(prune_expired_transcripts())

        assert count == 1
        assert not transcript_path.exists()
        assert not meta_path.exists()

    def test_transcript_retention_removes_rust_index_entry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        transcripts_dir: Path,
    ) -> None:
        import asyncio
        from unittest.mock import patch

        from anima_server.services.agent.eager_consolidation import prune_expired_transcripts

        transcript_path = transcripts_dir / "2025-01-01_thread-1.jsonl.enc"
        meta_path = transcripts_dir / "2025-01-01_thread-1.meta.json"
        transcript_path.write_bytes(b"data")
        meta_path.write_text(
            json.dumps(
                {
                    "thread_id": 1,
                    "user_id": 7,
                    "archived_at": "2025-01-01T00:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )

        deletes: list[dict[str, object]] = []
        monkeypatch.setattr(retrieval_module, "get_retrieval_root", lambda: transcripts_dir.parent / "indices")
        monkeypatch.setattr(
            retrieval_module,
            "transcript_index_delete",
            lambda **kwargs: deletes.append(kwargs) or True,
        )

        with patch("anima_server.services.agent.eager_consolidation.settings") as mock_settings:
            mock_settings.transcript_retention_days = 1
            mock_settings.data_dir = transcripts_dir.parent
            count = asyncio.run(prune_expired_transcripts())

        assert count == 1
        assert deletes == [
            {
                "root": transcripts_dir.parent / "indices",
                "thread_id": 1,
                "user_id": 7,
            }
        ]


class TestResetThreadLifecycle:
    def test_reset_agent_thread_closes_current_thread(self, runtime_db: Session) -> None:
        import asyncio
        from unittest.mock import AsyncMock, patch

        from anima_server.models.runtime import RuntimeThread
        from anima_server.services.agent.persistence import get_or_create_thread
        from anima_server.services.agent.service import reset_agent_thread

        thread = get_or_create_thread(runtime_db, user_id=1)
        runtime_db.commit()

        with patch(
            "anima_server.services.agent.eager_consolidation.on_thread_close",
            new_callable=AsyncMock,
        ):
            asyncio.run(reset_agent_thread(1, runtime_db))

        closed = runtime_db.get(RuntimeThread, thread.id)
        assert closed is not None
        assert closed.status == "closed"

        replacement = get_or_create_thread(runtime_db, user_id=1)
        assert replacement.id != thread.id
        assert replacement.status == "active"

    def test_reset_agent_thread_creates_replacement_thread_immediately(
        self, runtime_db: Session
    ) -> None:
        import asyncio
        from unittest.mock import AsyncMock, patch

        from anima_server.models.runtime import RuntimeThread
        from anima_server.services.agent.persistence import get_or_create_thread
        from anima_server.services.agent.service import reset_agent_thread

        thread = get_or_create_thread(runtime_db, user_id=1)
        runtime_db.commit()

        with patch(
            "anima_server.services.agent.eager_consolidation.on_thread_close",
            new_callable=AsyncMock,
        ):
            asyncio.run(reset_agent_thread(1, runtime_db))

        active_threads = runtime_db.scalars(
            select(RuntimeThread).where(
                RuntimeThread.user_id == 1,
                RuntimeThread.status == "active",
            )
        ).all()

        assert len(active_threads) == 1
        assert active_threads[0].id != thread.id


class TestThreadCloseRoute:
    def test_close_thread_returns_404_for_other_users_thread(self, runtime_db: Session) -> None:
        import asyncio

        from anima_server.api.routes.threads import close_thread_endpoint
        from anima_server.models.runtime import RuntimeThread
        from anima_server.services.sessions import unlock_session_store
        from fastapi import HTTPException
        from starlette.requests import Request

        thread = RuntimeThread(user_id=2, status="active")
        runtime_db.add(thread)
        runtime_db.commit()

        token = unlock_session_store.create(1, {})
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": f"/api/threads/{thread.id}/close",
                "headers": [(b"x-anima-unlock", token.encode("utf-8"))],
            }
        )

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(close_thread_endpoint(
                thread.id, request=request, runtime_db=runtime_db))

        assert exc_info.value.status_code == 404
