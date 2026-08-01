"""ARH-010: crash-durable per-turn memory extraction.

Extraction used to commit nothing until after the LLM call: a process
kill, shutdown cancellation, or post-LLM exception dropped the whole
turn — including the regex candidates computed before the LLM ran — and
held a DB session across the multi-second await.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from anima_server.db.runtime_base import RuntimeBase
from anima_server.models.runtime_memory import MemoryCandidate, MemoryExtractionFailure
from anima_server.services.agent import consolidation
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture()
def rt_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    RuntimeBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture()
def _llm_provider(monkeypatch: pytest.MonkeyPatch):
    from anima_server.config import settings

    monkeypatch.setattr(settings, "agent_provider", "openai")


def _fake_regex_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        consolidation,
        "extract_turn_memory",
        lambda user_message: SimpleNamespace(
            facts=("Works as a gardener",),
            preferences=(),
            current_focus=None,
        ),
    )


def _llm_success() -> SimpleNamespace:
    return SimpleNamespace(
        failed=False,
        error=None,
        memories=[{"content": "Likes green tea", "category": "preference", "importance": 3}],
        profile_updates=[],
        foresight=None,
        emotion=None,
    )


@pytest.mark.asyncio
async def test_cancellation_during_llm_keeps_pre_llm_work(
    rt_factory, _llm_provider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shutdown-cancel during the LLM await must leave the regex candidates
    and a retryable intent row committed."""
    _fake_regex_extraction(monkeypatch)

    async def cancelled_llm(**kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(consolidation, "extract_memories_via_llm", cancelled_llm)

    with pytest.raises(asyncio.CancelledError):
        await consolidation.run_background_extraction(
            user_id=1,
            user_message="I work as a gardener",
            assistant_response="Noted!",
            runtime_db_factory=rt_factory,
            trigger_soul_writer=False,
            source_message_ids=[11, 12],
        )

    with rt_factory() as rt_db:
        candidates = rt_db.scalars(select(MemoryCandidate)).all()
        intent = rt_db.scalar(select(MemoryExtractionFailure))

    assert any(c.source == "regex" for c in candidates)
    assert intent is not None
    # The guard is left "pending" (not "failed"): while the LLM call was in
    # flight it must not be immediately retryable.  A crash leaves it stale for
    # the Soul Writer sweep to recover.
    assert intent.status == "pending"
    assert "crash-recovery guard" in intent.failure_reason
    assert intent.source_message_ids == [11, 12]


@pytest.mark.asyncio
async def test_successful_extraction_resolves_the_intent_row(
    rt_factory, _llm_provider, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_regex_extraction(monkeypatch)

    async def ok_llm(**kwargs):
        return _llm_success()

    monkeypatch.setattr(consolidation, "extract_memories_via_llm", ok_llm)

    await consolidation.run_background_extraction(
        user_id=1,
        user_message="I work as a gardener",
        assistant_response="Noted!",
        runtime_db_factory=rt_factory,
        trigger_soul_writer=False,
    )

    with rt_factory() as rt_db:
        candidates = rt_db.scalars(select(MemoryCandidate)).all()
        intent = rt_db.scalar(select(MemoryExtractionFailure))

    sources = {c.source for c in candidates}
    assert sources == {"regex", "llm"}
    assert intent is not None
    assert intent.status == "resolved"
    assert intent.resolved_at is not None


@pytest.mark.asyncio
async def test_llm_failure_keeps_intent_for_soul_writer_retry(
    rt_factory, _llm_provider, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_regex_extraction(monkeypatch)

    async def failing_llm(**kwargs):
        return SimpleNamespace(
            failed=True,
            error="provider exploded",
            memories=[],
            profile_updates=[],
            foresight=None,
            emotion=None,
        )

    monkeypatch.setattr(consolidation, "extract_memories_via_llm", failing_llm)

    await consolidation.run_background_extraction(
        user_id=1,
        user_message="I work as a gardener",
        assistant_response="Noted!",
        runtime_db_factory=rt_factory,
        trigger_soul_writer=False,
    )

    with rt_factory() as rt_db:
        intent = rt_db.scalar(select(MemoryExtractionFailure))

    assert intent is not None
    assert intent.status == "failed"
    assert intent.failure_reason == "provider exploded"


@pytest.mark.asyncio
async def test_llm_failure_reseals_provider_reason(
    rt_factory,
    _llm_provider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.models.corefs_runtime import CoreFSRuntimeBinding
    from anima_server.services.corefs import sealed_runtime
    from anima_server.services.corefs.indexer import CoreFSProgressiveIndex
    from anima_server.services.corefs.runtime_sealing import RuntimeSealingLocked

    _fake_regex_extraction(monkeypatch)
    index = CoreFSProgressiveIndex("core-a")
    index.unlock(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")
    monkeypatch.setattr(
        sealed_runtime,
        "_active_runtime_index",
        lambda _user_id: index,
    )
    private_reason = "Provider leaked /private/logical/extraction-response.pdf"

    with rt_factory() as runtime_db:
        runtime_db.add(
            CoreFSRuntimeBinding(
                binding_slot=1,
                core_id="core-a",
                local_instance_id="instance-a",
            )
        )
        runtime_db.commit()

    async def failing_llm(**kwargs):
        return SimpleNamespace(
            failed=True,
            error=private_reason,
            memories=[],
            profile_updates=[],
            foresight=None,
            emotion=None,
        )

    monkeypatch.setattr(consolidation, "extract_memories_via_llm", failing_llm)

    await consolidation.run_background_extraction(
        user_id=1,
        user_message="I work as a gardener",
        assistant_response="Noted!",
        runtime_db_factory=rt_factory,
        trigger_soul_writer=False,
    )

    with rt_factory() as runtime_db:
        raw_reason = runtime_db.scalar(
            select(MemoryExtractionFailure.__table__.c.failure_reason)
        )
        runtime_db.expunge_all()
        intent = runtime_db.scalar(select(MemoryExtractionFailure))

    assert raw_reason == ""
    assert intent is not None
    assert intent.failure_reason == private_reason

    index.clear_unlocked_state()
    with rt_factory() as runtime_db, pytest.raises(RuntimeSealingLocked):
        runtime_db.scalar(select(MemoryExtractionFailure))


@pytest.mark.asyncio
async def test_scaffold_provider_writes_no_intent_row(
    rt_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from anima_server.config import settings

    monkeypatch.setattr(settings, "agent_provider", "scaffold")
    _fake_regex_extraction(monkeypatch)

    await consolidation.run_background_extraction(
        user_id=1,
        user_message="I work as a gardener",
        assistant_response="Noted!",
        runtime_db_factory=rt_factory,
        trigger_soul_writer=False,
    )

    with rt_factory() as rt_db:
        candidates = rt_db.scalars(select(MemoryCandidate)).all()
        intent = rt_db.scalar(select(MemoryExtractionFailure))

    assert any(c.source == "regex" for c in candidates)
    assert intent is None
