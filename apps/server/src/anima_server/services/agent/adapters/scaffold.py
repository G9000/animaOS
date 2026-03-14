from __future__ import annotations

from langchain_core.messages import HumanMessage

from anima_server.services.agent.messages import message_content, render_scaffold_response
from anima_server.services.agent.runtime_types import LLMRequest, StepExecutionResult

from .base import BaseLLMAdapter


class ScaffoldAdapter(BaseLLMAdapter):
    provider = "scaffold"
    model = "python-agent-scaffold"

    def prepare(self) -> None:
        return None

    async def invoke(self, request: LLMRequest) -> StepExecutionResult:
        human_turns = [
            message
            for message in request.messages
            if isinstance(message, HumanMessage)
        ]
        user_message = message_content(human_turns[-1]) if human_turns else ""
        response = render_scaffold_response(
            user_id=request.user_id,
            user_message=user_message,
            turn_number=len(human_turns),
        )
        return StepExecutionResult(assistant_text=response)
