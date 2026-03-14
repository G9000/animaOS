from __future__ import annotations

import pytest

from anima_server.services.agent.adapters.openai_compatible import OpenAICompatibleAdapter
from anima_server.services.agent.runtime_types import LLMRequest, ToolCall
from anima_server.services.agent.tools import send_message


class FakeResponse:
    def __init__(self) -> None:
        self.content = "hello from adapter"
        self.tool_calls = [
            {
                "id": "call-1",
                "name": "send_message",
                "args": {"message": "hello from adapter"},
            }
        ]
        self.usage_metadata = {
            "input_tokens": 5,
            "output_tokens": 7,
            "total_tokens": 12,
        }


class FakeChatClient:
    def __init__(self, response: FakeResponse) -> None:
        self._response = response
        self.bound_tools: list[object] = []
        self.tool_choice: str | None = None
        self.invocations: list[list[object]] = []

    def bind_tools(
        self,
        tools: list[object],
        *,
        tool_choice: str | None = None,
        **_: object,
    ) -> "FakeChatClient":
        self.bound_tools = list(tools)
        self.tool_choice = tool_choice
        return self

    async def ainvoke(self, input: list[object]) -> FakeResponse:
        self.invocations.append(list(input))
        return self._response


@pytest.mark.asyncio
async def test_openai_compatible_adapter_uses_generic_chat_client_surface() -> None:
    client = FakeChatClient(FakeResponse())
    adapter = OpenAICompatibleAdapter(
        client,
        provider="openrouter",
        model="test-model",
    )

    result = await adapter.invoke(
        LLMRequest(
            messages=("message",),
            user_id=1,
            step_index=0,
            max_steps=4,
            system_prompt="system",
            available_tools=(send_message,),
            force_tool_call=True,
        )
    )

    assert client.tool_choice == "required"
    assert client.bound_tools == [send_message]
    assert client.invocations == [["message"]]
    assert result.assistant_text == "hello from adapter"
    assert result.tool_calls == (
        ToolCall(
            id="call-1",
            name="send_message",
            arguments={"message": "hello from adapter"},
        ),
    )
    assert result.usage is not None
    assert result.usage.prompt_tokens == 5
    assert result.usage.completion_tokens == 7
    assert result.usage.total_tokens == 12
