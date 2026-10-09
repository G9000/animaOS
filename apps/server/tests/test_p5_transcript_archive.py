"""Retained Runtime projection and encrypted blob primitive regressions."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from anima_server.db.base import Base
from anima_server.db.runtime_base import RuntimeBase
from anima_server.models import runtime as _runtime_models  # noqa: F401
from cryptography.exceptions import InvalidTag
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

TRANSCRIPT_TEST_DAYS_BACK = 10_000


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
    from anima_server.models import SoulOwner

    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    session.add(SoulOwner(id=1, core_owner_id="owner"))
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

        threads = runtime_db.scalars(select(RuntimeThread).where(RuntimeThread.user_id == 1)).all()
        assert len(threads) == 2

    def test_multiple_active_threads_per_user_allowed(self, runtime_db: Session) -> None:
        from anima_server.models.runtime import RuntimeThread

        runtime_db.add(RuntimeThread(user_id=1, status="active"))
        runtime_db.flush()

        runtime_db.add(RuntimeThread(user_id=1, status="active"))
        runtime_db.flush()

        threads = runtime_db.scalars(
            select(RuntimeThread).where(
                RuntimeThread.user_id == 1,
                RuntimeThread.status == "active",
            )
        ).all()
        assert len(threads) == 2


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

        loaded = soul_db.scalar(select(MemoryEpisode).where(MemoryEpisode.id == episode.id))
        assert loaded is not None
        assert loaded.transcript_ref == "2026-03-28_thread-14.jsonl.enc"


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
