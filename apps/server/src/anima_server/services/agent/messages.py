from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from anima_server.services.agent.runtime_types import ToolCall
from anima_server.services.agent.state import StoredMessage

_TOOL_RULE_VIOLATION_PREFIX = "Tool rule violation:"


@dataclass
class SystemMessage:
    content: str
    type: str = "system"


@dataclass
class HumanMessage:
    content: str
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
) -> list[Any]:
    messages: list[Any] = [make_system_message(system_prompt)]
    messages.extend(
        to_runtime_message(message)
        for message in history
        if not _is_stale_tool_rule_violation_message(message)
    )
    if user_message is not None:
        messages.append(make_user_message(user_message))
    return messages


def _is_stale_tool_rule_violation_message(message: StoredMessage) -> bool:
    return (
        message.role == "tool"
        and message.content.startswith(_TOOL_RULE_VIOLATION_PREFIX)
    )


def to_runtime_message(message: StoredMessage) -> Any:
    if message.role in {"summary", "system"}:
        return make_summary_message(message.content)
    if message.role == "assistant":
        return make_assistant_message(
            message.content,
            tool_calls=message.tool_calls,
        )
    if message.role == "tool":
        return make_tool_message(
            message.content,
            tool_call_id=message.tool_call_id or message.tool_name or "tool",
            name=message.tool_name,
        )
    return make_user_message(message.content)


def make_system_message(content: str) -> Any:
    return SystemMessage(content=content)


def make_summary_message(content: str) -> Any:
    return SystemMessage(content=content)


def make_user_message(content: str) -> Any:
    return HumanMessage(content=content)


def make_assistant_message(
    content: str,
    *,
    tool_calls: Sequence[ToolCall] = (),
) -> Any:
    return AIMessage(
        content=content,
        tool_calls=[to_tool_call_payload(tool_call)
                    for tool_call in tool_calls],
    )


def make_tool_message(
    content: str,
    *,
    tool_call_id: str,
    name: str | None = None,
) -> Any:
    return ToolMessage(
        content=content,
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
    return content if isinstance(content, str) else str(content)


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

    usage_payload = response_metadata.get(
        "token_usage") or response_metadata.get("usage")
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


def to_tool_call_payload(tool_call: ToolCall) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": tool_call.id,
        "name": tool_call.name,
        "args": dict(tool_call.arguments),
        "type": "tool_call",
    }
    if tool_call.parse_error is not None:
        payload["parse_error"] = tool_call.parse_error
    if tool_call.raw_arguments is not None:
        payload["raw_arguments"] = tool_call.raw_arguments
    return payload
