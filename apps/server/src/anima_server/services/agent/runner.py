from __future__ import annotations

from typing import Any

from anima_server.config import settings
from anima_server.services.agent.messages import (
    build_conversation_messages,
    extract_last_ai_content,
    extract_tools_used,
)
from anima_server.services.agent.state import AgentResult, StoredMessage


class GraphRunner:
    """Wrap a compiled LangGraph and normalize its output."""

    def __init__(self, graph: Any, *, is_scaffold: bool = False) -> None:
        self._graph = graph
        self._is_scaffold = is_scaffold

    async def invoke(
        self,
        user_message: str,
        user_id: int,
        history: list[StoredMessage],
    ) -> AgentResult:
        messages = build_conversation_messages(history, user_message)
        result = await self._graph.ainvoke({"messages": messages, "user_id": user_id})

        response = extract_last_ai_content(result.get("messages", []))
        tools_used = extract_tools_used(result.get("messages", []))

        provider = "scaffold" if self._is_scaffold else settings.agent_provider
        model = "python-agent-scaffold" if self._is_scaffold else settings.agent_model

        return AgentResult(
            response=response,
            model=model,
            provider=provider,
            tools_used=tools_used,
        )
