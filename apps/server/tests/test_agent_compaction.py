"""Tests for context compaction: token estimation and summary rendering."""

from __future__ import annotations

import pytest
from anima_server.models.runtime import RuntimeMessage, RuntimeThread
from anima_server.services.agent.compaction import (
    SUMMARY_LINE_LIMIT,
    SUMMARY_TEXT_LIMIT,
    CompactionResult,
    _summarize_row,
    _trim_summary_text,
    compact_thread_context,
    estimate_message_tokens,
    render_summary_text,
)
from conftest_runtime import runtime_db_session
from sqlalchemy.orm import Session

# --------------------------------------------------------------------------- #
# In-memory database helper (runtime DB — compaction uses RuntimeBase models)
# --------------------------------------------------------------------------- #

_db_session = runtime_db_session

_TEST_USER_COUNTER = 0


class _FakeUser:
    def __init__(self) -> None:
        global _TEST_USER_COUNTER
        _TEST_USER_COUNTER += 1
        self.id = _TEST_USER_COUNTER


def _make_user(db: Session) -> _FakeUser:
    return _FakeUser()


def _make_thread(db: Session, user_id: int) -> RuntimeThread:
    thread = RuntimeThread(user_id=user_id, status="active")
    db.add(thread)
    db.flush()
    return thread


def _add_message(
    db: Session,
    *,
    thread_id: int,
    sequence_id: int,
    role: str,
    content_text: str,
    user_id: int = 1,
    is_in_context: bool = True,
    tool_name: str | None = None,
    token_estimate: int | None = None,
) -> RuntimeMessage:
    msg = RuntimeMessage(
        thread_id=thread_id,
        user_id=user_id,
        run_id=None,
        step_id=None,
        sequence_id=sequence_id,
        role=role,
        content_text=content_text,
        is_in_context=is_in_context,
        tool_name=tool_name,
        token_estimate=token_estimate,
    )
    db.add(msg)
    db.flush()
    return msg


# --------------------------------------------------------------------------- #
# estimate_message_tokens
# --------------------------------------------------------------------------- #


def test_estimate_tokens_empty() -> None:
    assert estimate_message_tokens(content_text=None) == 0


def test_estimate_tokens_text_only() -> None:
    # "hello world" = 11 chars => ceil(11/3) = 4 (conservative estimate)
    tokens = estimate_message_tokens(content_text="hello world")
    assert tokens == 4


def test_estimate_tokens_with_tool_name() -> None:
    tokens = estimate_message_tokens(content_text="result", tool_name="search")
    assert tokens > 0


def test_estimate_tokens_with_json() -> None:
    tokens = estimate_message_tokens(content_text=None, content_json={"key": "value"})
    assert tokens > 0


def test_estimate_tokens_with_non_dict_json() -> None:
    tokens = estimate_message_tokens(content_text=None, content_json=["a", "b"])
    assert tokens > 0


def test_estimate_tokens_minimum_one() -> None:
    # Single character => ceil(1/4) = 1
    assert estimate_message_tokens(content_text="x") == 1


# --------------------------------------------------------------------------- #
# _trim_summary_text
# --------------------------------------------------------------------------- #


def test_trim_summary_text_short() -> None:
    assert _trim_summary_text("  hello world  ") == "hello world"


def test_trim_summary_text_long_truncated() -> None:
    long_text = "a" * 300
    trimmed = _trim_summary_text(long_text)
    assert len(trimmed) <= SUMMARY_TEXT_LIMIT
    assert trimmed.endswith("...")


def test_trim_summary_text_normalizes_whitespace() -> None:
    assert _trim_summary_text("hello   world\n\nfoo") == "hello world foo"


# --------------------------------------------------------------------------- #
# _summarize_row
# --------------------------------------------------------------------------- #


def test_summarize_row_user() -> None:
    with _db_session() as db:
        user = _make_user(db)
        thread = _make_thread(db, user.id)
        msg = _add_message(
            db,
            thread_id=thread.id,
            sequence_id=1,
            role="user",
            content_text="Hello there",
        )
        result = _summarize_row(msg, user_id=user.id)
        assert result.startswith("User:")
        assert "Hello there" in result


