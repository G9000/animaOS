# P5: Transcript Archive — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the Archive tier — encrypted JSONL transcript export on thread close, sidecar-based search via `recall_transcript` tool, eager consolidation triggers, and PostgreSQL message pruning after TTL.

**Architecture:** Thread close replaces thread delete. On close, a background task runs the existing consolidation pipeline, exports messages to encrypted JSONL in `.anima/transcripts/`, generates an unencrypted sidecar `.meta.json` for fast filtering, and marks the thread as archived. A new active thread is created for the user. Background sweeps handle inactivity-based auto-close, message TTL pruning, and transcript retention. The `recall_transcript` tool lets the agent search archived conversations.

**Tech Stack:** Python, SQLAlchemy, PostgreSQL (runtime), SQLCipher (soul), AES-256-GCM encryption, TF-IDF keyword extraction, pytest

**PRD Reference:** `docs/prds/three-tier-architecture/P5-transcript-archive.md`

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `src/anima_server/services/agent/transcript_archive.py` | Export thread messages to encrypted JSONL, generate sidecar `.meta.json`, atomic file writes |
| `src/anima_server/services/agent/transcript_search.py` | Search sidecar indexes, decrypt matching transcripts, extract context-windowed snippets |
| `src/anima_server/services/agent/eager_consolidation.py` | `on_thread_close()` orchestrator, inactivity sweep, message prune sweep, transcript retention sweep |
| `src/anima_server/api/routes/threads.py` | `POST /api/threads/{thread_id}/close` endpoint |
| `tests/test_p5_transcript_archive.py` | All P5-specific tests |

### Modified Files

| File | Changes |
|------|---------|
| `src/anima_server/config.py` | Add `transcript_retention_days` setting |
| `src/anima_server/models/runtime.py` | Add `closed_at`, `is_archived` to `RuntimeThread`; drop `unique=True` on `user_id`; add index on `(user_id, status)` |
| `src/anima_server/models/agent_runtime.py` | Add `transcript_ref` to `MemoryEpisode` |
| `src/anima_server/services/agent/persistence.py` | Add `close_thread()`, update `get_or_create_thread()` to filter by `status='active'`, add `list_transcript_messages()` |
| `src/anima_server/services/agent/tools.py` | Register `recall_transcript` in `get_extension_tools()` |
| `src/anima_server/services/agent/service.py` | Replace `reset_agent_thread()` with close-and-new-thread pattern |
| `src/anima_server/services/crypto.py` | Add `encrypt_blob()` / `decrypt_blob()` for raw-bytes file encryption |
| `src/anima_server/main.py` | Register inactivity and prune sweeps in lifespan; import threads router |
| `src/anima_server/api/routes/chat.py` | Wire "new chat" to thread close instead of thread delete |
| `tests/conftest.py` | Add `transcripts_dir` fixture |

---

## Task 1: Config + Schema Changes

**Files:**
- Modify: `apps/server/src/anima_server/config.py`
- Modify: `apps/server/src/anima_server/models/runtime.py`
- Modify: `apps/server/src/anima_server/models/agent_runtime.py`
- Test: `apps/server/tests/test_p5_transcript_archive.py`

- [ ] **Step 1: Write failing test for RuntimeThread schema changes**

```python
# apps/server/tests/test_p5_transcript_archive.py
"""P5: Transcript Archive tests."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import BigInteger, create_engine, event, select, text
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from anima_server.db.runtime_base import RuntimeBase


@compiles(BigInteger, "sqlite")
def _compile_biginteger_sqlite(type_, compiler, **kw):
    return "INTEGER"


@pytest.fixture()
def runtime_db() -> Session:
    """In-memory SQLite session with runtime tables."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    RuntimeBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    yield session
    session.close()
    engine.dispose()


class TestRuntimeThreadSchema:
    def test_closed_at_field_exists(self, runtime_db: Session):
        from anima_server.models.runtime import RuntimeThread

        thread = RuntimeThread(user_id=1, status="active")
        runtime_db.add(thread)
        runtime_db.flush()

        assert thread.closed_at is None

        thread.closed_at = datetime.now(UTC)
        runtime_db.flush()
        assert thread.closed_at is not None

    def test_is_archived_field_defaults_false(self, runtime_db: Session):
        from anima_server.models.runtime import RuntimeThread

        thread = RuntimeThread(user_id=1, status="active")
        runtime_db.add(thread)
        runtime_db.flush()

        assert thread.is_archived is False

    def test_multiple_threads_per_user(self, runtime_db: Session):
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/server && python -m pytest tests/test_p5_transcript_archive.py::TestRuntimeThreadSchema -v`
Expected: FAIL — `closed_at` and `is_archived` not found, or unique constraint violation

- [ ] **Step 3: Update RuntimeThread model**

In `apps/server/src/anima_server/models/runtime.py`, add `closed_at` and `is_archived` fields to `RuntimeThread`, remove `unique=True` from `user_id`, and add a composite index:

```python
# Add these fields after last_message_at:
    closed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMPTZ,
        nullable=True,
    )
    is_archived: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
```

Change `user_id` from `unique=True` to just `index=True`:

```python
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        index=True,
    )
```

Add a `__table_args__` with the composite index:

```python
    __table_args__ = (
        Index("ix_runtime_threads_user_status", "user_id", "status"),
    )
```

Add `Boolean` to the imports from `sqlalchemy`.

- [ ] **Step 4: Add transcript_retention_days to config**

In `apps/server/src/anima_server/config.py`, add after `message_ttl_days`:

```python
    transcript_retention_days: int = -1  # -1 = keep forever
```

- [ ] **Step 5: Add transcript_ref to MemoryEpisode**

In `apps/server/src/anima_server/models/agent_runtime.py`, add to `MemoryEpisode` after `segmentation_method`:

```python
    transcript_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd apps/server && python -m pytest tests/test_p5_transcript_archive.py::TestRuntimeThreadSchema -v`
Expected: PASS

- [ ] **Step 7: Run full test suite for regressions**

Run: `cd apps/server && python -m pytest tests/ -x -q --timeout=60`
Expected: All pass. The unique constraint removal may cause test failures in conftest fixtures that assume one thread per user — fix by updating `get_or_create_thread()` callers.

- [ ] **Step 8: Commit**

```bash
git add apps/server/src/anima_server/config.py apps/server/src/anima_server/models/runtime.py apps/server/src/anima_server/models/agent_runtime.py apps/server/tests/test_p5_transcript_archive.py
git commit -m "feat(p5): schema changes — RuntimeThread close fields, MemoryEpisode transcript_ref, config"
```

---

