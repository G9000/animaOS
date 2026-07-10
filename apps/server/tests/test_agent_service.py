from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncGenerator, Generator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pytest
from anima_server.db.base import Base
from anima_server.models import User
from anima_server.models.runtime import (
    RuntimeImageAnnotation,
    RuntimeImageAsset,
    RuntimeImageMessageLink,
    RuntimeMessage,
    RuntimeRun,
    RuntimeStep,
    RuntimeThread,
)
from anima_server.models.runtime_embedding import RuntimeEmbedding
from anima_server.schemas.chat import ChatRequestAttachment
from anima_server.services.agent import list_agent_history, run_agent
from anima_server.services.agent import service as agent_service
from anima_server.services.agent.client_actions import ActionToolConnection, action_registry
from anima_server.services.agent.compaction import compact_thread_context
from anima_server.services.agent.evidence_retrieval import RetrievalMode, WideEvidenceResult
from anima_server.services.agent.memory_blocks import MemoryBlock
from anima_server.services.agent.persistence import (
    append_message,
    cancel_run,
    create_run,
    persist_agent_result,
)
from anima_server.services.agent.prompt_budget import (
    PromptBudgetBlockDecision,
    PromptBudgetTrace,
)
from anima_server.services.agent.runtime_types import StepTrace, ToolExecutionResult
from anima_server.services.agent.state import AgentResult
from anima_server.services.documents.rag import DocumentRagResult
from conftest_runtime import runtime_db_session
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


class FailingThenReplyRunner:
    def __init__(self) -> None:
        self.calls = 0

    async def invoke(self, *args, **kwargs) -> AgentResult:
        del args, kwargs
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("boom")
        return AgentResult(
            response="Recovered reply.",
            model="test-model",
            provider="test-provider",
            stop_reason="end_turn",
            step_traces=[StepTrace(step_index=0, assistant_text="Recovered reply.")],
        )


class RecordingRunner:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    async def invoke(
        self,
        user_message: str,
        user_id: int,
        history: list[agent_service.StoredMessage],
        **kwargs: object,
    ) -> AgentResult:
        self.requests.append(
            {
                "user_message": user_message,
                "user_id": user_id,
                "history": [
                    (message.role, message.content, message.tool_name, message.tool_call_id)
                    for message in history
                ],
                "extra_tool_schemas": kwargs.get("extra_tool_schemas"),
                "tool_executor": kwargs.get("tool_executor"),
                "memory_blocks": [
                    (block.label, block.value)
                    for block in kwargs.get("memory_blocks", ())
                    if isinstance(block, MemoryBlock)
                ],
            }
        )
        reply = f"Reply to: {user_message}"
        return AgentResult(
            response=reply,
            model="test-model",
            provider="test-provider",
            stop_reason="end_turn",
            step_traces=[StepTrace(step_index=0, assistant_text=reply)],
        )


class BlockingRunner:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def invoke(self, *args, **kwargs) -> AgentResult:
        del args, kwargs
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("blocking runner should be cancelled")


class FakeWebSocket:
    async def send_json(self, message: dict[str, object]) -> None:
        del message


PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde"
)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _embedding(x: float, y: float = 0.0) -> list[float]:
    return [x, y, *([0.0] * 766)]