def test_summarize_row_assistant() -> None:
    with _db_session() as db:
        user = _make_user(db)
        thread = _make_thread(db, user.id)
        msg = _add_message(
            db,
            thread_id=thread.id,
            sequence_id=1,
            role="assistant",
            content_text="Hi back!",
        )
        result = _summarize_row(msg, user_id=user.id)
        assert result.startswith("Assistant:")


def test_summarize_row_tool_with_name() -> None:
    with _db_session() as db:
        user = _make_user(db)
        thread = _make_thread(db, user.id)
        msg = _add_message(
            db,
            thread_id=thread.id,
            sequence_id=1,
            role="tool",
            content_text="search results",
            tool_name="search",
        )
        result = _summarize_row(msg, user_id=user.id)
        assert "Tool search:" in result


def test_summarize_row_empty_content() -> None:
    with _db_session() as db:
        user = _make_user(db)
        thread = _make_thread(db, user.id)
        msg = _add_message(
            db,
            thread_id=thread.id,
            sequence_id=1,
            role="user",
            content_text="",
        )
        result = _summarize_row(msg, user_id=user.id)
        assert "[empty]" in result


# --------------------------------------------------------------------------- #
# render_summary_text
# --------------------------------------------------------------------------- #


def test_render_summary_text_basic() -> None:
    with _db_session() as db:
        user = _make_user(db)
        thread = _make_thread(db, user.id)

        msgs = []
        for i in range(3):
            msgs.append(
                _add_message(
                    db,
                    thread_id=thread.id,
                    sequence_id=i + 1,
                    role="user",
                    content_text=f"Message {i}",
                )
            )

        summary = render_summary_text([], msgs, user_id=user.id)
        assert summary.startswith("Conversation summary:")
        assert "Message 0" in summary
        assert "Message 2" in summary


def test_render_summary_text_with_existing_summary() -> None:
    with _db_session() as db:
        user = _make_user(db)
        thread = _make_thread(db, user.id)

        summary_msg = _add_message(
            db,
            thread_id=thread.id,
            sequence_id=1,
            role="summary",
            content_text="Earlier summary text",
        )
        compacted = _add_message(
            db,
            thread_id=thread.id,
            sequence_id=2,
            role="user",
            content_text="Hello",
        )

        summary = render_summary_text([summary_msg], [compacted], user_id=user.id)
        assert "Earlier summary" in summary
        assert "Hello" in summary


def test_render_summary_text_hidden_count() -> None:
    with _db_session() as db:
        user = _make_user(db)
        thread = _make_thread(db, user.id)

        msgs = []
        for i in range(SUMMARY_LINE_LIMIT + 5):
            msgs.append(
                _add_message(
                    db,
                    thread_id=thread.id,
                    sequence_id=i + 1,
                    role="user",
                    content_text=f"msg {i}",
                )
            )

        summary = render_summary_text([], msgs, user_id=user.id)
        assert "additional earlier messages were compacted" in summary


# --------------------------------------------------------------------------- #
# compact_thread_context
# --------------------------------------------------------------------------- #


def test_compact_thread_context_no_messages() -> None:
    with _db_session() as db:
        user = _make_user(db)
        thread = _make_thread(db, user.id)
        result = compact_thread_context(
            db,
            thread=thread,
            run_id=None,
            trigger_token_limit=100,
            keep_last_messages=4,
        )
        assert result is None


def test_compact_thread_context_under_limit() -> None:
    with _db_session() as db:
        user = _make_user(db)
        thread = _make_thread(db, user.id)

        for i in range(3):
            _add_message(
                db,
                thread_id=thread.id,
                sequence_id=i + 1,
                role="user",
                content_text="short",
                token_estimate=5,
            )

        # 3 msgs * 5 tokens = 15, well under limit of 10000
        result = compact_thread_context(
            db,
            thread=thread,
            run_id=None,
            trigger_token_limit=10000,
            keep_last_messages=2,
        )
        assert result is None