## Task 2: Thread Lifecycle Helpers

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/persistence.py`
- Test: `apps/server/tests/test_p5_transcript_archive.py`

- [ ] **Step 1: Write failing tests for thread lifecycle**

Add to `test_p5_transcript_archive.py`:

```python
class TestThreadLifecycle:
    def test_get_or_create_thread_finds_active(self, runtime_db: Session):
        from anima_server.models.runtime import RuntimeThread
        from anima_server.services.agent.persistence import get_or_create_thread

        # Create a closed thread and an active thread
        closed = RuntimeThread(user_id=1, status="closed")
        runtime_db.add(closed)
        runtime_db.flush()

        thread = get_or_create_thread(runtime_db, user_id=1)
        assert thread.status == "active"
        assert thread.id != closed.id

    def test_get_or_create_thread_ignores_closed(self, runtime_db: Session):
        from anima_server.models.runtime import RuntimeThread
        from anima_server.services.agent.persistence import get_or_create_thread

        closed = RuntimeThread(user_id=1, status="closed")
        runtime_db.add(closed)
        runtime_db.flush()

        thread = get_or_create_thread(runtime_db, user_id=1)
        assert thread.id != closed.id
        assert thread.status == "active"

    def test_close_thread(self, runtime_db: Session):
        from anima_server.services.agent.persistence import close_thread, get_or_create_thread

        thread = get_or_create_thread(runtime_db, user_id=1)
        original_id = thread.id

        close_thread(runtime_db, thread_id=original_id)
        runtime_db.flush()

        from anima_server.models.runtime import RuntimeThread

        closed = runtime_db.get(RuntimeThread, original_id)
        assert closed.status == "closed"
        assert closed.closed_at is not None

    def test_close_thread_idempotent(self, runtime_db: Session):
        from anima_server.services.agent.persistence import close_thread, get_or_create_thread

        thread = get_or_create_thread(runtime_db, user_id=1)
        close_thread(runtime_db, thread_id=thread.id)
        runtime_db.flush()

        # Closing again should not raise
        result = close_thread(runtime_db, thread_id=thread.id)
        assert result is False  # already closed

    def test_list_transcript_messages(self, runtime_db: Session):
        from anima_server.models.runtime import RuntimeMessage
        from anima_server.services.agent.persistence import (
            get_or_create_thread,
            list_transcript_messages,
        )

        thread = get_or_create_thread(runtime_db, user_id=1)

        # Add messages of various roles
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
        # system and approval should be excluded
        assert "system" not in roles
        assert "approval" not in roles
        assert len(msgs) == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/server && python -m pytest tests/test_p5_transcript_archive.py::TestThreadLifecycle -v`
Expected: FAIL — `close_thread` and `list_transcript_messages` not defined

- [ ] **Step 3: Update get_or_create_thread to filter by active status**

In `apps/server/src/anima_server/services/agent/persistence.py`, update `get_or_create_thread()`:

```python
def get_or_create_thread(db: Session, user_id: int) -> RuntimeThread:
    thread = db.scalar(
        select(RuntimeThread).where(
            RuntimeThread.user_id == user_id,
            RuntimeThread.status == "active",
        )
    )
    if thread is not None:
        return thread

    thread = RuntimeThread(
        user_id=user_id,
        status="active",
    )
    db.add(thread)
    db.flush()
    return thread
```

- [ ] **Step 4: Add close_thread and list_transcript_messages**

Add to `apps/server/src/anima_server/services/agent/persistence.py`:

```python
def close_thread(db: Session, *, thread_id: int) -> bool:
    """Mark a thread as closed. Returns True if status changed, False if already closed."""
    thread = db.get(RuntimeThread, thread_id)
    if thread is None:
        return False
    if thread.status == "closed":
        return False
    thread.status = "closed"
    thread.closed_at = datetime.now(UTC)
    db.flush()
    return True


def list_transcript_messages(
    db: Session,
    *,
    thread_id: int,
) -> list[RuntimeMessage]:
    """List messages suitable for transcript archival.

    Excludes system messages and approval checkpoints.
    Excludes compacted stubs (is_in_context=False AND empty content).
    Ordered by sequence_id.
    """
    return list(
        db.scalars(
            select(RuntimeMessage)
            .where(
                RuntimeMessage.thread_id == thread_id,
                RuntimeMessage.role.notin_(("system", "approval")),
                or_(
                    RuntimeMessage.is_in_context.is_(True),
                    RuntimeMessage.content_text.isnot(None),
                    RuntimeMessage.content_text != "",
                ),
            )
            .order_by(RuntimeMessage.sequence_id)
        ).all()
    )
```

Add `or_` to the `sqlalchemy` imports at the top of `persistence.py` if not already present. Add `from datetime import UTC, datetime` if not already imported.

- [ ] **Step 5: Run tests**

Run: `cd apps/server && python -m pytest tests/test_p5_transcript_archive.py::TestThreadLifecycle -v`
Expected: PASS

- [ ] **Step 6: Run full test suite**

Run: `cd apps/server && python -m pytest tests/ -x -q --timeout=60`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add apps/server/src/anima_server/services/agent/persistence.py apps/server/tests/test_p5_transcript_archive.py
git commit -m "feat(p5): thread lifecycle — close_thread, list_transcript_messages, active-only get_or_create"
```

---

## Task 3: Binary Encryption Helpers

**Files:**
- Modify: `apps/server/src/anima_server/services/crypto.py`
- Test: `apps/server/tests/test_p5_transcript_archive.py`

- [ ] **Step 1: Write failing tests for blob encryption**

Add to `test_p5_transcript_archive.py`:

```python
class TestBlobEncryption:
    def test_encrypt_decrypt_roundtrip(self):
        import os
        from anima_server.services.crypto import decrypt_blob, encrypt_blob

        dek = os.urandom(32)
        plaintext = b"Hello, this is a transcript.\nLine two."
        aad = b"transcript:42:2026-03-28"

        ciphertext = encrypt_blob(plaintext, dek, aad=aad)
        assert ciphertext != plaintext
        assert len(ciphertext) == 12 + len(plaintext) + 16  # IV + data + tag

        recovered = decrypt_blob(ciphertext, dek, aad=aad)
        assert recovered == plaintext

    def test_decrypt_wrong_key_fails(self):
        import os
        from anima_server.services.crypto import decrypt_blob, encrypt_blob

        dek = os.urandom(32)
        wrong_key = os.urandom(32)
        plaintext = b"Secret data"
        aad = b"test:1:2026"

        ciphertext = encrypt_blob(plaintext, dek, aad=aad)

        with pytest.raises(Exception):
            decrypt_blob(ciphertext, wrong_key, aad=aad)

    def test_decrypt_wrong_aad_fails(self):
        import os
        from anima_server.services.crypto import decrypt_blob, encrypt_blob

        dek = os.urandom(32)
        plaintext = b"Secret data"

        ciphertext = encrypt_blob(plaintext, dek, aad=b"correct-aad")

        with pytest.raises(Exception):
            decrypt_blob(ciphertext, dek, aad=b"wrong-aad")

    def test_empty_plaintext(self):
        import os
        from anima_server.services.crypto import decrypt_blob, encrypt_blob

        dek = os.urandom(32)
        ciphertext = encrypt_blob(b"", dek, aad=b"test")
        recovered = decrypt_blob(ciphertext, dek, aad=b"test")
        assert recovered == b""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/server && python -m pytest tests/test_p5_transcript_archive.py::TestBlobEncryption -v`
Expected: FAIL — `encrypt_blob` not defined

- [ ] **Step 3: Implement encrypt_blob and decrypt_blob**

Add to `apps/server/src/anima_server/services/crypto.py`:

```python
def encrypt_blob(plaintext: bytes, dek: bytes, *, aad: bytes | None = None) -> bytes:
    """Encrypt raw bytes with AES-256-GCM.

    Returns: IV (12 bytes) || ciphertext || auth_tag (16 bytes)
    """
    iv = os.urandom(IV_LENGTH)
    aesgcm = AESGCM(dek)
    # AESGCM.encrypt returns ciphertext || tag
    ct_and_tag = aesgcm.encrypt(iv, plaintext, aad)
    return iv + ct_and_tag


def decrypt_blob(data: bytes, dek: bytes, *, aad: bytes | None = None) -> bytes:
    """Decrypt raw bytes encrypted by encrypt_blob.

    Expects: IV (12 bytes) || ciphertext || auth_tag (16 bytes)
    """
    if len(data) < IV_LENGTH:
        raise ValueError("Encrypted data too short")
    iv = data[:IV_LENGTH]
    ct_and_tag = data[IV_LENGTH:]
    aesgcm = AESGCM(dek)
    return aesgcm.decrypt(iv, ct_and_tag, aad)
```

