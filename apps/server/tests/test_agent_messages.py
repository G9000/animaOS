from __future__ import annotations

from anima_server.services.agent.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    build_conversation_messages,
    extract_last_assistant_content,
    extract_tools_used,
    is_assistant_message,
    is_user_message,
    make_assistant_message,
    make_summary_message,
    make_system_message,
    make_tool_message,
    make_user_message,
    message_content,
    message_tool_calls,
    message_usage_payload,
    render_scaffold_response,
    to_runtime_message,
    to_tool_call_payload,
)
from anima_server.services.agent.runtime_types import ToolCall
from anima_server.services.agent.state import StoredMessage


# --------------------------------------------------------------------------- #
# Message constructors
# --------------------------------------------------------------------------- #


def test_make_system_message_returns_system() -> None:
    msg = make_system_message("You are helpful.")
    assert isinstance(msg, SystemMessage)
    assert msg.content == "You are helpful."
    assert msg.type == "system"


def test_make_summary_message_returns_system_type() -> None:
    msg = make_summary_message("Summary of earlier messages.")
    assert isinstance(msg, SystemMessage)
    assert msg.content == "Summary of earlier messages."


def test_make_user_message_returns_human() -> None:
    msg = make_user_message("Hi there")
    assert isinstance(msg, HumanMessage)
    assert msg.content == "Hi there"
    assert msg.type == "human"


def test_make_assistant_message_without_tools() -> None:
    msg = make_assistant_message("Hello!")
    assert isinstance(msg, AIMessage)
    assert msg.content == "Hello!"
    assert msg.tool_calls == []


def test_make_assistant_message_with_tool_calls() -> None:
    tc = ToolCall(id="c1", name="search", arguments={"q": "cats"})
    msg = make_assistant_message("Looking it up...", tool_calls=(tc,))
    assert isinstance(msg, AIMessage)
    assert len(msg.tool_calls) == 1
    assert msg.tool_calls[0]["name"] == "search"
    assert msg.tool_calls[0]["args"] == {"q": "cats"}


def test_make_tool_message() -> None:
    msg = make_tool_message("result data", tool_call_id="c1", name="search")
    assert isinstance(msg, ToolMessage)
    assert msg.content == "result data"
    assert msg.tool_call_id == "c1"
    assert msg.name == "search"
    assert msg.type == "tool"


# --------------------------------------------------------------------------- #
# to_runtime_message
# --------------------------------------------------------------------------- #


def test_to_runtime_message_user() -> None:
    stored = StoredMessage(role="user", content="hello")
    result = to_runtime_message(stored)
    assert isinstance(result, HumanMessage)
    assert result.content == "hello"


def test_to_runtime_message_assistant() -> None:
    stored = StoredMessage(role="assistant", content="hi back")
    result = to_runtime_message(stored)
    assert isinstance(result, AIMessage)
    assert result.content == "hi back"


def test_to_runtime_message_tool() -> None:
    stored = StoredMessage(
        role="tool",
        content="tool output",
        tool_call_id="c1",
        tool_name="search",
    )
    result = to_runtime_message(stored)
    assert isinstance(result, ToolMessage)
    assert result.tool_call_id == "c1"
    assert result.name == "search"


def test_to_runtime_message_tool_fallback_id() -> None:
    """Tool messages without a call_id fall back to tool_name then 'tool'."""
    stored = StoredMessage(
        role="tool",
        content="output",
        tool_name="mytool",
    )
    result = to_runtime_message(stored)
    assert isinstance(result, ToolMessage)
    assert result.tool_call_id == "mytool"


def test_to_runtime_message_summary() -> None:
    stored = StoredMessage(role="summary", content="earlier context")
    result = to_runtime_message(stored)
    assert isinstance(result, SystemMessage)


def test_to_runtime_message_system() -> None:
    stored = StoredMessage(role="system", content="sys msg")
    result = to_runtime_message(stored)
    assert isinstance(result, SystemMessage)


# --------------------------------------------------------------------------- #
# build_conversation_messages
# --------------------------------------------------------------------------- #


def test_build_conversation_messages_structure() -> None:
    history = [
        StoredMessage(role="user", content="first"),
        StoredMessage(role="assistant", content="reply"),
    ]
    messages = build_conversation_messages(
        history, "second", system_prompt="Be nice."
    )
    assert len(messages) == 4
    assert isinstance(messages[0], SystemMessage)
    assert messages[0].content == "Be nice."
    assert isinstance(messages[1], HumanMessage)
    assert isinstance(messages[2], AIMessage)
    assert isinstance(messages[3], HumanMessage)
    assert messages[3].content == "second"


def test_build_conversation_messages_empty_history() -> None:
    messages = build_conversation_messages(
        [], "hello", system_prompt="prompt"
    )
    assert len(messages) == 2
    assert isinstance(messages[0], SystemMessage)
    assert isinstance(messages[1], HumanMessage)


# --------------------------------------------------------------------------- #
# Type predicates
# --------------------------------------------------------------------------- #


def test_is_assistant_message() -> None:
    assert is_assistant_message(AIMessage(content="hi")) is True
    assert is_assistant_message(HumanMessage(content="hi")) is False
    assert is_assistant_message("not a message") is False


