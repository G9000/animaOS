from __future__ import annotations

from dataclasses import dataclass, field


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
