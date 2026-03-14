from __future__ import annotations

from anima_server.services.agent.messages import (
    is_user_message,
    message_content,
    render_scaffold_response,
)
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
            if is_user_message(message)
        ]
        user_message = message_content(human_turns[-1]) if human_turns else ""
        turn_number = (
            request.conversation_turn_count
            if request.conversation_turn_count is not None
            else len(human_turns)
        )
        response = render_scaffold_response(
            user_id=request.user_id,
            user_message=user_message,
            turn_number=turn_number,
        )
        return StepExecutionResult(assistant_text=response)
