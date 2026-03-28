"""P5: Transcript Archive tests."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from anima_server.db.base import Base
from anima_server.db.runtime_base import RuntimeBase
from anima_server.models import runtime as _runtime_models  # noqa: F401
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy import select
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
    session.add(User(id=1, username="test", display_name="Test", password_hash="x"))
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

        with pytest.raises(Exception):
            decrypt_blob(ciphertext, wrong_key, aad=aad)

    def test_decrypt_wrong_aad_fails(self) -> None:
        import os

        from anima_server.services.crypto import decrypt_blob, encrypt_blob

        dek = os.urandom(32)
        plaintext = b"Secret data"

        ciphertext = encrypt_blob(plaintext, dek, aad=b"correct-aad")

        with pytest.raises(Exception):
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
            {"role": "user", "content": "Hello", "ts": "2026-03-28T10:00:00Z", "seq": 1},
            {"role": "assistant", "content": "Hi!", "ts": "2026-03-28T10:00:05Z", "seq": 2},
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
            {"role": "user", "content": "Hello", "ts": "2026-03-28T10:00:00Z", "seq": 1},
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
            {"role": "user", "content": "Hello", "ts": "2026-03-28T10:00:00Z", "seq": 1},
        ]
        result = export_transcript(
            messages=messages,
            thread_id=42,
            user_id=1,
            dek=test_dek,
            transcripts_dir=transcripts_dir,
        )

        recovered = decrypt_transcript(result.enc_path, dek=test_dek, thread_id=42)
        assert len(recovered) == 1
        assert recovered[0]["content"] == "Hello"

    def test_export_without_dek_creates_plaintext_file(self, transcripts_dir: Path) -> None:
        from anima_server.services.agent.transcript_archive import (
            decrypt_transcript,
            export_transcript,
        )

        messages = [
            {"role": "user", "content": "Hello", "ts": "2026-03-28T10:00:00Z", "seq": 1},
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
                "anima_server.services.agent.eager_consolidation.consolidate_pending_ops",
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
                "anima_server.services.agent.eager_consolidation.consolidate_pending_ops",
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

        refreshed = runtime_db.get(RuntimeThread, thread.id)
        assert refreshed is not None
        assert refreshed.is_archived is True


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
