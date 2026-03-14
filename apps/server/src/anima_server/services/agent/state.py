from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    messages: Annotated[list[Any], add_messages]
    user_id: int


@dataclass(frozen=True, slots=True)
class StoredMessage:
    role: str
    content: str


@dataclass(slots=True)
class AgentResult:
    response: str
    model: str
    provider: str
    tools_used: list[str] = field(default_factory=list)
