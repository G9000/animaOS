from __future__ import annotations

from abc import ABC, abstractmethod

from anima_server.services.agent.runtime_types import LLMRequest, StepExecutionResult


class BaseLLMAdapter(ABC):
    provider: str
    model: str

    def prepare(self) -> None:
        """Validate adapter readiness before the runtime starts."""

    @abstractmethod
    async def invoke(self, request: LLMRequest) -> StepExecutionResult:
        """Execute one model step and normalize the result."""