- [ ] **Step 4: Run tests**

Run: `cd apps/server && python -m pytest tests/test_p5_transcript_archive.py::TestBlobEncryption -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/anima_server/services/crypto.py apps/server/tests/test_p5_transcript_archive.py
git commit -m "feat(p5): add encrypt_blob/decrypt_blob for binary file encryption"
```

---

## Task 4: Transcript Archive (Export + Sidecar)

**Files:**
- Create: `apps/server/src/anima_server/services/agent/transcript_archive.py`
- Test: `apps/server/tests/test_p5_transcript_archive.py`

- [ ] **Step 1: Write failing tests for JSONL serialization**

Add to `test_p5_transcript_archive.py`:

```python
import json
import os
from pathlib import Path


@pytest.fixture()
def transcripts_dir(tmp_path: Path) -> Path:
    d = tmp_path / "transcripts"
    d.mkdir()
    return d


@pytest.fixture()
def test_dek() -> bytes:
    return os.urandom(32)


class TestTranscriptExport:
    def test_serialize_messages_to_jsonl(self):
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

    def test_export_creates_encrypted_file(self, transcripts_dir: Path, test_dek: bytes):
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

    def test_export_creates_sidecar(self, transcripts_dir: Path, test_dek: bytes):
        from anima_server.services.agent.transcript_archive import export_transcript

        messages = [
            {"role": "user", "content": "Tell me about project deadlines", "ts": "2026-03-28T10:00:00Z", "seq": 1},
            {"role": "assistant", "content": "The deadline is April 15", "ts": "2026-03-28T10:00:05Z", "seq": 2},
        ]
        result = export_transcript(
            messages=messages,
            thread_id=42,
            user_id=1,
            dek=test_dek,
            transcripts_dir=transcripts_dir,
        )

        assert result.meta_path.exists()
        meta = json.loads(result.meta_path.read_text())
        assert meta["version"] == 1
        assert meta["thread_id"] == 42
        assert meta["user_id"] == 1
        assert meta["message_count"] == 2
        assert isinstance(meta["keywords"], list)
        assert len(meta["keywords"]) <= 10

    def test_export_decrypt_roundtrip(self, transcripts_dir: Path, test_dek: bytes):
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

    def test_export_atomic_no_partial_on_error(self, transcripts_dir: Path, test_dek: bytes):
        """If encryption fails, no .enc file should be left behind."""
        enc_files_before = list(transcripts_dir.glob("*.enc"))
        assert len(enc_files_before) == 0

        # Export with empty messages should still succeed (edge case)
        from anima_server.services.agent.transcript_archive import export_transcript

        result = export_transcript(
            messages=[],
            thread_id=99,
            user_id=1,
            dek=test_dek,
            transcripts_dir=transcripts_dir,
        )
        # Empty transcript should still create files
        assert result.enc_path.exists()

    def test_export_excludes_nothing_extra(self):
        """Serialization should include all passed messages as-is."""
        from anima_server.services.agent.transcript_archive import serialize_messages_to_jsonl

        messages = [
            {"role": "assistant", "content": "Response", "thinking": "inner thought", "ts": "2026-03-28T10:00:05Z", "seq": 1},
        ]
        result = serialize_messages_to_jsonl(messages)
        parsed = json.loads(result.strip())
        assert parsed["thinking"] == "inner thought"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/server && python -m pytest tests/test_p5_transcript_archive.py::TestTranscriptExport -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement transcript_archive.py**

```python
# apps/server/src/anima_server/services/agent/transcript_archive.py
"""Transcript archive: export thread messages to encrypted JSONL + sidecar."""
from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from anima_server.services.crypto import decrypt_blob, encrypt_blob

logger = logging.getLogger(__name__)

# English stop words (compact set for TF-IDF filtering)
_STOP_WORDS = frozenset(
    "a an the is are was were be been being have has had do does did will would "
    "shall should may might can could am i me my we our you your he she it they "
    "them his her its their this that these those in on at to for of with by from "
    "and or but not so if then else when how what which who whom where why all any "
    "some no nor too also very just about up down out off over under again further "
    "once here there each every both few more most other such only own same than "
    "into through during before after above below between don doesn didn isn aren "
    "wasn weren won wouldn hasn hadn hasn ll ve re".split()
)


@dataclass(frozen=True)
class TranscriptExportResult:
    enc_path: Path
    meta_path: Path
    message_count: int


def serialize_messages_to_jsonl(messages: list[dict]) -> str:
    """Serialize message dicts to newline-delimited JSON."""
    lines = [json.dumps(m, ensure_ascii=False, separators=(",", ":")) for m in messages]
    return "\n".join(lines) + ("\n" if lines else "")


def _build_aad(thread_id: int, date_str: str) -> bytes:
    """Build AAD for transcript encryption."""
    return f"transcript:{thread_id}:{date_str}".encode("utf-8")


def _extract_keywords(messages: list[dict], max_keywords: int = 10) -> list[str]:
    """Extract top keywords from user messages using simple TF-IDF."""
    words: list[str] = []
    for m in messages:
        if m.get("role") != "user":
            continue
        content = m.get("content", "")
        if not content:
            continue
        tokens = re.findall(r"[a-zA-Z]{3,}", content.lower())
        words.extend(t for t in tokens if t not in _STOP_WORDS)

    if not words:
        return []

    counts = Counter(words)
    return [word for word, _ in counts.most_common(max_keywords)]


def _build_summary(messages: list[dict]) -> str:
    """Build a one-line summary from first and last user messages."""
    user_msgs = [m for m in messages if m.get("role") == "user"]
    if not user_msgs:
        return "Empty conversation"
    first = user_msgs[0].get("content", "")[:100]
    if len(user_msgs) == 1:
        return first
    last = user_msgs[-1].get("content", "")[:100]
    return f"{first} ... {last}"


def export_transcript(
    *,
    messages: list[dict],
    thread_id: int,
    user_id: int,
    dek: bytes,
    transcripts_dir: Path,
    episode_ids: list[str] | None = None,
) -> TranscriptExportResult:
    """Export messages to encrypted JSONL + sidecar metadata.

    Returns paths to the created files.
    """
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(UTC)
    date_str = now.strftime("%Y-%m-%d")

    # Determine date range from messages
    timestamps = [m.get("ts", "") for m in messages if m.get("ts")]
    date_start = timestamps[0] if timestamps else now.isoformat()
    date_end = timestamps[-1] if timestamps else now.isoformat()

    # Serialize to JSONL
    jsonl_text = serialize_messages_to_jsonl(messages)
    plaintext_bytes = jsonl_text.encode("utf-8")

    # Encrypt
    aad = _build_aad(thread_id, date_str)
    encrypted = encrypt_blob(plaintext_bytes, dek, aad=aad)

    # Atomic write: encrypted JSONL
    filename_base = f"{date_str}_thread-{thread_id}"
    enc_path = transcripts_dir / f"{filename_base}.jsonl.enc"
    tmp_path = enc_path.with_suffix(".tmp")
    tmp_path.write_bytes(encrypted)
    os.replace(str(tmp_path), str(enc_path))

    # Build sidecar
    keywords = _extract_keywords(messages)
    summary = _build_summary(messages)
    roles = sorted(set(m.get("role", "") for m in messages))

    sidecar = {
        "version": 1,
        "thread_id": thread_id,
        "user_id": user_id,
        "date_start": date_start,
        "date_end": date_end,
        "message_count": len(messages),
        "roles": roles,
        "keywords": keywords,
        "summary": summary,
        "chunk_offsets": [0],
        "episodic_memory_ids": episode_ids or [],
        "archived_at": now.isoformat(),
        "encryption": {
            "domain": "conversations",
            "aad_prefix": "transcript",
        },
    }

    meta_path = transcripts_dir / f"{filename_base}.meta.json"
    meta_tmp = meta_path.with_suffix(".tmp")
    meta_tmp.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(str(meta_tmp), str(meta_path))

    return TranscriptExportResult(
        enc_path=enc_path,
        meta_path=meta_path,
        message_count=len(messages),
    )


