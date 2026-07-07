from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from anima_server.services.agent.runtime_types import ToolCall
from anima_server.services.agent.state import StoredAttachment, StoredMessage

_TOOL_RULE_VIOLATION_PREFIX = "Tool rule violation:"

# Cap on tool output entering the conversation (live and replayed).  The
# executor's 50k trace cap stays on the step record; without a separate
# history cap a single large tool result was re-billed on every
# subsequent LLM call until compaction.
TOOL_HISTORY_CHAR_LIMIT = 8_000


def _clamp_tool_history_content(content: str) -> str:
    if len(content) <= TOOL_HISTORY_CHAR_LIMIT:
        return content

    note = (
        f"... [NOTE: tool output clamped for conversation history, "
        f"{len(content)} chars total; the step trace holds the full output]"
    )
    # Tool results usually arrive as the executor's JSON envelope —
    # truncate inside the message field so the model keeps seeing valid
    # JSON instead of a cut-off document.
    try:
        envelope = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        envelope = None
    if isinstance(envelope, dict) and isinstance(envelope.get("message"), str):
        overflow = len(content) - TOOL_HISTORY_CHAR_LIMIT
        message = envelope["message"]
        keep = max(len(message) - overflow, 0)
        envelope["message"] = message[:keep] + note
        return json.dumps(envelope)
    return content[:TOOL_HISTORY_CHAR_LIMIT] + note


@dataclass
class SystemMessage:
    content: str
    type: str = "system"
    # Prompt-cache boundary: content[:stable_prefix_chars] is byte-stable
    # across turns and safe to cache; 0 means no boundary (whole message
    # treated as volatile).  Only the leading system message carries it.
    stable_prefix_chars: int = 0


@dataclass
class HumanMessage:
    content: Any
    type: str = "human"


@dataclass
class AIMessage:
    content: str
    tool_calls: list[dict[str, object]] = field(default_factory=list)
    usage_metadata: dict[str, object] | None = None
    response_metadata: dict[str, object] | None = None
    type: str = "ai"


@dataclass
class ToolMessage:
    content: str
    tool_call_id: str
    name: str | None = None
    type: str = "tool"


def build_conversation_messages(
    history: list[StoredMessage],
    user_message: str | None,
    *,
    system_prompt: str,
    system_stable_prefix_chars: int = 0,
    user_attachments: Sequence[StoredAttachment] = (),
) -> list[Any]:
    messages: list[Any] = [
        make_system_message(
            system_prompt, stable_prefix_chars=system_stable_prefix_chars
        )
    ]
    messages.extend(
        to_runtime_message(message)
        for message in history
        if not _is_stale_tool_rule_violation_message(message)
    )
    if user_message is not None:
        messages.append(make_user_message(user_message, attachments=user_attachments))
    return _sanitize_tool_message_ordering(messages)


def _is_stale_tool_rule_violation_message(message: StoredMessage) -> bool:
    return message.role == "tool" and message.content.startswith(_TOOL_RULE_VIOLATION_PREFIX)


def _sanitize_tool_message_ordering(messages: list[Any]) -> list[Any]:
    """Strip orphaned tool messages that lack a preceding assistant tool_calls.

    OpenAI-compatible APIs require every ``tool`` role message to follow an
    ``assistant`` message containing ``tool_calls``.  After compaction or
    summary-based history truncation, tool messages can become orphaned.
    Drop them to prevent 400 errors.
    """
    result: list[Any] = []
    saw_tool_calls = False
    for msg in messages:
        msg_type = getattr(msg, "type", "")
        if msg_type == "ai":
            saw_tool_calls = bool(getattr(msg, "tool_calls", None))
            result.append(msg)
        elif msg_type == "tool":
            if saw_tool_calls:
                result.append(msg)
        else:
            saw_tool_calls = False
            result.append(msg)
    return result


def to_runtime_message(message: StoredMessage) -> Any:
    if message.role in {"summary", "system"}:
        return make_summary_message(message.content)
    if message.role == "assistant":
        # Thinking re-injection: when a stored assistant message has
        # both content (the inner thought) and non-terminal tool_calls,
        # re-inject the content into the tool call args as ``thinking``
        # and clear the message body so the model sees history consistent
        # with the injected schema.
        # Guard: only re-inject when tool_calls are present and none is
        # the terminal ``send_message`` (whose content is real assistant
        # text, not inner thinking).  This also protects legacy history
        # rows written before the thinking kwarg was introduced.
        has_non_terminal_tools = message.tool_calls and not any(
            tc.name == "send_message" for tc in message.tool_calls
        )
        inner = (
            message.content.strip()
            if has_non_terminal_tools and message.content and message.content.strip()
            else None
        )
        return make_assistant_message(
            message.content,
            tool_calls=message.tool_calls,
            inner_thoughts=inner,
        )
    if message.role == "tool":
        return make_tool_message(
            message.content,
            tool_call_id=message.tool_call_id or message.tool_name or "tool",
            name=message.tool_name,
        )
    return make_user_message(message.content, attachments=message.attachments)