def test_is_user_message() -> None:
    assert is_user_message(HumanMessage(content="hi")) is True
    assert is_user_message(AIMessage(content="hi")) is False


# --------------------------------------------------------------------------- #
# Content / tool call extractors
# --------------------------------------------------------------------------- #


def test_message_content_string() -> None:
    assert message_content(AIMessage(content="hello")) == "hello"


def test_message_content_non_string() -> None:
    """Non-string content is stringified."""

    class FakeMsg:
        content = 42  # type: ignore[assignment]

    assert message_content(FakeMsg()) == "42"


def test_message_content_missing() -> None:
    """Objects without 'content' return empty string."""
    assert message_content(object()) == ""


def test_message_tool_calls_with_list() -> None:
    msg = AIMessage(content="", tool_calls=[{"name": "a"}])
    assert message_tool_calls(msg) == [{"name": "a"}]


def test_message_tool_calls_none() -> None:
    assert message_tool_calls(object()) == ()


def test_message_tool_calls_non_list() -> None:
    """Non-list tool_calls attribute returns empty tuple."""

    class FakeMsg:
        tool_calls = "bad"

    assert message_tool_calls(FakeMsg()) == ()


# --------------------------------------------------------------------------- #
# extract_last_assistant_content
# --------------------------------------------------------------------------- #


def test_extract_last_assistant_content_found() -> None:
    messages = [
        AIMessage(content="first"),
        HumanMessage(content="user"),
        AIMessage(content="last"),
    ]
    assert extract_last_assistant_content(messages) == "last"


def test_extract_last_assistant_content_empty_skipped() -> None:
    messages = [
        AIMessage(content="first"),
        AIMessage(content=""),
    ]
    assert extract_last_assistant_content(messages) == "first"


def test_extract_last_assistant_content_none() -> None:
    messages = [HumanMessage(content="user")]
    assert extract_last_assistant_content(messages) == ""


# --------------------------------------------------------------------------- #
# extract_tools_used
# --------------------------------------------------------------------------- #


def test_extract_tools_used_dict_tool_calls() -> None:
    messages = [
        AIMessage(
            content="",
            tool_calls=[{"name": "search"}, {"name": "recall"}],
        ),
        AIMessage(content="", tool_calls=[{"name": "search"}]),
    ]
    assert extract_tools_used(messages) == ["search", "recall"]


def test_extract_tools_used_empty() -> None:
    messages = [HumanMessage(content="hi")]
    assert extract_tools_used(messages) == []


# --------------------------------------------------------------------------- #
# message_usage_payload
# --------------------------------------------------------------------------- #


def test_message_usage_payload_from_usage_metadata() -> None:
    msg = AIMessage(content="", usage_metadata={"total_tokens": 42})
    assert message_usage_payload(msg) == {"total_tokens": 42}


def test_message_usage_payload_from_response_metadata_token_usage() -> None:
    msg = AIMessage(
        content="",
        response_metadata={"token_usage": {"total_tokens": 10}},
    )
    assert message_usage_payload(msg) == {"total_tokens": 10}


def test_message_usage_payload_from_response_metadata_usage() -> None:
    msg = AIMessage(
        content="",
        response_metadata={"usage": {"prompt_tokens": 5}},
    )
    assert message_usage_payload(msg) == {"prompt_tokens": 5}


def test_message_usage_payload_none() -> None:
    msg = AIMessage(content="")
    assert message_usage_payload(msg) is None


def test_message_usage_payload_non_dict_response_metadata() -> None:
    """Non-dict response_metadata returns None."""
    msg = AIMessage(content="", response_metadata="bad")  # type: ignore[arg-type]
    assert message_usage_payload(msg) is None


# --------------------------------------------------------------------------- #
# render_scaffold_response
# --------------------------------------------------------------------------- #


def test_render_scaffold_response_basic() -> None:
    text = render_scaffold_response(user_id=1, user_message="hi", turn_number=3)
    assert "user 1" in text
    assert "turn 3" in text
    assert "hi" in text


def test_render_scaffold_response_empty_message() -> None:
    text = render_scaffold_response(user_id=2, user_message="  ", turn_number=1)
    assert "[empty]" in text


# --------------------------------------------------------------------------- #
# to_tool_call_payload
# --------------------------------------------------------------------------- #


def test_to_tool_call_payload_basic() -> None:
    tc = ToolCall(id="c1", name="search", arguments={"q": "cats"})
    payload = to_tool_call_payload(tc)
    assert payload == {
        "id": "c1",
        "name": "search",
        "args": {"q": "cats"},
        "type": "tool_call",
    }


def test_to_tool_call_payload_with_parse_error() -> None:
    tc = ToolCall(
        id="c2",
        name="broken",
        arguments={},
        parse_error="invalid json",
        raw_arguments='{"bad',
    )
    payload = to_tool_call_payload(tc)
    assert payload["parse_error"] == "invalid json"
    assert payload["raw_arguments"] == '{"bad'


def test_to_tool_call_payload_omits_none_error_fields() -> None:
    tc = ToolCall(id="c3", name="clean", arguments={})
    payload = to_tool_call_payload(tc)
    assert "parse_error" not in payload
    assert "raw_arguments" not in payload