def decrypt_transcript(
    enc_path: Path,
    *,
    dek: bytes,
    thread_id: int,
) -> list[dict]:
    """Decrypt an encrypted JSONL transcript file. Returns list of message dicts."""
    data = enc_path.read_bytes()

    # Extract date from filename: YYYY-MM-DD_thread-N.jsonl.enc
    filename = enc_path.stem.replace(".jsonl", "")  # "YYYY-MM-DD_thread-N"
    date_str = filename.split("_thread-")[0]

    aad = _build_aad(thread_id, date_str)
    plaintext = decrypt_blob(data, dek, aad=aad)

    messages = []
    for line in plaintext.decode("utf-8").strip().split("\n"):
        if line.strip():
            messages.append(json.loads(line))
    return messages


def messages_to_transcript_dicts(
    messages: list,
) -> list[dict]:
    """Convert RuntimeMessage ORM objects to transcript dict format.

    Handles both RuntimeMessage objects (with attributes) and plain dicts.
    """
    result = []
    for m in messages:
        role = getattr(m, "role", None) or m.get("role", "")
        content = getattr(m, "content_text", None) or m.get("content_text", "")
        seq = getattr(m, "sequence_id", None) or m.get("sequence_id", 0)
        created = getattr(m, "created_at", None) or m.get("created_at")

        entry: dict = {
            "role": role,
            "content": content or "",
            "ts": created.isoformat() if isinstance(created, datetime) else str(created or ""),
            "seq": seq,
        }

        # Optional fields
        content_json = getattr(m, "content_json", None) or m.get("content_json")
        if content_json and isinstance(content_json, dict):
            tool_calls = content_json.get("tool_calls")
            if tool_calls:
                entry["tool_calls"] = tool_calls
            thinking = content_json.get("thinking")
            if thinking:
                entry["thinking"] = thinking

        tool_name = getattr(m, "tool_name", None) or m.get("tool_name")
        if tool_name:
            entry["tool_name"] = tool_name

        tool_call_id = getattr(m, "tool_call_id", None) or m.get("tool_call_id")
        if tool_call_id:
            entry["tool_call_id"] = tool_call_id

        source = getattr(m, "source", None) or m.get("source")
        if source:
            entry["source"] = source

        result.append(entry)
    return result
