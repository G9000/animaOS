from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from threading import Lock
from typing import Annotated, Any, Protocol, TypedDict

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from anima_server.config import settings


@dataclass(slots=True)
class AgentResult:
    response: str
    model: str
    provider: str
    tools_used: list[str]


@dataclass(frozen=True, slots=True)
class StoredMessage:
    role: str
    content: str


class AgentGraphRunner(Protocol):
    async def invoke(
        self,
        user_message: str,
        user_id: int,
        history: list[StoredMessage],
    ) -> AgentResult: ...


class _ThreadStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._threads: dict[int, list[StoredMessage]] = {}

    def read(self, user_id: int) -> list[StoredMessage]:
        with self._lock:
            return list(self._threads.get(user_id, []))

    def append_turn(self, user_id: int, user_message: str, assistant_message: str) -> None:
        with self._lock:
            thread = self._threads.setdefault(user_id, [])
            thread.append(StoredMessage(role="user", content=user_message))
            thread.append(StoredMessage(role="assistant", content=assistant_message))

    def reset(self, user_id: int) -> None:
        with self._lock:
            self._threads.pop(user_id, None)

    def clear(self) -> None:
        with self._lock:
            self._threads.clear()


@dataclass(slots=True)
class _LangGraphRunner:
    graph: Any

    async def invoke(
        self,
        user_message: str,
        user_id: int,
        history: list[StoredMessage],
    ) -> AgentResult:
        messages = [_to_langchain_message(message) for message in history]
        messages.append(HumanMessage(content=user_message))

        result = await self.graph.ainvoke({"messages": messages, "user_id": user_id})
        response = _extract_ai_content(result.get("messages", []), AIMessage)
        return AgentResult(
            response=response,
            model=settings.agent_model,
            provider=settings.agent_provider,
            tools_used=[],
        )


class _AgentState(TypedDict):
    messages: Annotated[list[Any], add_messages]
    user_id: int


_thread_store = _ThreadStore()
_graph_lock = Lock()
_cached_graph: AgentGraphRunner | None = None


def get_or_build_agent_graph() -> AgentGraphRunner:
    global _cached_graph

    if _cached_graph is not None:
        return _cached_graph

    with _graph_lock:
        if _cached_graph is None:
            _cached_graph = _build_agent_graph()
        return _cached_graph


def invalidate_agent_graph_cache() -> None:
    global _cached_graph
    with _graph_lock:
        _cached_graph = None


def clear_agent_threads() -> None:
    _thread_store.clear()


async def run_agent(user_message: str, user_id: int) -> AgentResult:
    history = _thread_store.read(user_id)
    graph = get_or_build_agent_graph()
    result = await graph.invoke(user_message, user_id, history)
    _thread_store.append_turn(user_id, user_message, result.response)
    return result


async def stream_agent(
    user_message: str,
    user_id: int,
) -> AsyncGenerator[str, None]:
    result = await run_agent(user_message, user_id)
    async for chunk in _chunk_text(result.response, size=settings.agent_stream_chunk_size):
        yield chunk


async def reset_agent_thread(user_id: int) -> None:
    _thread_store.reset(user_id)


def _build_agent_graph() -> AgentGraphRunner:
    async def model_request(state: _AgentState) -> dict[str, list[Any]]:
        messages = state["messages"]
        human_turns = [
            message for message in messages if getattr(message, "type", "") == "human"
        ]
        user_message = _message_content(human_turns[-1]) if human_turns else ""
        response = _render_scaffold_response(
            user_id=state["user_id"],
            user_message=user_message,
            turn_number=len(human_turns),
        )
        return {"messages": [AIMessage(content=response)]}

    graph = StateGraph(_AgentState)
    graph.add_node("model_request", model_request)
    graph.set_entry_point("model_request")
    graph.add_edge("model_request", END)
    return _LangGraphRunner(graph=graph.compile())


def _to_langchain_message(message: StoredMessage) -> Any:
    if message.role == "assistant":
        return AIMessage(content=message.content)
    return HumanMessage(content=message.content)


def _extract_ai_content(messages: list[Any], ai_message_type: type[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, ai_message_type):
            return _message_content(message)
    return _render_scaffold_response(user_id=0, user_message="", turn_number=0)


def _message_content(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    return str(content)


def _render_scaffold_response(
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


async def _chunk_text(text: str, size: int) -> AsyncGenerator[str, None]:
    normalized_size = max(1, size)
    for start in range(0, len(text), normalized_size):
        await asyncio.sleep(0)
        yield text[start : start + normalized_size]