def test_compact_thread_context_triggers_compaction() -> None:
    with _db_session() as db:
        user = _make_user(db)
        thread = _make_thread(db, user.id)

        # Create messages that exceed the token limit
        num_messages = 10
        for i in range(num_messages):
            _add_message(
                db,
                thread_id=thread.id,
                sequence_id=i + 1,
                role="user" if i % 2 == 0 else "assistant",
                content_text=f"Message content number {i} " * 20,
                token_estimate=200,
            )

        # Update thread sequence counter so reserve_message_sequences works
        thread.next_message_sequence = num_messages + 1
        db.add(thread)
        db.commit()

        # Total tokens = 10 * 200 = 2000, trigger limit = 500
        result = compact_thread_context(
            db,
            thread=thread,
            run_id=None,
            trigger_token_limit=500,
            keep_last_messages=2,
        )
        assert result is not None
        assert isinstance(result, CompactionResult)
        assert result.compacted_message_count > 0
        assert result.kept_message_count == 2
        assert result.estimated_tokens_after < result.estimated_tokens_before


def test_compact_thread_context_too_few_messages() -> None:
    """If there are fewer messages than keep_last, no compaction occurs."""
    with _db_session() as db:
        user = _make_user(db)
        thread = _make_thread(db, user.id)

        for i in range(3):
            _add_message(
                db,
                thread_id=thread.id,
                sequence_id=i + 1,
                role="user",
                content_text="short",
                token_estimate=200,
            )

        result = compact_thread_context(
            db,
            thread=thread,
            run_id=None,
            trigger_token_limit=100,
            keep_last_messages=5,
        )
        assert result is None


def test_compact_thread_context_reserved_prompt_tokens() -> None:
    """Reserved prompt tokens reduce the effective trigger limit."""
    with _db_session() as db:
        user = _make_user(db)
        thread = _make_thread(db, user.id)

        num_messages = 10
        for i in range(num_messages):
            _add_message(
                db,
                thread_id=thread.id,
                sequence_id=i + 1,
                role="user" if i % 2 == 0 else "assistant",
                content_text=f"Msg {i} " * 20,
                token_estimate=100,
            )

        # Update thread sequence counter so reserve_message_sequences works
        thread.next_message_sequence = num_messages + 1
        db.add(thread)
        db.commit()

        # 10*100=1000 tokens, trigger=1200 but reserved=500 → effective=700
        result = compact_thread_context(
            db,
            thread=thread,
            run_id=None,
            trigger_token_limit=1200,
            keep_last_messages=2,
            reserved_prompt_tokens=500,
        )
        assert result is not None
        assert result.reserved_prompt_tokens == 500
        assert result.effective_trigger_token_limit == 700


# --------------------------------------------------------------------------- #
# summarize_with_llm (routed through the provider chat client, ARH-001)
# --------------------------------------------------------------------------- #


class _FakeChatClient:
    def __init__(self, content: str = "", error: Exception | None = None) -> None:
        self._content = content
        self._error = error
        self.invocations: list[list] = []

    async def ainvoke(self, messages):
        self.invocations.append(messages)
        if self._error is not None:
            raise self._error

        class _Response:
            content = self._content

        return _Response()


def _patch_provider_client(monkeypatch, client):
    """Patch the client factory, capturing the kwargs it was called with."""
    import anima_server.services.agent.llm as llm_module

    captured: dict = {}

    def _factory(**kwargs):
        captured.update(kwargs)
        return client

    monkeypatch.setattr(llm_module, "create_provider_chat_client", _factory)
    return captured


@pytest.mark.asyncio
async def test_summarize_with_llm_uses_provider_client(monkeypatch) -> None:
    """The summarizer goes through create_provider_chat_client, never raw HTTP,
    so the Anthropic provider gets a working Messages-API call."""
    from anima_server.config import settings
    from anima_server.services.agent.compaction import summarize_with_llm

    monkeypatch.setattr(settings, "agent_provider", "anthropic")
    monkeypatch.setattr(settings, "agent_extraction_model", "")
    monkeypatch.setattr(settings, "agent_model", "claude-haiku-4-5-20251001")

    client = _FakeChatClient(content="  A tidy summary.  ")
    captured = _patch_provider_client(monkeypatch, client)

    result = await summarize_with_llm([], transcript_override="User: hi\nAssistant: hello")

    assert result == "A tidy summary."
    assert captured["provider"] == "anthropic"
    assert captured["model"] == "claude-haiku-4-5-20251001"
    assert len(client.invocations) == 1