```

- [ ] **Step 4: Run tests**

Run: `cd apps/server && python -m pytest tests/test_p5_transcript_archive.py::TestTranscriptExport -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/anima_server/services/agent/transcript_archive.py apps/server/tests/test_p5_transcript_archive.py
git commit -m "feat(p5): transcript archive — JSONL export, encryption, sidecar generation"
```

---

## Task 5: Transcript Search

**Files:**
- Create: `apps/server/src/anima_server/services/agent/transcript_search.py`
- Test: `apps/server/tests/test_p5_transcript_archive.py`

- [ ] **Step 1: Write failing tests for transcript search**

Add to `test_p5_transcript_archive.py`:

```python
class TestTranscriptSearch:
    def _create_test_transcript(self, transcripts_dir: Path, test_dek: bytes, thread_id: int, messages: list[dict]) -> None:
        from anima_server.services.agent.transcript_archive import export_transcript

        export_transcript(
            messages=messages,
            thread_id=thread_id,
            user_id=1,
            dek=test_dek,
            transcripts_dir=transcripts_dir,
        )

    def test_search_finds_matching_transcript(self, transcripts_dir: Path, test_dek: bytes):
        from anima_server.services.agent.transcript_search import search_transcripts

        self._create_test_transcript(
            transcripts_dir,
            test_dek,
            thread_id=42,
            messages=[
                {"role": "user", "content": "Tell me about quantum physics", "ts": "2026-03-28T10:00:00Z", "seq": 1},
                {"role": "assistant", "content": "Quantum physics is fascinating", "ts": "2026-03-28T10:00:05Z", "seq": 2},
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
        assert any("quantum" in s.text.lower() for s in snippets)

    def test_search_returns_empty_for_no_match(self, transcripts_dir: Path, test_dek: bytes):
        from anima_server.services.agent.transcript_search import search_transcripts

        self._create_test_transcript(
            transcripts_dir,
            test_dek,
            thread_id=42,
            messages=[
                {"role": "user", "content": "Talk about cooking", "ts": "2026-03-28T10:00:00Z", "seq": 1},
            ],
        )

        snippets = search_transcripts(
            query="quantum physics",
            user_id=1,
            dek=test_dek,
            transcripts_dir=transcripts_dir,
            days_back=30,
        )
        assert len(snippets) == 0

    def test_search_respects_budget(self, transcripts_dir: Path, test_dek: bytes):
        from anima_server.services.agent.transcript_search import search_transcripts

        # Create a transcript with many messages
        messages = [
            {"role": "user", "content": f"Message about topic {i} with relevant keywords", "ts": f"2026-03-28T10:{i:02d}:00Z", "seq": i}
            for i in range(50)
        ]
        self._create_test_transcript(transcripts_dir, test_dek, thread_id=42, messages=messages)

        snippets = search_transcripts(
            query="topic relevant keywords",
            user_id=1,
            dek=test_dek,
            transcripts_dir=transcripts_dir,
            budget_chars=200,
        )
        total_chars = sum(len(s.text) for s in snippets)
        assert total_chars <= 200 + 100  # allow some margin for formatting

    def test_search_no_transcripts(self, transcripts_dir: Path, test_dek: bytes):
        from anima_server.services.agent.transcript_search import search_transcripts

        snippets = search_transcripts(
            query="anything",
            user_id=1,
            dek=test_dek,
            transcripts_dir=transcripts_dir,
        )
        assert snippets == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/server && python -m pytest tests/test_p5_transcript_archive.py::TestTranscriptSearch -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement transcript_search.py**

```python
# apps/server/src/anima_server/services/agent/transcript_search.py
"""Search archived transcripts via sidecar-based filtering + decryption."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from anima_server.services.agent.transcript_archive import decrypt_transcript

logger = logging.getLogger(__name__)


@dataclass
class TranscriptSnippet:
    date: str
    thread_id: int
    text: str


def _keyword_overlap_score(query: str, keywords: list[str]) -> float:
    """Score a sidecar's keywords against a search query."""
    query_words = set(re.findall(r"[a-zA-Z]{3,}", query.lower()))
    if not query_words or not keywords:
        return 0.0
    keyword_set = set(k.lower() for k in keywords)
    overlap = len(query_words & keyword_set)
    return overlap / max(len(query_words), 1)


def _text_overlap_score(query: str, text: str) -> float:
    """Score a message's text against a search query."""
    query_words = set(re.findall(r"[a-zA-Z]{3,}", query.lower()))
    text_words = set(re.findall(r"[a-zA-Z]{3,}", text.lower()))
    if not query_words or not text_words:
        return 0.0
    overlap = len(query_words & text_words)
    return overlap / max(len(query_words), 1)


def _date_recency_bonus(date_str: str) -> float:
    """Give a small bonus to more recent transcripts."""
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        age_days = (datetime.now(UTC) - dt).days
        return max(0.0, 1.0 - (age_days / 365.0))
    except (ValueError, TypeError):
        return 0.0


def search_transcripts(
    *,
    query: str,
    user_id: int,
    dek: bytes,
    transcripts_dir: Path,
    days_back: int = 30,
    max_transcripts: int = 5,
    max_snippets: int = 10,
    snippet_context: int = 2,
    budget_chars: int = 3000,
) -> list[TranscriptSnippet]:
    """Search archived transcripts.

    1. List sidecar files
    2. Filter by date range and user_id
    3. Score by keyword overlap + recency
    4. Decrypt top candidates
    5. Extract context-windowed snippets
    """
    if not transcripts_dir.exists():
        return []

    cutoff = datetime.now(UTC) - timedelta(days=days_back)

    # Phase 1: filter and rank sidecars
    candidates: list[tuple[float, Path, dict]] = []
    for meta_path in transcripts_dir.glob("*.meta.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        if meta.get("user_id") != user_id:
            continue

        # Date filter
        date_start_str = meta.get("date_start", "")
        try:
            date_start = datetime.fromisoformat(date_start_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue

        if date_start < cutoff:
            continue

        # Score
        kw_score = _keyword_overlap_score(query, meta.get("keywords", []))
        recency = _date_recency_bonus(date_start_str)
        score = kw_score * 2.0 + recency

        # Find matching .enc file
        enc_name = meta_path.name.replace(".meta.json", ".jsonl.enc")
        enc_path = meta_path.parent / enc_name
        if not enc_path.exists():
            continue

        candidates.append((score, enc_path, meta))

    # Sort by score descending, take top N
    candidates.sort(key=lambda x: x[0], reverse=True)
    candidates = candidates[:max_transcripts]

    if not candidates:
        return []

    # Phase 2: decrypt and scan
    snippets: list[TranscriptSnippet] = []
    chars_used = 0

    for _score, enc_path, meta in candidates:
        thread_id = meta.get("thread_id", 0)
        try:
            messages = decrypt_transcript(enc_path, dek=dek, thread_id=thread_id)
        except Exception:
            logger.warning("Failed to decrypt transcript %s", enc_path.name, exc_info=True)
            continue

        # Score each message
        scored: list[tuple[float, int]] = []
        for i, msg in enumerate(messages):
            content = msg.get("content", "")
            s = _text_overlap_score(query, content)
            if s > 0:
                scored.append((s, i))

        scored.sort(key=lambda x: x[0], reverse=True)

        # Extract context windows (dedup overlapping)
        seen_indices: set[int] = set()
        date_str = meta.get("date_start", "unknown")[:10]

        for _, hit_idx in scored:
            if chars_used >= budget_chars:
                break

            start = max(0, hit_idx - snippet_context)
            end = min(len(messages), hit_idx + snippet_context + 1)

            # Skip if we already included these messages
            window_indices = set(range(start, end))
            if window_indices & seen_indices:
                continue
            seen_indices |= window_indices

            # Format snippet
            lines = []
            for j in range(start, end):
                m = messages[j]
                role = m.get("role", "unknown").capitalize()
                content = m.get("content", "")
                lines.append(f"{role}: {content}")

            text = "\n".join(lines)
            if chars_used + len(text) > budget_chars:
                break

            snippets.append(TranscriptSnippet(
                date=date_str,
                thread_id=thread_id,
                text=text,
            ))
            chars_used += len(text)

            if len(snippets) >= max_snippets:
                break

        if len(snippets) >= max_snippets or chars_used >= budget_chars:
            break

    return snippets


def format_snippets(snippets: list[TranscriptSnippet]) -> str:
    """Format snippets for tool output."""
    if not snippets:
        return "No matching transcripts found."

    parts = []
    for s in snippets:
        parts.append(f"[{s.date}, thread {s.thread_id}]\n{s.text}")
    return "\n\n".join(parts)
```

- [ ] **Step 4: Run tests**

Run: `cd apps/server && python -m pytest tests/test_p5_transcript_archive.py::TestTranscriptSearch -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/anima_server/services/agent/transcript_search.py apps/server/tests/test_p5_transcript_archive.py
git commit -m "feat(p5): transcript search — sidecar filtering, decryption, snippet extraction"
```

---

## Task 6: recall_transcript Tool

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/tools.py`
- Test: `apps/server/tests/test_p5_transcript_archive.py`

- [ ] **Step 1: Write failing test for tool registration**

Add to `test_p5_transcript_archive.py`:

```python
class TestRecallTranscriptTool:
    def test_tool_in_extension_tools(self):
        from anima_server.services.agent.tools import get_extension_tools

        tools = get_extension_tools()
        tool_names = [t.name for t in tools]
        assert "recall_transcript" in tool_names

    def test_tool_has_correct_params(self):
        from anima_server.services.agent.tools import get_extension_tools

        tools = get_extension_tools()
        tool = next(t for t in tools if t.name == "recall_transcript")
        schema = tool.args_schema
        assert "query" in schema
        assert "days_back" in schema
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/server && python -m pytest tests/test_p5_transcript_archive.py::TestRecallTranscriptTool -v`
Expected: FAIL — `recall_transcript` not found in extension tools

- [ ] **Step 3: Implement recall_transcript tool**

In `apps/server/src/anima_server/services/agent/tools.py`, add the tool function (before `get_extension_tools()`):

```python
@tool
def recall_transcript(query: str, days_back: int = 30) -> str:
    """Search past conversation transcripts for specific details.
    Use this when you need exact wording or verbatim recall from
    past conversations, not just general memory of what happened.
    Returns relevant snippets, not full conversations."""
    from anima_server.services.agent.tool_context import get_tool_context
    from anima_server.services.agent.transcript_search import format_snippets, search_transcripts
    from anima_server.services.data_crypto import get_active_dek

    ctx = get_tool_context()

    dek = get_active_dek(ctx.user_id, "conversations")
    if dek is None:
        return "Transcript search unavailable (no active encryption key)."

    from anima_server.config import settings

    transcripts_dir = settings.data_dir / "transcripts"

    snippets = search_transcripts(
        query=query,
        user_id=ctx.user_id,
        dek=dek,
        transcripts_dir=transcripts_dir,
        days_back=days_back,
    )
    return format_snippets(snippets)
```

Add `recall_transcript` to the list returned by `get_extension_tools()`:

```python
def get_extension_tools() -> list:
    return [
        create_task,
        list_tasks,
        complete_task,
        set_intention,
        complete_goal,
        note_to_self,
        dismiss_note,
        update_human_memory,
        current_datetime,
        recall_transcript,  # <-- add here
    ]
```

- [ ] **Step 4: Run tests**

Run: `cd apps/server && python -m pytest tests/test_p5_transcript_archive.py::TestRecallTranscriptTool -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `cd apps/server && python -m pytest tests/ -x -q --timeout=60`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add apps/server/src/anima_server/services/agent/tools.py apps/server/tests/test_p5_transcript_archive.py
git commit -m "feat(p5): recall_transcript tool — search archived conversations"
```

---

## Task 7: Thread Close Endpoint + Eager Consolidation

**Files:**
- Create: `apps/server/src/anima_server/api/routes/threads.py`
- Create: `apps/server/src/anima_server/services/agent/eager_consolidation.py`
- Modify: `apps/server/src/anima_server/main.py`
- Test: `apps/server/tests/test_p5_transcript_archive.py`

- [ ] **Step 1: Write failing test for thread close endpoint**

Add to `test_p5_transcript_archive.py`:

```python
class TestThreadCloseEndpoint:
    def test_close_thread_returns_200(self, runtime_db: Session):
        from anima_server.services.agent.persistence import get_or_create_thread

        thread = get_or_create_thread(runtime_db, user_id=1)
        runtime_db.commit()

        from anima_server.services.agent.persistence import close_thread

        result = close_thread(runtime_db, thread_id=thread.id)
        assert result is True

        from anima_server.models.runtime import RuntimeThread

        closed = runtime_db.get(RuntimeThread, thread.id)
        assert closed.status == "closed"

    def test_close_already_closed_thread(self, runtime_db: Session):
        from anima_server.services.agent.persistence import close_thread, get_or_create_thread

        thread = get_or_create_thread(runtime_db, user_id=1)
        close_thread(runtime_db, thread_id=thread.id)
        runtime_db.commit()

        result = close_thread(runtime_db, thread_id=thread.id)
        assert result is False
```

- [ ] **Step 2: Write test for eager consolidation orchestrator**

```python
class TestEagerConsolidation:
    def test_on_thread_close_exports_transcript(
        self, runtime_db: Session, transcripts_dir: Path, test_dek: bytes
    ):
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
                "anima_server.services.agent.eager_consolidation.get_active_dek",
                return_value=test_dek,
            ),
            patch(
                "anima_server.services.agent.eager_consolidation._get_transcripts_dir",
                return_value=transcripts_dir,
            ),
        ):
            asyncio.get_event_loop().run_until_complete(
                on_thread_close(
                    thread_id=thread.id,
                    user_id=1,
                    runtime_db_factory=lambda: runtime_db,
                )
            )

        enc_files = list(transcripts_dir.glob("*.jsonl.enc"))
        assert len(enc_files) == 1

    def test_on_thread_close_marks_archived(
        self, runtime_db: Session, transcripts_dir: Path, test_dek: bytes
    ):
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
                "anima_server.services.agent.eager_consolidation.get_active_dek",
                return_value=test_dek,
            ),
            patch(
                "anima_server.services.agent.eager_consolidation._get_transcripts_dir",
                return_value=transcripts_dir,
            ),
        ):
            asyncio.get_event_loop().run_until_complete(
                on_thread_close(
                    thread_id=thread.id,
                    user_id=1,
                    runtime_db_factory=lambda: runtime_db,
                )
            )

        refreshed = runtime_db.get(RuntimeThread, thread.id)
        assert refreshed.is_archived is True
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd apps/server && python -m pytest tests/test_p5_transcript_archive.py -k "TestThreadCloseEndpoint or TestEagerConsolidation" -v`
Expected: FAIL — module not found

- [ ] **Step 4: Implement eager_consolidation.py**

```python
# apps/server/src/anima_server/services/agent/eager_consolidation.py
"""Eager consolidation: thread close triggers archival pipeline."""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models.runtime import RuntimeMessage, RuntimeThread
from anima_server.services.agent.persistence import list_transcript_messages
from anima_server.services.agent.transcript_archive import (
    export_transcript,
    messages_to_transcript_dicts,
)
from anima_server.services.data_crypto import get_active_dek

