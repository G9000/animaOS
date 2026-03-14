from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from anima_server.services.agent.state import StoredMessage

SYSTEM_PROMPT = (
    "You are Anima, a thoughtful and capable AI assistant. "
    "Be concise and direct. Use tools when they would help answer the user's question."
)


def build_conversation_messages(
    history: list[StoredMessage],
    user_message: str,
) -> list[Any]:
    messages: list[Any] = [SystemMessage(content=SYSTEM_PROMPT)]
    messages.extend(to_langchain_message(message) for message in history)
    messages.append(HumanMessage(content=user_message))
    return messages


def to_langchain_message(message: StoredMessage) -> Any:
    if message.role == "assistant":
        return AIMessage(content=message.content)
    return HumanMessage(content=message.content)


def extract_last_ai_content(messages: list[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage) and message_content(message):
            return message_content(message)
    return ""


def extract_tools_used(messages: list[Any]) -> list[str]:
    names: list[str] = []
    for message in messages:
        if not hasattr(message, "tool_calls"):
            continue
        for tool_call in message.tool_calls:
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


def render_scaffold_response(
    user_id: int,
    user_message: str,
    turn_number: int,
) -> str:
    normalized_message = user_message.strip() or "[empty]"
    return (
        f"Python agent graph scaffold is active for user {user_id}. "
        f"This is turn {turn_number}. Replace the scaffold node with a real model call. "
        f"Last message: {normalized_message}"
    )