@contextmanager
def _soul_db_session() -> Generator[Session, None, None]:
    """Soul DB session (for User model)."""
    engine: Engine = create_engine(
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
    Base.metadata.create_all(bind=engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.mark.asyncio
async def test_prepare_turn_context_deletes_attachment_files_on_persistence_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeCompanion:
        thread_id: int | None = None

        def invalidate_history(self, *, thread_id: int) -> None:
            del thread_id

        def ensure_history_loaded(
            self,
            runtime_db: Session,
            *,
            thread_id: int,
        ) -> list[agent_service.StoredMessage]:
            del runtime_db, thread_id
            return []

    def fail_create_run(*args: object, **kwargs: object) -> RuntimeRun:
        del args, kwargs
        raise RuntimeError("runtime insert failed")

    monkeypatch.setattr(agent_service.settings, "data_dir", tmp_path)
    monkeypatch.setattr(agent_service.settings, "agent_provider", "openai")
    monkeypatch.setattr(agent_service.settings, "agent_model", "gpt-4o-mini")
    monkeypatch.setattr(agent_service, "_get_companion", lambda user_id: FakeCompanion())
    monkeypatch.setattr(agent_service, "create_run", fail_create_run)

    attachment = ChatRequestAttachment(
        kind="image",
        filename="pixel.png",
        mimeType="image/png",
        data=_b64(PNG_BYTES),
    )

    with (
        _soul_db_session() as soul_session,
        runtime_db_session() as runtime_session,
        pytest.raises(RuntimeError, match="runtime insert failed"),
    ):
        await agent_service._prepare_turn_context(
            "look",
            1,
            soul_session,
            runtime_session,
            attachments=[attachment],
        )

    attachment_files = [
        path for path in (tmp_path / "users").rglob("*") if path.is_file()
    ]
    assert attachment_files == []


@pytest.mark.asyncio
async def test_failed_turn_retry_keeps_history_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = FailingThenReplyRunner()
    monkeypatch.setattr(agent_service, "get_or_build_runner", lambda: runner)
    monkeypatch.setattr(agent_service, "_run_post_turn_hooks", lambda **kwargs: None)

    with _soul_db_session() as soul_session, runtime_db_session() as runtime_session:
        user = User(
            username="retry-me",
            password_hash="not-used",
            display_name="Retry Me",
        )
        soul_session.add(user)
        soul_session.commit()

        with pytest.raises(RuntimeError, match="boom"):
            await run_agent("first attempt", user.id, soul_session, runtime_session)

        result = await run_agent("second attempt", user.id, soul_session, runtime_session)

        thread = runtime_session.query(RuntimeThread).one()
        runs = runtime_session.query(RuntimeRun).order_by(RuntimeRun.id).all()
        messages = runtime_session.query(RuntimeMessage).order_by(RuntimeMessage.sequence_id).all()
        history = list_agent_history(user.id, runtime_session, limit=10)

    assert result.response == "Recovered reply."
    assert [run.status for run in runs] == ["failed", "completed"]
    assert [message.sequence_id for message in messages] == [1, 2, 3]
    assert messages[0].content_text == "first attempt"
    assert messages[0].is_in_context is False
    assert messages[1].content_text == "second attempt"
    assert messages[1].is_in_context is True
    assert messages[2].content_text == "Recovered reply."
    assert messages[2].is_in_context is True
    assert thread.next_message_sequence == 4
    assert [message.content_text for message in history] == [
        "second attempt",
        "Recovered reply.",
    ]


@pytest.mark.asyncio
async def test_run_agent_passes_only_prior_turns_in_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_service.invalidate_agent_runtime_cache()
    runner = RecordingRunner()
    monkeypatch.setattr(agent_service, "get_or_build_runner", lambda: runner)
    monkeypatch.setattr(agent_service, "_run_post_turn_hooks", lambda **kwargs: None)

    try:
        with _soul_db_session() as soul_session, runtime_db_session() as runtime_session:
            user = User(
                username="history-check",
                password_hash="not-used",
                display_name="History Check",
            )
            soul_session.add(user)
            soul_session.commit()

            await run_agent("first turn", user.id, soul_session, runtime_session)
            await run_agent("second turn", user.id, soul_session, runtime_session)
    finally:
        agent_service.invalidate_agent_runtime_cache()

    assert runner.requests[0]["user_message"] == "first turn"
    assert runner.requests[0]["history"] == []
    assert runner.requests[1]["user_message"] == "second turn"
    assert runner.requests[1]["history"] == [
        ("user", "first turn", None, None),
        ("assistant", "Reply to: first turn", None, None),
    ]


@pytest.mark.asyncio
async def test_run_agent_includes_home_greeting_context_in_current_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_service.invalidate_agent_runtime_cache()
    runner = RecordingRunner()
    monkeypatch.setattr(agent_service, "get_or_build_runner", lambda: runner)
    monkeypatch.setattr(agent_service, "_run_post_turn_hooks", lambda **kwargs: None)

    try:
        with _soul_db_session() as soul_session, runtime_db_session() as runtime_session:
            user = User(
                username="home-greeting",
                password_hash="not-used",
                display_name="Home Greeting",
            )
            soul_session.add(user)
            soul_session.commit()

            await run_agent(
                "That sounds right.",
                user.id,
                soul_session,
                runtime_session,
                context_messages=[
                    agent_service.ChatContextMessage(
                        role="assistant",
                        content="Hello there. I hope you and Tappy are having a peaceful start.",
                        source="home_greeting",
                    )
                ],
            )

            messages = (
                runtime_session.query(RuntimeMessage)
                .order_by(RuntimeMessage.sequence_id)
                .all()
            )
            thread = runtime_session.query(RuntimeThread).one()
    finally:
        agent_service.invalidate_agent_runtime_cache()

    assert runner.requests[0]["user_message"] == "That sounds right."
    assert runner.requests[0]["history"] == [
        (
            "assistant",
            "Hello there. I hope you and Tappy are having a peaceful start.",
            None,
            None,
        )
    ]
    assert [(message.role, message.content_text, message.source) for message in messages] == [
        (
            "assistant",
            "Hello there. I hope you and Tappy are having a peaceful start.",
            "home_greeting",
        ),
        ("user", "That sounds right.", None),
        ("assistant", "Reply to: That sounds right.", None),
    ]
    assert [message.sequence_id for message in messages] == [1, 2, 3]
    assert thread.next_message_sequence == 4


@pytest.mark.asyncio
async def test_run_agent_persists_context_message_pills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provenance pills on a context message survive into content_json and
    surface through the thread-display path."""
    from anima_server.services.agent.state import extract_stored_pills
    from anima_server.services.agent.thread_manager import (
        get_thread_messages_for_display,
    )

    agent_service.invalidate_agent_runtime_cache()
    runner = RecordingRunner()
    monkeypatch.setattr(agent_service, "get_or_build_runner", lambda: runner)
    monkeypatch.setattr(agent_service, "_run_post_turn_hooks", lambda **kwargs: None)

    try:
        with _soul_db_session() as soul_session, runtime_db_session() as runtime_session:
            user = User(
                username="thought-pills",
                password_hash="not-used",
                display_name="Thought Pills",
            )
            soul_session.add(user)
            soul_session.commit()

            await run_agent(
                "Let's talk about this.",
                user.id,
                soul_session,
                runtime_session,
                context_messages=[
                    agent_service.ChatContextMessage(
                        role="assistant",
                        content="That trip a year ago still feels like dreamland.",
                        source="home_greeting",
                        pills=[
                            {"kind": "brief", "label": "DAILY BRIEF"},
                            {"kind": "emotion", "label": "WISTFUL"},
                        ],
                    )
                ],
            )

            context_message = (
                runtime_session.query(RuntimeMessage)
                .order_by(RuntimeMessage.sequence_id)
                .first()
            )
            thread = runtime_session.query(RuntimeThread).one()
            display = get_thread_messages_for_display(
                runtime_session,
                thread=thread,
                user_id=user.id,
                transcripts_dir=None,
                dek=None,
            )
    finally:
        agent_service.invalidate_agent_runtime_cache()

    assert extract_stored_pills(context_message.content_json) == [
        {"kind": "brief", "label": "DAILY BRIEF", "ref": None},
        {"kind": "emotion", "label": "WISTFUL", "ref": None},
    ]
    assert display[0]["pills"] == [
        {"kind": "brief", "label": "DAILY BRIEF", "ref": None},
        {"kind": "emotion", "label": "WISTFUL", "ref": None},
    ]


@pytest.mark.asyncio
async def test_run_agent_includes_today_context_without_persisting_or_caching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_service.invalidate_agent_runtime_cache()
    runner = RecordingRunner()
    monkeypatch.setattr(agent_service, "get_or_build_runner", lambda: runner)
    monkeypatch.setattr(agent_service, "_run_post_turn_hooks", lambda **kwargs: None)

    try:
        with _soul_db_session() as soul_session, runtime_db_session() as runtime_session:
            user = User(
                username="today-context",
                password_hash="not-used",
                display_name="Today Context",
            )
            soul_session.add(user)
            soul_session.commit()

            today = date.today().isoformat()
            await run_agent(
                "Can you help me plan this?",
                user.id,
                soul_session,
                runtime_session,
                today_context=agent_service.TodayContext(
                    date=today,
                    mood="tired",
                    energy="low",
                    note="keep replies direct",
                ),
            )

            messages = (
                runtime_session.query(RuntimeMessage)
                .order_by(RuntimeMessage.sequence_id)
                .all()
            )
            companion = agent_service.get_companion(user.id)
    finally:
        agent_service.invalidate_agent_runtime_cache()

    blocks = runner.requests[0]["memory_blocks"]
    today_blocks = [block for block in blocks if block[0] == "today_user_context"]
    assert len(today_blocks) == 1
    assert "Mood: tired" in today_blocks[0][1]
    assert "Energy: low" in today_blocks[0][1]
    assert "Note: keep replies direct" in today_blocks[0][1]
    assert [(message.role, message.content_text) for message in messages] == [
        ("user", "Can you help me plan this?"),
        ("assistant", "Reply to: Can you help me plan this?"),
    ]
    assert companion is not None
    cached = companion.get_cached_memory_blocks() or ()
    assert all(block.label != "today_user_context" for block in cached)


def test_today_context_block_guides_adaptive_checkins() -> None:
    block = agent_service._build_today_context_block(
        agent_service.TodayContext(
            date=date.today().isoformat(),
            mood="overwhelmed",
            energy="low",
            note="keep it small",
        )
    )

    assert block is not None
    assert block.read_only is True
    assert "Mood: overwhelmed" in block.value
    assert "Energy: low" in block.value
    assert "Note: keep it small" in block.value
    assert "gently ask" in block.description
    assert "not every turn" in block.description
    assert "Do not store" in block.description


@pytest.mark.asyncio
async def test_run_agent_adds_document_priority_block_for_mixed_pdf_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_service.invalidate_agent_runtime_cache()
    runner = RecordingRunner()
    monkeypatch.setattr(agent_service, "get_or_build_runner", lambda: runner)
    monkeypatch.setattr(agent_service, "_run_post_turn_hooks", lambda **kwargs: None)

    def fake_search_document_chunks(
        runtime_db: object,
        user_id: int,
        query: str,
        *,
        document_ids: list[int],
        limit: int,
    ) -> list[DocumentRagResult]:
        del runtime_db, user_id, query, document_ids, limit
        return [
            DocumentRagResult(
                chunk_id=12,
                document_id=4,
                filename="manual.pdf",
                content="Install the relay before enabling checkpoint restart.",
                similarity=0.91,
                page_start=2,
                page_end=3,
                section_title="Install",
            )
        ]

    monkeypatch.setattr(agent_service, "search_document_chunks", fake_search_document_chunks)

    try:
        with _soul_db_session() as soul_session, runtime_db_session() as runtime_session:
            user = User(
                username="document-priority",
                password_hash="not-used",
                display_name="Document Priority",
            )
            soul_session.add(user)
            soul_session.commit()

            await run_agent(
                "so what inside",
                user.id,
                soul_session,
                runtime_session,
                document_ids=[4],
            )
    finally:
        agent_service.invalidate_agent_runtime_cache()

    blocks = runner.requests[0]["memory_blocks"]
    labels = [label for label, _value in blocks]
    assert "document_context" in labels
    assert "user_directive" in labels
    directive_value = next(value for label, value in blocks if label == "user_directive")
    assert "selected PDF" in directive_value or "selected document" in directive_value
    assert "what do you see" in directive_value


@pytest.mark.asyncio
async def test_run_agent_omits_personal_memory_blocks_when_pdf_is_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_service.invalidate_agent_runtime_cache()
    runner = RecordingRunner()
    monkeypatch.setattr(agent_service, "get_or_build_runner", lambda: runner)
    monkeypatch.setattr(agent_service, "_run_post_turn_hooks", lambda **kwargs: None)

    def fake_turn_memory_blocks(*args: object, **kwargs: object) -> tuple[MemoryBlock, ...]:
        del args, kwargs
        return (
            MemoryBlock(
                label="relevant_memories",
                value="The user has platinum hair and galaxy star nails.",
                description="Query-ranked personal memories.",
            ),
            MemoryBlock(
                label="self_working_memory",
                value="Talk about how the user looks.",
                description="Working memory.",
            ),
        )

    def fake_search_document_chunks(
        runtime_db: object,
        user_id: int,
        query: str,
        *,
        document_ids: list[int],
        limit: int,
    ) -> list[DocumentRagResult]:
        del runtime_db, user_id, query, document_ids, limit
        return [
            DocumentRagResult(
                chunk_id=12,
                document_id=4,
                filename="CHCC 2026 Price List updated March.pdf",
                content="HRT blood panel runs around RM150-300.",
                similarity=0.91,
                page_start=2,
                page_end=2,
                section_title="Blood tests",
            )
        ]

    monkeypatch.setattr(agent_service, "build_turn_memory_blocks", fake_turn_memory_blocks)
    monkeypatch.setattr(agent_service, "search_document_chunks", fake_search_document_chunks)

    try:
        with _soul_db_session() as soul_session, runtime_db_session() as runtime_session:
            user = User(
                username="document-memory-scope",
                password_hash="not-used",
                display_name="Document Memory Scope",
            )
            soul_session.add(user)
            soul_session.commit()

            await run_agent(
                "so what do you see",
                user.id,
                soul_session,
                runtime_session,
                document_ids=[4],
            )
    finally:
        agent_service.invalidate_agent_runtime_cache()

    labels = [label for label, _value in runner.requests[0]["memory_blocks"]]
    assert "user_directive" in labels
    assert "document_context" in labels
    assert "human" not in labels
    assert "relevant_memories" not in labels
    assert "self_working_memory" not in labels


@pytest.mark.asyncio
async def test_run_agent_persists_document_attachment_and_citation_pills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent.state import extract_stored_pills
    from anima_server.services.agent.thread_manager import (
        get_thread_messages_for_display,
    )
    from anima_server.services.documents.models import DocumentRegistration
    from anima_server.services.documents.store import register_document

    agent_service.invalidate_agent_runtime_cache()
    runner = RecordingRunner()
    monkeypatch.setattr(agent_service, "get_or_build_runner", lambda: runner)
    monkeypatch.setattr(agent_service, "_run_post_turn_hooks", lambda **kwargs: None)

    def fake_search_document_chunks(
        runtime_db: object,
        user_id: int,
        query: str,
        *,
        document_ids: list[int],
        limit: int,
    ) -> list[DocumentRagResult]:
        del runtime_db, user_id, query, limit
        assert document_ids == [document_id]
        return [
            DocumentRagResult(
                chunk_id=12,
                document_id=document_id,
                filename="CHCC 2026 Price List updated March.pdf",
                content="HRT blood panel runs around RM150-300.",
                similarity=0.91,
                page_start=2,
                page_end=2,
                section_title="Blood tests",
            )
        ]

    monkeypatch.setattr(agent_service, "search_document_chunks", fake_search_document_chunks)

    try:
        with _soul_db_session() as soul_session, runtime_db_session() as runtime_session:
            user = User(
                username="document-pills",
                password_hash="not-used",
                display_name="Document Pills",
            )
            soul_session.add(user)
            soul_session.commit()

            registered_document = register_document(
                runtime_session,
                DocumentRegistration(
                    user_id=user.id,
                    thread_id=None,
                    workflow_run_id=None,
                    filename="CHCC 2026 Price List updated March.pdf",
                    mime_type="application/pdf",
                    storage_path=f"users/{user.id}/attachments/chcc-price-list.pdf",
                    sha256="doc-sha-1",
                    size_bytes=1024,
                    metadata_json=None,
                ),
            )
            document_id = registered_document.id

            await run_agent(
                "how much the HRT test again",
                user.id,
                soul_session,
                runtime_session,
                document_ids=[document_id],
            )

            messages = (
                runtime_session.query(RuntimeMessage)
                .order_by(RuntimeMessage.sequence_id)
                .all()
            )
            thread = runtime_session.query(RuntimeThread).one()
            display = get_thread_messages_for_display(
                runtime_session,
                thread=thread,
                user_id=user.id,
                transcripts_dir=None,
                dek=None,
            )
    finally:
        agent_service.invalidate_agent_runtime_cache()

    assert extract_stored_pills(messages[0].content_json) == [
        {
            "kind": "document_attachment",
            "label": "CHCC 2026 Price List updated March.pdf",
            "ref": document_id,
        }
    ]
    assert extract_stored_pills(messages[1].content_json) == [
        {
            "kind": "document_source",
            "label": "CHCC 2026 Price List updated March.pdf",
            "ref": document_id,
        }
    ]
    assert display[0]["pills"] == [
        {
            "kind": "document_attachment",
            "label": "CHCC 2026 Price List updated March.pdf",
            "ref": document_id,
        }
    ]
    assert display[1]["pills"] == [
        {
            "kind": "document_source",
            "label": "CHCC 2026 Price List updated March.pdf",
            "ref": document_id,
        }
    ]


@pytest.mark.asyncio
async def test_run_agent_reuses_recent_pdf_context_for_followup_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent.thread_manager import (
        get_thread_messages_for_display,
    )
    from anima_server.services.documents.models import DocumentRegistration
    from anima_server.services.documents.store import register_document

    agent_service.invalidate_agent_runtime_cache()
    runner = RecordingRunner()
    monkeypatch.setattr(agent_service, "get_or_build_runner", lambda: runner)
    monkeypatch.setattr(agent_service, "_run_post_turn_hooks", lambda **kwargs: None)

    search_calls: list[tuple[str, tuple[int, ...]]] = []

    def fake_search_document_chunks(
        runtime_db: object,
        user_id: int,
        query: str,
        *,
        document_ids: list[int],
        limit: int,
    ) -> list[DocumentRagResult]:
        del runtime_db, user_id, limit
        search_calls.append((query, tuple(document_ids)))
        assert document_ids == [document_id]
        return [
            DocumentRagResult(
                chunk_id=20 + len(search_calls),
                document_id=document_id,
                filename="Insta360-X5-technische-daten-spec-sheet.pdf",
                content="Insta360 X5 specifications include camera sensors, video modes, battery, and phone compatibility.",
                similarity=0.9,
                page_start=1,
                page_end=1,
                section_title="Specs",
            )
        ]

    monkeypatch.setattr(agent_service, "search_document_chunks", fake_search_document_chunks)

    try:
        with _soul_db_session() as soul_session, runtime_db_session() as runtime_session:
            user = User(
                username="document-followup",
                password_hash="not-used",
                display_name="Document Followup",
            )
            soul_session.add(user)
            soul_session.commit()

            registered_document = register_document(
                runtime_session,
                DocumentRegistration(
                    user_id=user.id,
                    thread_id=None,
                    workflow_run_id=None,
                    filename="Insta360-X5-technische-daten-spec-sheet.pdf",
                    mime_type="application/pdf",
                    storage_path=f"users/{user.id}/attachments/insta360-x5.pdf",
                    sha256="doc-sha-followup",
                    size_bytes=2048,
                    metadata_json=None,
                ),
            )
            document_id = registered_document.id

            await run_agent(
                "what is this document all about",
                user.id,
                soul_session,
                runtime_session,
                document_ids=[document_id],
            )
            await run_agent(
                "tell me more about it",
                user.id,
                soul_session,
                runtime_session,
            )

            thread = runtime_session.query(RuntimeThread).one()
            display = get_thread_messages_for_display(
                runtime_session,
                thread=thread,
                user_id=user.id,
                transcripts_dir=None,
                dek=None,
            )
    finally:
        agent_service.invalidate_agent_runtime_cache()

    assert search_calls == [
        ("what is this document all about", (document_id,)),
        ("tell me more about it", (document_id,)),
    ]
    labels = [label for label, _value in runner.requests[1]["memory_blocks"]]
    assert "document_context" in labels
    assert "human" not in labels
    assert display[3]["pills"] == [
        {
            "kind": "document_source",
            "label": "Insta360-X5-technische-daten-spec-sheet.pdf",
            "ref": document_id,
        }
    ]


@pytest.mark.asyncio
async def test_run_agent_persists_image_source_pill_on_assistant_reply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent.state import extract_stored_pills

    agent_service.invalidate_agent_runtime_cache()
    runner = RecordingRunner()
    monkeypatch.setattr(agent_service, "get_or_build_runner", lambda: runner)
    monkeypatch.setattr(agent_service, "_run_post_turn_hooks", lambda **kwargs: None)
    monkeypatch.setattr(agent_service.settings, "data_dir", tmp_path)
    monkeypatch.setattr(agent_service.settings, "agent_provider", "openai")
    monkeypatch.setattr(agent_service.settings, "agent_model", "gpt-4o-mini")

    attachment = ChatRequestAttachment(
        kind="image",
        filename="pixel.png",
        mimeType="image/png",
        data=_b64(PNG_BYTES),
    )

    try:
        with _soul_db_session() as soul_session, runtime_db_session() as runtime_session:
            user = User(
                username="image-pills",
                password_hash="not-used",
                display_name="Image Pills",
            )
            soul_session.add(user)
            soul_session.commit()

            await run_agent(
                "what is in this image?",
                user.id,
                soul_session,
                runtime_session,
                attachments=[attachment],
            )

            messages = (
                runtime_session.query(RuntimeMessage)
                .order_by(RuntimeMessage.sequence_id)
                .all()
            )
    finally:
        agent_service.invalidate_agent_runtime_cache()

    pills = extract_stored_pills(messages[1].content_json)
    assert len(pills) == 1
    assert pills[0]["kind"] == "image_source"
    assert pills[0]["label"] == "pixel.png"
    assert isinstance(pills[0]["ref"], str)
    assert pills[0]["ref"].startswith("img_")


@pytest.mark.asyncio
async def test_run_agent_persists_recalled_image_source_pill_on_assistant_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import embeddings
    from anima_server.services.agent.state import extract_stored_pills

    async def fake_generate_embedding(_text: str) -> list[float]:
        return _embedding(1.0, 0.0)

    agent_service.invalidate_agent_runtime_cache()
    runner = RecordingRunner()
    monkeypatch.setattr(agent_service, "get_or_build_runner", lambda: runner)
    monkeypatch.setattr(agent_service, "_run_post_turn_hooks", lambda **kwargs: None)
    monkeypatch.setattr(embeddings, "generate_embedding", fake_generate_embedding)

    try:
        with _soul_db_session() as soul_session, runtime_db_session() as runtime_session:
            user = User(
                username="recalled-image-pills",
                password_hash="not-used",
                display_name="Recalled Image Pills",
            )
            soul_session.add(user)
            soul_session.commit()

            thread = RuntimeThread(user_id=user.id, status="active")
            runtime_session.add(thread)
            runtime_session.flush()
            asset = RuntimeImageAsset(
                user_id=user.id,
                filename="talia.png",
                mime_type="image/png",
                storage_path="users/1/media/images/talia.png",
                sha256="c" * 64,
                size_bytes=len(PNG_BYTES),
                status="indexed",
            )
            runtime_session.add(asset)
            runtime_session.flush()
            source_message = RuntimeMessage(
                thread_id=thread.id,
                user_id=user.id,
                sequence_id=1,
                role="user",
                content_text="Talia Yung reference image.",
                content_json={
                    "attachments": [
                        {
                            "id": "img_talia",
                            "kind": "image",
                            "mimeType": "image/png",
                            "filename": "talia.png",
                            "assetId": asset.id,
                            "storagePath": asset.storage_path,
                            "sizeBytes": asset.size_bytes,
                            "sha256": asset.sha256,
                        }
                    ]
                },
            )
            runtime_session.add(source_message)
            runtime_session.flush()
            runtime_session.add(
                RuntimeImageMessageLink(
                    user_id=user.id,
                    message_id=source_message.id,
                    image_asset_id=asset.id,
                    attachment_id="img_talia",
                )
            )
            annotation_text = "Talia Yung reference image with pastel pink hair."
            annotation = RuntimeImageAnnotation(
                user_id=user.id,
                image_asset_id=asset.id,
                annotation_kind="upload_context",
                content_text=annotation_text,
                content_hash=RuntimeImageAnnotation.compute_content_hash(
                    annotation_text
                ),
                status="active",
            )
            runtime_session.add(annotation)
            runtime_session.flush()
            annotation_embedding = _embedding(1.0, 0.0)
            runtime_session.add(
                RuntimeEmbedding(
                    user_id=user.id,
                    source_type="image_annotation",
                    source_id=annotation.id,
                    content_hash=annotation.content_hash,
                    embedding_checksum=RuntimeEmbedding.compute_embedding_checksum(
                        annotation_embedding
                    ),
                    embedding=annotation_embedding,
                    content_preview=annotation_text,
                )
            )
            thread.next_message_sequence = 2

            await run_agent(
                "do you remember her picture?",
                user.id,
                soul_session,
                runtime_session,
            )

            messages = (
                runtime_session.query(RuntimeMessage)
                .order_by(RuntimeMessage.sequence_id)
                .all()
            )
    finally:
        agent_service.invalidate_agent_runtime_cache()

    labels = [label for label, _value in runner.requests[0]["memory_blocks"]]
    assert "relevant_images" in labels
    pills = extract_stored_pills(messages[-1].content_json)
    assert pills == [
        {
            "kind": "image_source",
            "label": "talia.png",
            "ref": f"image:{asset.id}",
            "assetId": asset.id,
            "mimeType": "image/png",
            "url": f"/api/chat/messages/{source_message.id}/attachments/img_talia",
            "messageId": source_message.id,
            "threadId": thread.id,
            "attachmentId": "img_talia",
        }
    ]


@pytest.mark.asyncio
async def test_run_agent_attaches_connected_animus_action_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_service.invalidate_agent_runtime_cache()
    runner = RecordingRunner()
    monkeypatch.setattr(agent_service, "get_or_build_runner", lambda: runner)
    monkeypatch.setattr(agent_service, "_run_post_turn_hooks", lambda **kwargs: None)

    try:
        with _soul_db_session() as soul_session, runtime_db_session() as runtime_session:
            user = User(
                username="action-tools",
                password_hash="not-used",
                display_name="Action Tools",
            )
            soul_session.add(user)
            soul_session.commit()

            conn = ActionToolConnection(
                websocket=FakeWebSocket(),
                user_id=user.id,
                username="animus",
                action_tool_schemas=[
                    {
                        "name": "bash",
                        "description": "Execute a shell command through Animus.",
                        "parameters": {
                            "type": "object",
                            "properties": {"command": {"type": "string"}},
                            "required": ["command"],
                        },
                    }
                ],
            )
            action_registry.add(conn)
            try:
                await run_agent("inspect files", user.id, soul_session, runtime_session)
            finally:
                action_registry.remove(conn)
    finally:
        agent_service.invalidate_agent_runtime_cache()

    extra_tool_schemas = runner.requests[0]["extra_tool_schemas"]
    assert isinstance(extra_tool_schemas, list)
    assert extra_tool_schemas[0]["function"]["name"] == "bash"
    assert runner.requests[0]["tool_executor"] is not None


@pytest.mark.asyncio
async def test_run_agent_does_not_run_hidden_wide_evidence_retrieval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_service.invalidate_agent_runtime_cache()
    runner = RecordingRunner()
    calls: list[dict[str, object]] = []

    async def fake_retrieve_wide_evidence(**kwargs: object) -> WideEvidenceResult:
        calls.append(kwargs)
        return WideEvidenceResult(mode=RetrievalMode.AGGREGATE)

    monkeypatch.setattr(agent_service, "get_or_build_runner", lambda: runner)
    monkeypatch.setattr(agent_service, "_run_post_turn_hooks", lambda **kwargs: None)
    monkeypatch.setattr(
        "anima_server.services.agent.evidence_retrieval.retrieve_wide_evidence",
        fake_retrieve_wide_evidence,
    )

    try:
        with _soul_db_session() as soul_session, runtime_db_session() as runtime_session:
            user = User(
                username="no-hidden-wide-retrieval",
                password_hash="not-used",
                display_name="No Hidden Wide Retrieval",
            )
            soul_session.add(user)
            soul_session.commit()

            await run_agent(
                "How many model kits have I worked on or bought?",
                user.id,
                soul_session,
                runtime_session,
            )
    finally:
        agent_service.invalidate_agent_runtime_cache()

    assert calls == []


@pytest.mark.asyncio
async def test_run_agent_reloads_thread_scoped_memory_on_thread_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_service.invalidate_agent_runtime_cache()
    runner = RecordingRunner()

    monkeypatch.setattr(agent_service, "get_or_build_runner", lambda: runner)
    monkeypatch.setattr(agent_service, "_run_post_turn_hooks", lambda **kwargs: None)

    try:
        with _soul_db_session() as soul_session, runtime_db_session() as runtime_session:
            user = User(
                username="thread-memory",
                password_hash="not-used",
                display_name="Thread Memory",
            )
            soul_session.add(user)
            soul_session.commit()

            thread_one = RuntimeThread(user_id=user.id, status="active")
            thread_two = RuntimeThread(user_id=user.id, status="active")
            runtime_session.add_all([thread_one, thread_two])
            runtime_session.flush()

            await run_agent(
                "thread one turn",
                user.id,
                soul_session,
                runtime_session,
                thread_id=thread_one.id,
            )
            companion = agent_service._get_companion(user.id)
            stale_thread_one_block = (
                "thread_summary",
                f"stale summary for thread {thread_one.id}",
            )
            companion.set_memory_cache(
                (
                    MemoryBlock(
                        label=stale_thread_one_block[0],
                        description="Thread-specific summary.",
                        value=stale_thread_one_block[1],
                    ),
                )
            )
            await run_agent(
                "thread two turn",
                user.id,
                soul_session,
                runtime_session,
                thread_id=thread_two.id,
            )
    finally:
        agent_service.invalidate_agent_runtime_cache()

    assert stale_thread_one_block not in runner.requests[1]["memory_blocks"]


def test_persist_agent_result_records_prompt_budget_on_first_step() -> None:
    with runtime_db_session() as session:
        user_id = 42

        thread = RuntimeThread(user_id=user_id, status="active", next_message_sequence=2)
        session.add(thread)
        session.flush()

        run = create_run(
            session,
            thread_id=thread.id,
            user_id=user_id,
            provider="test-provider",
            model="test-model",
            mode="blocking",
        )
        result = AgentResult(
            response="ok",
            model="test-model",
            provider="test-provider",
            stop_reason="end_turn",
            step_traces=[StepTrace(step_index=0, assistant_text="ok")],
            prompt_budget=PromptBudgetTrace(
                total_budget=100,
                retained_chars=24,
                dropped_chars=8,
                retained_token_estimate=6,
                dropped_token_estimate=2,
                tier_usage={"0": 0, "1": 24, "2": 0, "3": 0},
                tier_budgets={"0": 0, "1": 100, "2": 0, "3": 0},
                system_prompt_chars=120,
                system_prompt_token_estimate=30,
                decisions=(
                    PromptBudgetBlockDecision(
                        label="current_focus",
                        tier=1,
                        status="kept",
                        original_chars=24,
                        final_chars=24,
                        reason="within_budget",
                    ),
                ),
            ),
        )

        persist_agent_result(
            session,
            thread=thread,
            run=run,
            result=result,
            initial_sequence_id=1,
        )
        session.commit()

        step = session.query(RuntimeStep).one()

    prompt_budget = step.request_json["prompt_budget"]
    assert prompt_budget["system_prompt_token_estimate"] == 30
    assert prompt_budget["decisions"][0]["label"] == "current_focus"


def test_post_turn_hooks_skip_experience_capture_without_source_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduled: list[object] = []

    def track_background_task(coro: object) -> None:
        scheduled.append(coro)
        close = getattr(coro, "close", None)
        if callable(close):
            close()

    monkeypatch.setattr(
        agent_service,
        "schedule_background_memory_consolidation",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(agent_service, "schedule_reflection", lambda **kwargs: None)
    monkeypatch.setattr(agent_service, "_track_background_task", track_background_task)

    result = AgentResult(
        response="Approved tool completed.",
        model="test-model",
        provider="test-provider",
        stop_reason="end_turn",
        step_traces=[
            StepTrace(
                step_index=0,
                tool_results=(
                    ToolExecutionResult(
                        call_id="call-1",
                        name="write_file",
                        output="ok",
                    ),
                ),
            )
        ],
    )

    agent_service._run_post_turn_hooks(
        user_id=1,
        thread_id=2,
        conversation_turn_count=1,
        user_message="",
        result=result,
        db_factory=lambda: None,  # type: ignore[return-value]
        runtime_db_factory=lambda: None,  # type: ignore[return-value]
        source_run_id=None,
    )

    assert scheduled == []


def test_experience_approach_redacts_raw_tool_outputs() -> None:
    result = AgentResult(
        response="I found the relevant memory.",
        model="test-model",
        provider="test-provider",
        stop_reason="end_turn",
        step_traces=[
            StepTrace(
                step_index=0,
                tool_results=(
                    ToolExecutionResult(
                        call_id="call-1",
                        name="recall_conversation",
                        output="PRIVATE MEMORY: the user shared a secret project name.",
                    ),
                ),
            )
        ],
    )

    approach = agent_service._experience_approach(result)

    assert "Result: recall_conversation succeeded." in approach
    assert "PRIVATE MEMORY" not in approach
    assert "secret project name" not in approach


def test_compaction_accounts_for_reserved_prompt_tokens() -> None:
    with runtime_db_session() as session:
        user_id = 43

        thread = RuntimeThread(user_id=user_id, status="active", next_message_sequence=3)
        session.add(thread)
        session.flush()

        session.add_all(
            [
                RuntimeMessage(
                    thread_id=thread.id,
                    user_id=user_id,
                    sequence_id=1,
                    role="user",
                    content_text="a" * 40,
                    is_in_context=True,
                ),
                RuntimeMessage(
                    thread_id=thread.id,
                    user_id=user_id,
                    sequence_id=2,
                    role="assistant",
                    content_text="b" * 40,
                    is_in_context=True,
                ),
            ]
        )
        session.flush()

        result = compact_thread_context(
            session,
            thread=thread,
            run_id=None,
            trigger_token_limit=30,
            keep_last_messages=1,
            reserved_prompt_tokens=12,
        )
        summary = session.query(RuntimeMessage).filter(RuntimeMessage.role == "summary").one()

    assert result is not None
    assert result.effective_trigger_token_limit == 18
    assert result.reserved_prompt_tokens == 12
    assert summary.sequence_id == 3
    assert "Conversation summary:" in (summary.content_text or "")


def test_companion_history_cache_is_scoped_by_thread() -> None:
    agent_service.invalidate_agent_runtime_cache()
    try:
        with runtime_db_session() as session:
            user_id = 44
            thread_one = RuntimeThread(
                user_id=user_id,
                status="active",
                next_message_sequence=2,
            )
            thread_two = RuntimeThread(
                user_id=user_id,
                status="active",
                next_message_sequence=2,
            )
            session.add_all([thread_one, thread_two])
            session.flush()

            append_message(
                session,
                thread=thread_one,
                run_id=None,
                step_id=None,
                sequence_id=1,
                role="user",
                content_text="thread one history",
            )
            append_message(
                session,
                thread=thread_two,
                run_id=None,
                step_id=None,
                sequence_id=1,
                role="user",
                content_text="thread two history",
            )
            session.commit()

            companion = agent_service._get_companion(user_id)
            history_one = companion.ensure_history_loaded(
                session,
                thread_id=thread_one.id,
            )
            history_two = companion.ensure_history_loaded(
                session,
                thread_id=thread_two.id,
            )

            history_one.clear()
            history_one_again = companion.ensure_history_loaded(
                session,
                thread_id=thread_one.id,
            )

        assert [message.content for message in history_one_again] == [
            "thread one history"
        ]
        assert [message.content for message in history_two] == [
            "thread two history"
        ]
    finally:
        agent_service.invalidate_agent_runtime_cache()


def test_persist_agent_result_does_not_overwrite_cancelled_run() -> None:
    with runtime_db_session() as session:
        user_id = 45
        thread = RuntimeThread(user_id=user_id, status="active", next_message_sequence=2)
        session.add(thread)
        session.flush()
        run = create_run(
            session,
            thread_id=thread.id,
            user_id=user_id,
            provider="test-provider",
            model="test-model",
            mode="blocking",
        )
        cancel_run(session, run.id)
        session.flush()

        result = AgentResult(
            response="late reply",
            model="test-model",
            provider="test-provider",
            stop_reason="terminal_tool",
            step_traces=[StepTrace(step_index=0, assistant_text="late reply")],
        )

        persist_agent_result(
            session,
            thread=thread,
            run=run,
            result=result,
            initial_sequence_id=1,
        )
        session.commit()

        session.refresh(run)
        persisted_message_count = (
            session.query(RuntimeMessage)
            .filter(RuntimeMessage.run_id == run.id)
            .count()
        )

    assert run.status == "cancelled"
    assert run.stop_reason == "cancelled"
    assert persisted_message_count == 0


@pytest.mark.asyncio
async def test_cancelled_agent_task_marks_running_run_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = BlockingRunner()
    monkeypatch.setattr(agent_service, "get_or_build_runner", lambda: runner)
    monkeypatch.setattr(agent_service, "_run_post_turn_hooks", lambda **kwargs: None)

    with _soul_db_session() as soul_session, runtime_db_session() as runtime_session:
        user = User(
            username="cancel-stream",
            password_hash="not-used",
            display_name="Cancel Stream",
        )
        soul_session.add(user)
        soul_session.commit()

        task = asyncio.create_task(
            run_agent("please wait", user.id, soul_session, runtime_session)
        )
        await asyncio.wait_for(runner.started.wait(), timeout=1)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        run = runtime_session.query(RuntimeRun).one()

    assert run.status == "cancelled"
    assert run.stop_reason == "cancelled"


@pytest.mark.asyncio
async def test_stage1_failure_marks_run_failed_and_evicts_user_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure after the run/user message are persisted but before the
    runtime is invoked must not leave a zombie 'running' run or replay
    the user message as unanswered history."""

    async def fail_assemble(**kwargs: object) -> None:
        raise RuntimeError("memory load failed")

    class FakeCompanion:
        thread_id: int | None = None

        def invalidate_history(self, *, thread_id: int) -> None:
            del thread_id

        def ensure_history_loaded(
            self,
            runtime_db: Session,
            *,
            thread_id: int,
        ) -> list[agent_service.StoredMessage]:
            del runtime_db, thread_id
            return []

    monkeypatch.setattr(agent_service.settings, "data_dir", tmp_path)
    monkeypatch.setattr(agent_service.settings, "agent_provider", "openai")
    monkeypatch.setattr(agent_service.settings, "agent_model", "gpt-4o-mini")
    monkeypatch.setattr(
        agent_service, "_get_companion", lambda user_id: FakeCompanion()
    )
    monkeypatch.setattr(agent_service, "_assemble_turn_context", fail_assemble)

    attachment = ChatRequestAttachment(
        kind="image",
        filename="stage1-failed.png",
        mimeType="image/png",
        data=_b64(PNG_BYTES),
    )

    with _soul_db_session() as soul_session, runtime_db_session() as runtime_session:
        user = User(
            username="stage1-fail",
            password_hash="not-used",
            display_name="Stage1 Fail",
        )
        soul_session.add(user)
        soul_session.commit()

        with pytest.raises(RuntimeError, match="memory load failed"):
            await run_agent(
                "hello",
                user.id,
                soul_session,
                runtime_session,
                attachments=[attachment],
            )

        run = runtime_session.query(RuntimeRun).one()
        message = runtime_session.query(RuntimeMessage).one()
        image_link_count = runtime_session.query(RuntimeImageMessageLink).count()
        image_asset_count = runtime_session.query(RuntimeImageAsset).count()

    assert run.status == "failed"
    assert message.is_in_context is False
    assert image_link_count == 0
    assert image_asset_count == 0


@pytest.mark.asyncio
async def test_failed_image_turn_does_not_leave_active_image_annotations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent.thread_manager import get_thread_messages_for_display

    class FailingRunner:
        async def invoke(self, *args, **kwargs) -> AgentResult:
            del args, kwargs
            raise RuntimeError("provider failed")

    agent_service.invalidate_agent_runtime_cache()
    monkeypatch.setattr(agent_service.settings, "data_dir", tmp_path)
    monkeypatch.setattr(agent_service.settings, "agent_provider", "openai")
    monkeypatch.setattr(agent_service.settings, "agent_model", "gpt-4o-mini")
    monkeypatch.setattr(agent_service, "get_or_build_runner", lambda: FailingRunner())
    monkeypatch.setattr(agent_service, "_run_post_turn_hooks", lambda **kwargs: None)

    attachment = ChatRequestAttachment(
        kind="image",
        filename="failed.png",
        mimeType="image/png",
        data=_b64(PNG_BYTES),
    )

    try:
        with _soul_db_session() as soul_session, runtime_db_session() as runtime_session:
            user = User(
                username="failed-image-turn",
                password_hash="not-used",
                display_name="Failed Image Turn",
            )
            soul_session.add(user)
            soul_session.commit()

            with pytest.raises(RuntimeError, match="provider failed"):
                await run_agent(
                    "what is this failed image?",
                    user.id,
                    soul_session,
                    runtime_session,
                    attachments=[attachment],
                )

            run = runtime_session.query(RuntimeRun).one()
            user_msg = (
                runtime_session.query(RuntimeMessage)
                .filter(RuntimeMessage.role == "user")
                .one()
            )
            thread = runtime_session.query(RuntimeThread).one()
            display_messages = get_thread_messages_for_display(
                runtime_session,
                thread=thread,
                user_id=user.id,
                transcripts_dir=tmp_path / "transcripts",
                dek=None,
            )
            annotation_count = runtime_session.query(RuntimeImageAnnotation).count()
            image_link_count = runtime_session.query(RuntimeImageMessageLink).count()
            image_asset_count = runtime_session.query(RuntimeImageAsset).count()
            embedding_count = (
                runtime_session.query(RuntimeEmbedding)
                .filter(RuntimeEmbedding.source_type == "image_annotation")
                .count()
            )
    finally:
        agent_service.invalidate_agent_runtime_cache()

    assert run.status == "failed"
    assert user_msg.is_in_context is False
    assert image_link_count == 0
    assert image_asset_count == 0
    assert display_messages[0]["attachments"] == []
    assert annotation_count == 0
    assert embedding_count == 0


def test_search_knowledge_bundle_tool_returns_concepts_and_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hashlib

    from anima_server.models.runtime import RuntimeKnowledgeConcept
    from anima_server.services.agent import tools as agent_tools
    from anima_server.services.agent.tool_context import (
        ToolContext,
        clear_tool_context,
        set_tool_context,
    )
    from anima_server.services.ingestion import retrieval as knowledge_retrieval
    from anima_server.services.ingestion.adapters.text import ingest_text_content

    def fake_embedding(text: str) -> list[float]:
        if "portable" in text.lower():
            return [1.0, *([0.0] * 767)]
        return [0.0, 1.0, *([0.0] * 766)]

    monkeypatch.setattr(knowledge_retrieval, "generate_embedding", fake_embedding)
    with _soul_db_session() as soul_session, runtime_db_session() as runtime_session:
        concept = RuntimeKnowledgeConcept(
            user_id=1,
            concept_type="claim",
            slug="portable-core",
            title="Portable Core",
            description="Portable continuity",
            body_markdown="Portable continuity keeps the local core coherent.",
            frontmatter_json={"type": "claim", "title": "Portable Core"},
            content_hash=hashlib.sha256(b"portable").hexdigest(),
            status="active",
        )
        runtime_session.add(concept)
        runtime_session.flush()
        _source, _artifacts, spans = ingest_text_content(
            runtime_session,
            user_id=1,
            content="Portable evidence with a source citation.",
            filename="portable.txt",
        )
        knowledge_retrieval.upsert_concept_embedding(runtime_session, concept=concept)
        knowledge_retrieval.upsert_source_span_embedding(runtime_session, span=spans[0])

        set_tool_context(
            ToolContext(db=soul_session, runtime_db=runtime_session, user_id=1, thread_id=1)
        )
        try:
            output = agent_tools.search_knowledge_bundle("portable continuity")
        finally:
            clear_tool_context()

    assert "Portable Core" in output
    assert "source_span:" in output
    assert "Saved" not in output


def test_search_knowledge_bundle_tool_uses_text_fallback_without_embeddings() -> None:
    import hashlib

    from anima_server.models.runtime import RuntimeKnowledgeConcept
    from anima_server.services.agent import tools as agent_tools
    from anima_server.services.agent.tool_context import (
        ToolContext,
        clear_tool_context,
        set_tool_context,
    )
    from anima_server.services.ingestion.adapters.text import ingest_text_content

    with _soul_db_session() as soul_session, runtime_db_session() as runtime_session:
        concept = RuntimeKnowledgeConcept(
            user_id=1,
            concept_type="claim",
            slug="portable-core",
            title="Portable Core",
            description="Portable continuity",
            body_markdown="Portable continuity keeps the local core coherent.",
            frontmatter_json={"type": "claim", "title": "Portable Core"},
            content_hash=hashlib.sha256(b"portable").hexdigest(),
            status="active",
        )
        runtime_session.add(concept)
        runtime_session.flush()
        ingest_text_content(
            runtime_session,
            user_id=1,
            content="Portable evidence with a source citation.",
            filename="portable.txt",
        )

        set_tool_context(
            ToolContext(db=soul_session, runtime_db=runtime_session, user_id=1, thread_id=1)
        )
        try:
            output = agent_tools.search_knowledge_bundle("portable")
        finally:
            clear_tool_context()

    assert "Portable Core" in output
    assert "source_span:" in output
    assert "No compiled knowledge bundle results found" not in output


def test_inline_image_indexing_rolls_back_partial_rows_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_after_partial_flush(
        runtime_db: Session,
        *,
        user_id: int,
        image_asset_ids: list[int],
        upload_context: str,
    ) -> None:
        runtime_db.add(
            RuntimeImageAnnotation(
                user_id=user_id,
                image_asset_id=image_asset_ids[0],
                annotation_kind="upload_context",
                content_text=upload_context,
                content_hash=RuntimeImageAnnotation.compute_content_hash(upload_context),
                status="active",
            )
        )
        runtime_db.flush()
        raise RuntimeError("indexing failed after flush")

    from anima_server.services.images import indexing as indexing_module

    monkeypatch.setattr(
        indexing_module,
        "index_image_attachments_for_message",
        fail_after_partial_flush,
    )

    with runtime_db_session() as runtime_session:
        asset = RuntimeImageAsset(
            user_id=7,
            filename="partial.png",
            mime_type="image/png",
            storage_path="users/7/media/images/aa/partial.png",
            sha256="a" * 64,
            size_bytes=len(PNG_BYTES),
            retention_state="active",
        )
        runtime_session.add(asset)
        runtime_session.flush()

        agent_service._index_image_attachments_inline(
            runtime_session,
            user_id=7,
            user_message="partial indexing should not persist",
            attachments=(
                agent_service.StoredAttachment(
                    id="partial",
                    kind="image",
                    mime_type="image/png",
                    path="unused",
                    storage_path=asset.storage_path,
                    asset_id=asset.id,
                    retention_state="active",
                ),
            ),
        )
        runtime_session.commit()

        annotation_count = runtime_session.query(RuntimeImageAnnotation).count()

    assert annotation_count == 0


def test_should_retry_after_compaction_only_before_tools_executed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The overflow retry re-runs the whole turn, so it is only safe when
    no tools have executed yet."""
    from anima_server.services.agent.llm import ContextWindowOverflowError
    from anima_server.services.agent.runtime_types import (
        StepContext,
        StepFailedError,
        StepProgression,
    )

    monkeypatch.setattr(
        agent_service.settings, "agent_context_overflow_retry", True)

    def make_error(step_index: int, progression: StepProgression) -> StepFailedError:
        ctx = StepContext(step_index=step_index, progression=progression)
        return StepFailedError(ContextWindowOverflowError("too big"), ctx)

    # Overflow on the very first LLM request: prompt too large, safe to retry.
    assert agent_service._should_retry_after_compaction(
        make_error(0, StepProgression.LLM_REQUESTED)) is True
    # Tools already started within step 0: retry would re-execute them.
    assert agent_service._should_retry_after_compaction(
        make_error(0, StepProgression.TOOLS_STARTED)) is False
    # Later step: tools from earlier steps already executed.
    assert agent_service._should_retry_after_compaction(
        make_error(1, StepProgression.LLM_REQUESTED)) is False
    # Non-overflow failures never retry.
    assert agent_service._should_retry_after_compaction(
        StepFailedError(RuntimeError("boom"), StepContext())) is False


def test_client_error_message_masks_internal_errors() -> None:
    from anima_server.services.agent.llm import LLMConfigError

    masked = agent_service.client_error_message(
        RuntimeError("connection to postgres://user:secret@host failed"))
    assert "secret" not in masked
    assert "internal error" in masked

    # Messages written for users pass through unchanged.
    assert agent_service.client_error_message(
        LLMConfigError("Choose a vision-capable model.")
    ) == "Choose a vision-capable model."


@pytest.mark.asyncio
async def test_cancel_agent_run_reaches_inflight_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cancel_agent_run() during a turn must stop the runner: the run row
    is committed before the LLM stage, the cancel endpoint finds it, and
    the runtime's cancel_event fires."""

    class CancellableRunner:
        def __init__(self) -> None:
            self.started = asyncio.Event()

        async def invoke(self, *args, **kwargs) -> AgentResult:
            del args
            self.started.set()
            cancel_event = kwargs.get("cancel_event")
            assert cancel_event is not None
            await asyncio.wait_for(cancel_event.wait(), timeout=5)
            return AgentResult(
                response="",
                model="test-model",
                provider="test-provider",
                stop_reason="cancelled",
                step_traces=[],
            )

    agent_service.invalidate_agent_runtime_cache()
    runner = CancellableRunner()
    monkeypatch.setattr(agent_service, "get_or_build_runner", lambda: runner)
    monkeypatch.setattr(agent_service, "_run_post_turn_hooks", lambda **kwargs: None)

    with _soul_db_session() as soul_session, runtime_db_session() as runtime_session:
        user = User(
            username="cancel-mid-turn",
            password_hash="not-used",
            display_name="Cancel Mid Turn",
        )
        soul_session.add(user)
        soul_session.commit()

        task = asyncio.create_task(
            run_agent("slow question", user.id, soul_session, runtime_session)
        )
        await asyncio.wait_for(runner.started.wait(), timeout=5)

        # The run row is committed before the LLM stage, so a concurrent
        # request can find and cancel it.
        run = runtime_session.query(RuntimeRun).one()
        assert run.status == "running"
        cancelled = await agent_service.cancel_agent_run(
            run.id, user.id, runtime_session)
        assert cancelled is not None

        result = await asyncio.wait_for(task, timeout=5)
        runtime_session.expire_all()
        run = runtime_session.query(RuntimeRun).one()

    assert result.stop_reason == "cancelled"
    assert run.status == "cancelled"


@pytest.mark.asyncio
async def test_stage3_persist_failure_marks_run_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure while persisting the result (after the early commit) must
    mark the run failed and evict the user message — not leave a zombie
    'running' run whose message replays as history."""

    class OkRunner:
        async def invoke(self, *args, **kwargs) -> AgentResult:
            del args, kwargs
            return AgentResult(
                response="hi there",
                model="test-model",
                provider="test-provider",
                stop_reason="end_turn",
                step_traces=[StepTrace(step_index=0, assistant_text="hi there")],
            )

    async def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("persist exploded")

    agent_service.invalidate_agent_runtime_cache()
    monkeypatch.setattr(agent_service, "get_or_build_runner", lambda: OkRunner())
    monkeypatch.setattr(agent_service, "_run_post_turn_hooks", lambda **kwargs: None)
    monkeypatch.setattr(agent_service, "_persist_turn_result", boom)

    with _soul_db_session() as soul_session, runtime_db_session() as runtime_session:
        user = User(
            username="stage3-fail",
            password_hash="not-used",
            display_name="Stage3 Fail",
        )
        soul_session.add(user)
        soul_session.commit()

        with pytest.raises(RuntimeError, match="persist exploded"):
            await run_agent("hello", user.id, soul_session, runtime_session)

        runtime_session.expire_all()
        run = runtime_session.query(RuntimeRun).one()
        user_msg = (
            runtime_session.query(RuntimeMessage)
            .filter(RuntimeMessage.role == "user")
            .one()
        )

    assert run.status == "failed"
    assert user_msg.is_in_context is False


# --------------------------------------------------------------------------- #
# Cancellation-safe turn lifecycle (ARH-002)
# --------------------------------------------------------------------------- #


class _MinimalCompanion:
    """Just enough companion surface for turn preparation."""

    thread_id: int | None = None

    def invalidate_history(self, *, thread_id: int) -> None:
        del thread_id

    def ensure_history_loaded(
        self,
        runtime_db: Session,
        *,
        thread_id: int,
    ) -> list[agent_service.StoredMessage]:
        del runtime_db, thread_id
        return []


@pytest.mark.asyncio
async def test_stage1_cancellation_marks_run_cancelled_and_evicts_user_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A client disconnect during context assembly (after the early commit)
    raises CancelledError, which the Exception-only cleanup used to miss —
    stranding the run as 'running' and replaying the user message."""

    async def cancelled_assemble(**kwargs: object) -> None:
        del kwargs
        raise asyncio.CancelledError

    monkeypatch.setattr(
        agent_service, "_get_companion", lambda user_id: _MinimalCompanion()
    )
    monkeypatch.setattr(agent_service, "_assemble_turn_context", cancelled_assemble)

    with _soul_db_session() as soul_session, runtime_db_session() as runtime_session:
        user = User(
            username="stage1-cancel",
            password_hash="not-used",
            display_name="Stage1 Cancel",
        )
        soul_session.add(user)
        soul_session.commit()

        with pytest.raises(asyncio.CancelledError):
            await run_agent("hello", user.id, soul_session, runtime_session)

        runtime_session.expire_all()
        run = runtime_session.query(RuntimeRun).one()
        user_msg = (
            runtime_session.query(RuntimeMessage)
            .filter(RuntimeMessage.role == "user")
            .one()
        )

    assert run.status == "cancelled"
    assert user_msg.is_in_context is False


@pytest.mark.asyncio
async def test_stage1b_cancellation_marks_run_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation during proactive compaction (or the run_started emit,
    which shares the same handler) must not strand the run."""

    async def cancelled_compact(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise asyncio.CancelledError

    async def minimal_assemble(**kwargs: object) -> agent_service._TurnContext:
        del kwargs
        return agent_service._TurnContext(
            history=[],
            conversation_turn_count=1,
            memory_blocks=(),
        )

    monkeypatch.setattr(
        agent_service, "_get_companion", lambda user_id: _MinimalCompanion()
    )
    monkeypatch.setattr(agent_service, "_assemble_turn_context", minimal_assemble)
    monkeypatch.setattr(
        agent_service, "_proactive_compact_if_needed", cancelled_compact
    )

    with _soul_db_session() as soul_session, runtime_db_session() as runtime_session:
        user = User(
            username="stage1b-cancel",
            password_hash="not-used",
            display_name="Stage1b Cancel",
        )
        soul_session.add(user)
        soul_session.commit()

        with pytest.raises(asyncio.CancelledError):
            await run_agent("hello", user.id, soul_session, runtime_session)

        runtime_session.expire_all()
        run = runtime_session.query(RuntimeRun).one()
        user_msg = (
            runtime_session.query(RuntimeMessage)
            .filter(RuntimeMessage.role == "user")
            .one()
        )

    assert run.status == "cancelled"
    assert user_msg.is_in_context is False


@pytest.mark.asyncio
async def test_stage3_cancellation_marks_run_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation while persisting the result must not strand the run."""

    class OkRunner:
        async def invoke(self, *args, **kwargs) -> AgentResult:
            del args, kwargs
            return AgentResult(
                response="hi there",
                model="test-model",
                provider="test-provider",
                stop_reason="end_turn",
                step_traces=[StepTrace(step_index=0, assistant_text="hi there")],
            )

    async def cancelled_persist(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise asyncio.CancelledError

    agent_service.invalidate_agent_runtime_cache()
    monkeypatch.setattr(agent_service, "get_or_build_runner", lambda: OkRunner())
    monkeypatch.setattr(agent_service, "_run_post_turn_hooks", lambda **kwargs: None)
    monkeypatch.setattr(agent_service, "_persist_turn_result", cancelled_persist)

    with _soul_db_session() as soul_session, runtime_db_session() as runtime_session:
        user = User(
            username="stage3-cancel",
            password_hash="not-used",
            display_name="Stage3 Cancel",
        )
        soul_session.add(user)
        soul_session.commit()

        with pytest.raises(asyncio.CancelledError):
            await run_agent("hello", user.id, soul_session, runtime_session)

        runtime_session.expire_all()
        run = runtime_session.query(RuntimeRun).one()

    assert run.status == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["agent", "approval"])
async def test_public_stream_closes_shared_pump(
    monkeypatch: pytest.MonkeyPatch,
    entrypoint: str,
) -> None:
    """Closing either public SSE wrapper must close its inner shared pump."""
    inner_closed = asyncio.Event()
    created_streams: list[AsyncGenerator[object, None]] = []

    def fake_stream_via_queue(
        *args: object, **kwargs: object
    ) -> AsyncGenerator[object, None]:
        del args, kwargs

        async def inner() -> AsyncGenerator[object, None]:
            try:
                yield agent_service.build_error_event("probe")
                await asyncio.Future()
            finally:
                inner_closed.set()

        stream = inner()
        created_streams.append(stream)
        return stream

    monkeypatch.setattr(agent_service, "_stream_via_queue", fake_stream_via_queue)
    stream = (
        agent_service.stream_agent("hello", 1, object(), object())
        if entrypoint == "agent"
        else agent_service.stream_approve_or_deny(1, 1, True, object(), object())
    )

    try:
        await anext(stream)
        await stream.aclose()
        assert inner_closed.is_set()
    finally:
        for created in created_streams:
            await created.aclose()


@pytest.mark.asyncio
async def test_stream_shutdown_does_not_deadlock_when_queue_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closing the stream while the bounded queue is full must not deadlock:
    the cancelled worker's finally used to await queue.put(None) on a full
    queue nobody reads, hanging the generator's own finally forever."""

    class FloodingRunner:
        async def invoke(self, *args, **kwargs) -> AgentResult:
            del args
            event_callback = kwargs["event_callback"]
            while True:
                await event_callback(agent_service.build_error_event("flood"))

    agent_service.invalidate_agent_runtime_cache()
    monkeypatch.setattr(agent_service.settings, "agent_stream_queue_max_size", 1)
    monkeypatch.setattr(agent_service, "get_or_build_runner", lambda: FloodingRunner())
    monkeypatch.setattr(agent_service, "_run_post_turn_hooks", lambda **kwargs: None)
    original_stream_via_queue = agent_service._stream_via_queue
    created_pumps: list[AsyncGenerator[object, None]] = []

    def capture_stream_via_queue(
        *args: object, **kwargs: object
    ) -> AsyncGenerator[object, None]:
        pump = original_stream_via_queue(*args, **kwargs)
        created_pumps.append(pump)
        return pump

    monkeypatch.setattr(
        agent_service, "_stream_via_queue", capture_stream_via_queue
    )
    tasks_before = set(asyncio.all_tasks())

    with _soul_db_session() as soul_session, runtime_db_session() as runtime_session:
        user = User(
            username="flood-stream",
            password_hash="not-used",
            display_name="Flood Stream",
        )
        soul_session.add(user)
        soul_session.commit()

        gen = agent_service.stream_agent(
            "hello", user.id, soul_session, runtime_session
        )
        first = await asyncio.wait_for(anext(gen), timeout=5)
        assert first is not None
        # Let the worker fill the queue and block on its next put.
        await asyncio.sleep(0.05)
        await asyncio.wait_for(gen.aclose(), timeout=5)

    await asyncio.sleep(0)
    leaked_workers = [
        task
        for task in asyncio.all_tasks() - tasks_before
        if not task.done()
        and "_stream_via_queue.<locals>.worker" in task.get_coro().__qualname__
    ]
    try:
        assert leaked_workers == []
    finally:
        for task in leaked_workers:
            task.cancel()
        if leaked_workers:
            await asyncio.gather(*leaked_workers, return_exceptions=True)
        for pump in created_pumps:
            await pump.aclose()


@pytest.mark.asyncio
async def test_cancel_agent_run_does_not_leak_preset_event_for_terminal_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancelling an already-terminal run must not insert a pre-set cancel
    event that no turn will ever pop; cancelling an active run must still
    signal the in-flight turn."""

    class SpyCompanion:
        def __init__(self) -> None:
            self.events: dict[int, object] = {}
            self.set_calls: list[int] = []

        def get_cancel_event(self, run_id: int) -> object | None:
            return self.events.get(run_id)

        def set_cancel(self, run_id: int) -> None:
            self.set_calls.append(run_id)

    spy = SpyCompanion()
    monkeypatch.setattr(agent_service, "get_companion", lambda user_id: spy)

    with runtime_db_session() as session:
        thread = RuntimeThread(user_id=7, status="active", next_message_sequence=1)
        session.add(thread)
        session.flush()

        done_run = create_run(
            session,
            thread_id=thread.id,
            user_id=7,
            provider="test-provider",
            model="test-model",
            mode="blocking",
        )
        done_run.status = "completed"
        session.commit()

        result = await agent_service.cancel_agent_run(done_run.id, 7, session)
        assert result is not None
        assert spy.set_calls == []

        active_run = create_run(
            session,
            thread_id=thread.id,
            user_id=7,
            provider="test-provider",
            model="test-model",
            mode="blocking",
        )
        session.commit()

        result = await agent_service.cancel_agent_run(active_run.id, 7, session)
        assert result is not None
        assert spy.set_calls == [active_run.id]