logger = logging.getLogger(__name__)


def _get_transcripts_dir() -> Path:
    return settings.data_dir / "transcripts"


async def on_thread_close(
    *,
    thread_id: int,
    user_id: int,
    runtime_db_factory: Callable[..., Session] | None = None,
    soul_db_factory: Callable[..., object] | None = None,
) -> None:
    """Run after a thread is closed. Non-blocking background task.

    1. Run existing consolidation pipeline (pending ops)
    2. Export transcript to encrypted JSONL + sidecar
    3. Mark thread as archived
    """
    # 1. Run pending ops consolidation (best-effort)
    try:
        from anima_server.services.agent.consolidation import consolidate_pending_ops

        if soul_db_factory is not None and runtime_db_factory is not None:
            await consolidate_pending_ops(
                user_id=user_id,
                soul_db_factory=soul_db_factory,
                runtime_db_factory=runtime_db_factory,
            )
    except Exception:
        logger.warning(
            "Pending ops consolidation failed for thread %d", thread_id, exc_info=True
        )

    # 2. Export transcript
    dek = get_active_dek(user_id, "conversations")
    if runtime_db_factory is not None:
        db = runtime_db_factory()
    else:
        from anima_server.db.runtime import get_runtime_session

        db = get_runtime_session()

    try:
        messages = list_transcript_messages(db, thread_id=thread_id)
        if messages:
            msg_dicts = messages_to_transcript_dicts(messages)
            transcripts_dir = _get_transcripts_dir()

            if dek is not None:
                export_transcript(
                    messages=msg_dicts,
                    thread_id=thread_id,
                    user_id=user_id,
                    dek=dek,
                    transcripts_dir=transcripts_dir,
                )
                logger.info(
                    "Exported transcript for thread %d (%d messages)", thread_id, len(messages)
                )
            else:
                logger.info(
                    "Skipping transcript encryption for thread %d (no DEK)", thread_id
                )

        # 3. Mark thread as archived
        thread = db.get(RuntimeThread, thread_id)
        if thread is not None:
            thread.is_archived = True
            db.commit()
    except Exception:
        logger.exception("Transcript export failed for thread %d", thread_id)
        db.rollback()
    finally:
        if runtime_db_factory is not None:
            db.close()


async def inactivity_sweep(
    *,
    runtime_db_factory: Callable[..., Session] | None = None,
    soul_db_factory: Callable[..., object] | None = None,
    inactivity_minutes: int = 5,
) -> int:
    """Close threads that have been idle beyond the inactivity threshold.

    Returns the number of threads closed.
    """
    cutoff = datetime.now(UTC) - timedelta(minutes=inactivity_minutes)

    if runtime_db_factory is not None:
        db = runtime_db_factory()
    else:
        from anima_server.db.runtime import get_runtime_session

        db = get_runtime_session()

    try:
        stale_threads = db.scalars(
            select(RuntimeThread).where(
                RuntimeThread.status == "active",
                RuntimeThread.last_message_at.isnot(None),
                RuntimeThread.last_message_at < cutoff,
            )
        ).all()

        for thread in stale_threads:
            thread.status = "closed"
            thread.closed_at = datetime.now(UTC)

        db.commit()

        closed_count = len(stale_threads)
        if closed_count > 0:
            logger.info("Inactivity sweep closed %d threads", closed_count)

        # Fire background consolidation for each closed thread
        for thread in stale_threads:
            try:
                await on_thread_close(
                    thread_id=thread.id,
                    user_id=thread.user_id,
                    runtime_db_factory=runtime_db_factory,
                    soul_db_factory=soul_db_factory,
                )
            except Exception:
                logger.warning(
                    "Failed to consolidate thread %d after inactivity close",
                    thread.id,
                    exc_info=True,
                )

        return closed_count
    except Exception:
        logger.exception("Inactivity sweep failed")
        db.rollback()
        return 0
    finally:
        if runtime_db_factory is not None:
            db.close()