@pytest.mark.asyncio
async def test_summarize_with_llm_prefers_extraction_model(monkeypatch) -> None:
    from anima_server.config import settings
    from anima_server.services.agent.compaction import summarize_with_llm

    monkeypatch.setattr(settings, "agent_provider", "anthropic")
    monkeypatch.setattr(settings, "agent_extraction_model", "claude-haiku-4-5-20251001")
    monkeypatch.setattr(settings, "agent_model", "claude-sonnet-5")

    captured = _patch_provider_client(monkeypatch, _FakeChatClient(content="ok"))

    result = await summarize_with_llm([], transcript_override="User: hi")

    assert result == "ok"
    assert captured["model"] == "claude-haiku-4-5-20251001"


@pytest.mark.asyncio
async def test_summarize_with_llm_falls_back_from_extraction_provider(monkeypatch) -> None:
    import anima_server.services.agent.llm as llm_module
    from anima_server.config import settings
    from anima_server.services.agent.compaction import summarize_with_llm

    monkeypatch.setattr(settings, "agent_provider", "openai")
    monkeypatch.setattr(settings, "agent_model", "gpt-5-mini")
    monkeypatch.setattr(settings, "agent_extraction_provider", "ollama")
    monkeypatch.setattr(settings, "agent_extraction_model", "all-minilm:latest")

    extraction = _FakeChatClient(error=RuntimeError("ollama returned 400"))
    primary = _FakeChatClient(content="Primary summary")
    extraction.closed = False
    primary.closed = False

    async def _close_extraction():
        extraction.closed = True

    async def _close_primary():
        primary.closed = True

    extraction.aclose = _close_extraction
    primary.aclose = _close_primary
    calls: list[tuple[str, str]] = []

    def _factory(**kwargs):
        calls.append((kwargs["provider"], kwargs["model"]))
        return extraction if len(calls) == 1 else primary

    monkeypatch.setattr(llm_module, "create_provider_chat_client", _factory)

    result = await summarize_with_llm([], transcript_override="User: hi")

    assert result == "Primary summary"
    assert calls == [
        ("ollama", "all-minilm:latest"),
        ("openai", "gpt-5-mini"),
    ]
    assert extraction.closed is True
    assert primary.closed is True


@pytest.mark.asyncio
async def test_summarize_with_llm_failure_logs_degraded_and_falls_back(
    monkeypatch, caplog
) -> None:
    """A summarizer failure returns None (fallback) and is visible at WARNING
    on the degraded logger instead of a silent debug line."""
    import logging

    from anima_server.config import settings
    from anima_server.services.agent.compaction import summarize_with_llm

    monkeypatch.setattr(settings, "agent_provider", "anthropic")
    monkeypatch.setattr(settings, "agent_extraction_model", "")

    _patch_provider_client(
        monkeypatch, _FakeChatClient(error=RuntimeError("provider down"))
    )

    with caplog.at_level(logging.WARNING, logger="anima.runtime.degraded"):
        result = await summarize_with_llm([], transcript_override="User: hi")

    assert result is None
    degraded = [r for r in caplog.records if r.name == "anima.runtime.degraded"]
    assert degraded, "expected a WARNING on anima.runtime.degraded"
    assert "falling back" in degraded[0].getMessage()


@pytest.mark.asyncio
async def test_summarize_with_llm_empty_output_falls_back(monkeypatch, caplog) -> None:
    import logging

    from anima_server.config import settings
    from anima_server.services.agent.compaction import summarize_with_llm

    monkeypatch.setattr(settings, "agent_provider", "anthropic")
    monkeypatch.setattr(settings, "agent_extraction_model", "")

    _patch_provider_client(monkeypatch, _FakeChatClient(content="   "))

    with caplog.at_level(logging.WARNING, logger="anima.runtime.degraded"):
        result = await summarize_with_llm([], transcript_override="User: hi")

    assert result is None
    assert any(r.name == "anima.runtime.degraded" for r in caplog.records)


@pytest.mark.asyncio
async def test_summarize_with_llm_scaffold_short_circuits(monkeypatch) -> None:
    from anima_server.config import settings
    from anima_server.services.agent.compaction import summarize_with_llm

    monkeypatch.setattr(settings, "agent_provider", "scaffold")

    def _fail(**kwargs):
        raise AssertionError("client factory must not be called for scaffold")

    import anima_server.services.agent.llm as llm_module

    monkeypatch.setattr(llm_module, "create_provider_chat_client", _fail)

    assert await summarize_with_llm([], transcript_override="User: hi") is None