def make_system_message(content: str, *, stable_prefix_chars: int = 0) -> Any:
    return SystemMessage(content=content, stable_prefix_chars=stable_prefix_chars)


def make_summary_message(content: str) -> Any:
    return SystemMessage(content=content)


def make_user_message(
    content: str,
    *,
    attachments: Sequence[StoredAttachment] = (),
) -> Any:
    if not attachments:
        return HumanMessage(content=content)

    blocks: list[dict[str, object]] = []
    if content.strip():
        blocks.append({"type": "text", "text": content})
    for attachment in attachments:
        blocks.append(
            {
                "type": "image",
                "mime_type": attachment.mime_type,
                "path": attachment.path,
            }
        )
    return HumanMessage(content=blocks)


def make_assistant_message(
    content: str,
    *,
    tool_calls: Sequence[ToolCall] = (),
    inner_thoughts: str | None = None,
) -> Any:
    if inner_thoughts and tool_calls:
        # Re-injection for history consistency: move the inner thought
        # into each tool call's ``thinking`` kwarg and clear message
        # content so the model sees history matching the injected schema.
        return AIMessage(
            content="",
            tool_calls=[
                to_tool_call_payload(tc, inner_thoughts=inner_thoughts) for tc in tool_calls
            ],
        )
    return AIMessage(
        content=content,
        tool_calls=[to_tool_call_payload(tool_call) for tool_call in tool_calls],
    )


def make_tool_message(
    content: str,
    *,
    tool_call_id: str,
    name: str | None = None,
) -> Any:
    return ToolMessage(
        content=_clamp_tool_history_content(content),
        tool_call_id=tool_call_id,
        name=name,
    )


def is_assistant_message(message: Any) -> bool:
    return isinstance(message, AIMessage)


def is_user_message(message: Any) -> bool:
    return isinstance(message, HumanMessage)


def extract_last_assistant_content(messages: list[Any]) -> str:
    for message in reversed(messages):
        if is_assistant_message(message) and message_content(message):
            return message_content(message)
    return ""


def extract_tools_used(messages: list[Any]) -> list[str]:
    names: list[str] = []
    for message in messages:
        for tool_call in message_tool_calls(message):
            name = (
                tool_call.get("name", "")
                if isinstance(tool_call, dict)
                else getattr(tool_call, "name", "")
            )
            if name and name not in names:
                names.append(name)
    return names


def message_content(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                parts.append(str(item["text"]))
            elif item.get("type") == "image":
                parts.append("[image]")
        return " ".join(part for part in parts if part)
    return str(content)


def message_tool_calls(message: Any) -> Sequence[Any]:
    raw_tool_calls = getattr(message, "tool_calls", ())
    if isinstance(raw_tool_calls, (list, tuple)):
        return raw_tool_calls
    return ()


def message_usage_payload(message: Any) -> dict[str, object] | None:
    raw_usage = getattr(message, "usage_metadata", None)
    if isinstance(raw_usage, dict):
        return raw_usage

    response_metadata = getattr(message, "response_metadata", None)
    if not isinstance(response_metadata, dict):
        return None

    usage_payload = response_metadata.get("token_usage") or response_metadata.get("usage")
    return usage_payload if isinstance(usage_payload, dict) else None


def render_scaffold_response(
    user_id: int,
    user_message: str,
    turn_number: int,
) -> str:
    normalized_message = user_message.strip() or "[empty]"
    return (
        f"Python agent scaffold is active for user {user_id}. "
        f"This is turn {turn_number}. Replace the scaffold runtime with a real model call. "
        f"Last message: {normalized_message}"
    )


def to_tool_call_payload(
    tool_call: ToolCall,
    *,
    inner_thoughts: str | None = None,
) -> dict[str, object]:
    args = dict(tool_call.arguments)
    if inner_thoughts:
        # Insert 'thinking' as the first key, matching the injected schema.
        args = {"thinking": inner_thoughts, **args}
    payload: dict[str, object] = {
        "id": tool_call.id,
        "name": tool_call.name,
        "args": args,
        "type": "tool_call",
    }
    if tool_call.parse_error is not None:
        payload["parse_error"] = tool_call.parse_error
    if tool_call.raw_arguments is not None:
        payload["raw_arguments"] = tool_call.raw_arguments
    return payload