async def prune_expired_messages(
    *,
    runtime_db_factory: Callable[..., Session] | None = None,
) -> int:
    """Delete messages from archived threads older than message_ttl_days.

    Returns count of deleted messages.
    """
    if settings.message_ttl_days <= 0:
        return 0

    cutoff = datetime.now(UTC) - timedelta(days=settings.message_ttl_days)

    if runtime_db_factory is not None:
        db = runtime_db_factory()
    else:
        from anima_server.db.runtime import get_runtime_session

        db = get_runtime_session()

    try:
        # Only prune messages from archived threads
        archived_thread_ids = db.scalars(
            select(RuntimeThread.id).where(RuntimeThread.is_archived.is_(True))
        ).all()

        if not archived_thread_ids:
            return 0

        result = db.execute(
            delete(RuntimeMessage).where(
                RuntimeMessage.created_at < cutoff,
                RuntimeMessage.thread_id.in_(archived_thread_ids),
            )
        )
        db.commit()
        count = result.rowcount
        if count > 0:
            logger.info("Pruned %d expired messages from archived threads", count)
        return count
    except Exception:
        logger.exception("Message pruning failed")
        db.rollback()
        return 0
    finally:
        if runtime_db_factory is not None:
            db.close()


async def prune_expired_transcripts() -> int:
    """Delete transcript files older than transcript_retention_days.

    Returns count of deleted transcript pairs.
    """
    if settings.transcript_retention_days < 0:
        return 0  # -1 = keep forever

    transcripts_dir = _get_transcripts_dir()
    if not transcripts_dir.exists():
        return 0

    import json

    cutoff = datetime.now(UTC) - timedelta(days=settings.transcript_retention_days)
    deleted = 0

    for meta_path in list(transcripts_dir.glob("*.meta.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            archived_at_str = meta.get("archived_at", "")
            archived_at = datetime.fromisoformat(archived_at_str.replace("Z", "+00:00"))

            if archived_at < cutoff:
                enc_name = meta_path.name.replace(".meta.json", ".jsonl.enc")
                enc_path = meta_path.parent / enc_name
                if enc_path.exists():
                    enc_path.unlink()
                meta_path.unlink()
                deleted += 1
        except (json.JSONDecodeError, ValueError, OSError):
            continue

    if deleted > 0:
        logger.info("Pruned %d expired transcript files", deleted)
    return deleted
```

- [ ] **Step 5: Implement thread close endpoint**

```python
# apps/server/src/anima_server/api/routes/threads.py
"""Thread management endpoints."""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_user
from anima_server.db import get_runtime_db
from anima_server.models.runtime import RuntimeThread
from anima_server.services.agent.persistence import close_thread

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/threads", tags=["threads"])


@router.post("/{thread_id}/close")
async def close_thread_endpoint(
    thread_id: int,
    request: Request,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict:
    """Close a thread and trigger eager consolidation."""
    thread = runtime_db.get(RuntimeThread, thread_id)
    if thread is None:
        raise HTTPException(404, "Thread not found")

    require_unlocked_user(request, thread.user_id)

    if thread.status == "closed":
        return {"status": "already_closed", "thread_id": thread_id}

    changed = close_thread(runtime_db, thread_id=thread_id)
    runtime_db.commit()

    if changed:
        # Fire background consolidation (non-blocking)
        from anima_server.services.agent.eager_consolidation import on_thread_close

        loop = asyncio.get_running_loop()
        loop.create_task(
            on_thread_close(
                thread_id=thread_id,
                user_id=thread.user_id,
            )
        )

    return {"status": "closed", "thread_id": thread_id}
```

- [ ] **Step 6: Register threads router in main.py**

In `apps/server/src/anima_server/main.py`, add the import:

```python
from .api.routes.threads import router as threads_router
```

And register the router alongside the others:

```python
app.include_router(threads_router)
```

- [ ] **Step 7: Run tests**

Run: `cd apps/server && python -m pytest tests/test_p5_transcript_archive.py -k "TestThreadCloseEndpoint or TestEagerConsolidation" -v`
Expected: PASS

- [ ] **Step 8: Run full test suite**

Run: `cd apps/server && python -m pytest tests/ -x -q --timeout=60`
Expected: All pass

- [ ] **Step 9: Commit**

```bash
git add apps/server/src/anima_server/services/agent/eager_consolidation.py apps/server/src/anima_server/api/routes/threads.py apps/server/src/anima_server/main.py apps/server/tests/test_p5_transcript_archive.py
git commit -m "feat(p5): thread close endpoint + eager consolidation orchestrator"
```

---

## Task 8: Background Sweeps + Wire Reset to Close

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/service.py`
- Modify: `apps/server/src/anima_server/api/routes/chat.py`
- Modify: `apps/server/src/anima_server/main.py`
- Test: `apps/server/tests/test_p5_transcript_archive.py`

- [ ] **Step 1: Write tests for inactivity sweep and message pruning**

Add to `test_p5_transcript_archive.py`:

```python
class TestBackgroundSweeps:
    def test_inactivity_sweep_closes_stale_threads(self, runtime_db: Session):
        import asyncio
        from datetime import timedelta
        from unittest.mock import AsyncMock, patch

        from anima_server.models.runtime import RuntimeThread
        from anima_server.services.agent.eager_consolidation import inactivity_sweep

        # Create a thread with old last_message_at
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
            count = asyncio.get_event_loop().run_until_complete(
                inactivity_sweep(
                    runtime_db_factory=lambda: runtime_db,
                    inactivity_minutes=5,
                )
            )

        assert count == 1
        refreshed = runtime_db.get(RuntimeThread, thread.id)
        assert refreshed.status == "closed"

    def test_inactivity_sweep_skips_recent_threads(self, runtime_db: Session):
        import asyncio
        from unittest.mock import AsyncMock, patch

        from anima_server.models.runtime import RuntimeThread
        from anima_server.services.agent.eager_consolidation import inactivity_sweep

        thread = RuntimeThread(
            user_id=1,
            status="active",
            last_message_at=datetime.now(UTC),  # just now
        )
        runtime_db.add(thread)
        runtime_db.commit()

        with patch(
            "anima_server.services.agent.eager_consolidation.on_thread_close",
            new_callable=AsyncMock,
        ):
            count = asyncio.get_event_loop().run_until_complete(
                inactivity_sweep(
                    runtime_db_factory=lambda: runtime_db,
                    inactivity_minutes=5,
                )
            )

        assert count == 0

    def test_prune_only_archived_messages(self, runtime_db: Session):
        import asyncio
        from datetime import timedelta
        from unittest.mock import patch

        from anima_server.models.runtime import RuntimeMessage, RuntimeThread
        from anima_server.services.agent.eager_consolidation import prune_expired_messages

        # Archived thread with old messages
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

        # Active thread with old messages (should NOT be pruned)
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

            count = asyncio.get_event_loop().run_until_complete(
                prune_expired_messages(runtime_db_factory=lambda: runtime_db)
            )

        assert count == 1  # only the archived thread's message

    def test_transcript_retention_forever(self, transcripts_dir: Path):
        import asyncio
        from unittest.mock import patch

        from anima_server.services.agent.eager_consolidation import prune_expired_transcripts

        # Create a test transcript file
        (transcripts_dir / "2025-01-01_thread-1.jsonl.enc").write_bytes(b"data")
        (transcripts_dir / "2025-01-01_thread-1.meta.json").write_text(
            '{"archived_at": "2025-01-01T00:00:00+00:00"}'
        )

        with patch("anima_server.services.agent.eager_consolidation.settings") as mock_settings:
            mock_settings.transcript_retention_days = -1
            mock_settings.data_dir = transcripts_dir.parent

            count = asyncio.get_event_loop().run_until_complete(prune_expired_transcripts())

        assert count == 0  # -1 means keep forever
        assert (transcripts_dir / "2025-01-01_thread-1.jsonl.enc").exists()
```

- [ ] **Step 2: Run tests to verify they pass (implementation already done in Task 7)**

Run: `cd apps/server && python -m pytest tests/test_p5_transcript_archive.py::TestBackgroundSweeps -v`
Expected: PASS (sweep functions were implemented in Task 7)

- [ ] **Step 3: Wire reset_agent_thread to close instead of delete**

In `apps/server/src/anima_server/services/agent/service.py`, update `reset_agent_thread()`:

```python
async def reset_agent_thread(user_id: int, runtime_db: Session) -> None:
    """Close the current thread and create a new one.

    Replaces the old destructive delete with close-and-archive.
    """
    from anima_server.services.agent.persistence import close_thread, get_or_create_thread

    # Find active thread
    thread = get_or_create_thread(runtime_db, user_id)
    thread_id = thread.id

    # Close it (preserves messages for archival)
    close_thread(runtime_db, thread_id=thread_id)
    runtime_db.commit()

    # Fire background consolidation (non-blocking)
    import asyncio

    from anima_server.services.agent.eager_consolidation import on_thread_close

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(
            on_thread_close(
                thread_id=thread_id,
                user_id=user_id,
            )
        )
    except RuntimeError:
        pass  # No event loop (e.g., in tests)

    # Reset companion state
    companion = get_companion(user_id)
    if companion is not None:
        companion.reset()
```

- [ ] **Step 4: Register periodic sweeps in main.py lifespan**

In `apps/server/src/anima_server/main.py`, add periodic sweep tasks in the lifespan:

```python
@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    # ... existing startup code ...

    # Start periodic sweeps
    sweep_tasks: list[asyncio.Task] = []
    try:
        import asyncio

        async def _periodic_inactivity_sweep():
            while True:
                await asyncio.sleep(60)  # every 60 seconds
                try:
                    from .services.agent.eager_consolidation import inactivity_sweep
                    await inactivity_sweep()
                except Exception:
                    logger.warning("Inactivity sweep error", exc_info=True)

        async def _periodic_prune_sweep():
            while True:
                await asyncio.sleep(6 * 3600)  # every 6 hours
                try:
                    from .services.agent.eager_consolidation import (
                        prune_expired_messages,
                        prune_expired_transcripts,
                    )
                    await prune_expired_messages()
                    await prune_expired_transcripts()
                except Exception:
                    logger.warning("Prune sweep error", exc_info=True)

        sweep_tasks.append(asyncio.create_task(_periodic_inactivity_sweep()))
        sweep_tasks.append(asyncio.create_task(_periodic_prune_sweep()))

        yield
    finally:
        for task in sweep_tasks:
            task.cancel()
        # ... existing shutdown code ...
```

- [ ] **Step 5: Run full test suite**

Run: `cd apps/server && python -m pytest tests/ -x -q --timeout=60`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add apps/server/src/anima_server/services/agent/service.py apps/server/src/anima_server/api/routes/chat.py apps/server/src/anima_server/main.py apps/server/tests/test_p5_transcript_archive.py
git commit -m "feat(p5): wire reset to close, register periodic sweeps"
```

---

## Task 9: Alembic Migrations

**Files:**
- Create: `apps/server/alembic_core/versions/20260328_0001_p5_transcript_ref.py`

- [ ] **Step 1: Write soul Alembic migration for MemoryEpisode.transcript_ref**

```python
# apps/server/alembic_core/versions/20260328_0001_p5_transcript_ref.py
"""P5: Add transcript_ref to memory_episodes.

Revision ID: 20260328_0001
Revises: 20260327_0002
Create Date: 2026-03-28
"""
import sqlalchemy as sa
from alembic import op

revision = "20260328_0001"
down_revision = "20260327_0002"  # Update to match actual latest revision
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("memory_episodes") as batch_op:
        batch_op.add_column(
            sa.Column("transcript_ref", sa.String(255), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("memory_episodes") as batch_op:
        batch_op.drop_column("transcript_ref")
```

Note: The `down_revision` must be updated to match the actual latest migration. Run `ls apps/server/alembic_core/versions/` to find the current head.

The `RuntimeThread` schema changes (closed_at, is_archived, unique constraint removal) do NOT need an Alembic migration because the runtime database is ephemeral — `RuntimeBase.metadata.create_all()` recreates it from the model definitions at startup.

- [ ] **Step 2: Verify migration syntax**

Run: `cd apps/server && python -c "exec(open('alembic_core/versions/20260328_0001_p5_transcript_ref.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add apps/server/alembic_core/versions/20260328_0001_p5_transcript_ref.py
git commit -m "feat(p5): Alembic migration — add transcript_ref to memory_episodes"
```

---

## Task 10: System Prompt Update

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/system_prompt.py` (or wherever the memory tier description lives)

- [ ] **Step 1: Find and update the memory tier description**

Search for the memory tier description in the system prompt templates. Add `recall_transcript` to the available tools description:

```
You have different levels of memory:
- Your core memories and feelings are always with you (you just know them)
- For recent conversations, use recall_conversation to search what was discussed
- For exact wording from past conversations, use recall_transcript
  Think of this like finding a specific page in a diary — you check the
  dates and topics first, then read the exact passage you need
```

The exact file and location depends on the template system. Look in persona templates under `apps/server/src/anima_server/services/agent/system_prompt.py` or persona template files.

- [ ] **Step 2: Run full test suite**

Run: `cd apps/server && python -m pytest tests/ -x -q --timeout=60`
Expected: All pass

- [ ] **Step 3: Commit**

```bash
git add apps/server/src/anima_server/services/agent/system_prompt.py
git commit -m "feat(p5): add recall_transcript to system prompt memory tier description"
```

---

## Task 11: Final Verification

- [ ] **Step 1: Run the complete test suite**

Run: `cd apps/server && python -m pytest tests/ -v --timeout=120`
Expected: All tests pass, no regressions

- [ ] **Step 2: Verify acceptance criteria**

Manually verify against the PRD acceptance criteria:

1. Thread close creates transcript (encrypted `.jsonl.enc` + `.meta.json` sidecar)
2. Transcript contains all conversation messages (minus system/approval)
3. Encryption uses existing `conversations` domain DEK via AES-256-GCM
4. Sidecar enables date and keyword filtering
5. `recall_transcript` returns relevant snippets with context
6. Messages pruned after TTL from archived threads only
7. Inactivity fallback closes idle threads
8. Atomic writes prevent corruption (tmp + rename pattern)
9. No test regressions
10. Graceful degradation without DEK (skip encryption, log warning)

- [ ] **Step 3: Check for any TODO items or missing imports**

Run: `cd apps/server && grep -rn "TODO\|FIXME\|XXX" src/anima_server/services/agent/transcript_archive.py src/anima_server/services/agent/transcript_search.py src/anima_server/services/agent/eager_consolidation.py src/anima_server/api/routes/threads.py`
Expected: No unresolved TODOs

- [ ] **Step 4: Verify git status is clean**

Run: `git status`
Expected: All changes committed
