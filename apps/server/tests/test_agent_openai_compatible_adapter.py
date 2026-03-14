from __future__ import annotations

import pytest

from anima_server.services.agent.adapters.openai_compatible import OpenAICompatibleAdapter
from anima_server.services.agent.openai_compatible_client import OpenAICompatibleStreamChunk
from anima_server.services.agent.runtime_types import LLMRequest, StepStreamEvent, ToolCall
from anima_server.services.agent.tools import send_message


class FakeResponse:
    def __init__(self) -> None:
        self.content = "<think>private reasoning</think>\n\nhello from adapter"
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

    async def astream(self, input: list[object]):
        self.invocations.append(list(input))
        yield OpenAICompatibleStreamChunk(content_delta="<think>private")
        yield OpenAICompatibleStreamChunk(
            content_delta=" reasoning</think>\n\nhello ",
        )
        yield OpenAICompatibleStreamChunk(
            content_delta="world",
            tool_call_deltas=(
                {
                    "index": 0,
                    "id": "call-1",
                    "name": "send_message",
                    "arguments": '{"message":"hello world"}',
                },
            ),
            usage_metadata={
                "input_tokens": 5,
                "output_tokens": 7,
                "total_tokens": 12,
            },
            done=True,
        )


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


@pytest.mark.asyncio
async def test_openai_compatible_adapter_streams_chunks_and_final_result() -> None:
    client = FakeChatClient(FakeResponse())
    adapter = OpenAICompatibleAdapter(
        client,
        provider="ollama",
        model="test-model",
    )

    events = [
        event
        async for event in adapter.stream(
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
    ]

    assert events[:2] == [
        StepStreamEvent(content_delta="hello "),
        StepStreamEvent(content_delta="world"),
    ]
    final_event = events[-1]
    assert final_event.result is not None
    assert final_event.result.assistant_text == "hello world"
    assert final_event.result.tool_calls == (
        ToolCall(
            id="call-1",
            name="send_message",
            arguments={"message": "hello world"},
        ),
    )
    assert final_event.result.usage is not None
    assert final_event.result.usage.total_tokens == 12


@pytest.mark.asyncio
async def test_openai_compatible_adapter_strips_split_reasoning_tags() -> None:
    class SplitReasoningChatClient(FakeChatClient):
        async def astream(self, input: list[object]):
            self.invocations.append(list(input))
            yield OpenAICompatibleStreamChunk(content_delta="<thi")
            yield OpenAICompatibleStreamChunk(content_delta="nk>hidden")
            yield OpenAICompatibleStreamChunk(content_delta=" plan</th")
            yield OpenAICompatibleStreamChunk(content_delta="ink>\n\nvisible ")
            yield OpenAICompatibleStreamChunk(
                content_delta="answer",
                done=True,
            )

    client = SplitReasoningChatClient(FakeResponse())
    adapter = OpenAICompatibleAdapter(
        client,
        provider="ollama",
        model="test-model",
    )

    events = [
        event
        async for event in adapter.stream(
            LLMRequest(
                messages=("message",),
                user_id=1,
                step_index=0,
                max_steps=4,
                system_prompt="system",
            )
        )
    ]

    assert events[:2] == [
        StepStreamEvent(content_delta="visible "),
        StepStreamEvent(content_delta="answer"),
    ]
    final_event = events[-1]
    assert final_event.result is not None
    assert final_event.result.assistant_text == "visible answer"


@pytest.mark.asyncio
async def test_openai_compatible_adapter_preserves_sync_tool_parse_errors() -> None:
    client = FakeChatClient(FakeResponse())
    client._response.tool_calls = [
        {
            "id": "call-bad",
            "name": "send_message",
            "args": {},
            "parse_error": "Malformed tool-call arguments (invalid JSON).",
            "raw_arguments": "{broken json",
        }
    ]
    adapter = OpenAICompatibleAdapter(
        client,
        provider="ollama",
        model="test-model",
    )

    result = await adapter.invoke(
        LLMRequest(
            messages=("message",),
            user_id=1,
            step_index=0,
            max_steps=4,
            system_prompt="system",
        )
    )

    assert result.tool_calls == (
        ToolCall(
            id="call-bad",
            name="send_message",
            arguments={},
            parse_error="Malformed tool-call arguments (invalid JSON).",
            raw_arguments="{broken json",
        ),
    )


@pytest.mark.asyncio
async def test_openai_compatible_adapter_streams_structured_parse_errors() -> None:
    class BrokenArgsChatClient(FakeChatClient):
        async def astream(self, input: list[object]):
            self.invocations.append(list(input))
            yield OpenAICompatibleStreamChunk(
                tool_call_deltas=(
                    {
                        "index": 0,
                        "id": "call-bad",
                        "name": "send_message",
                        "arguments": "{broken json",
                    },
                ),
                done=True,
            )

    client = BrokenArgsChatClient(FakeResponse())
    adapter = OpenAICompatibleAdapter(
        client,
        provider="ollama",
        model="test-model",
    )

    events = [
        event
        async for event in adapter.stream(
            LLMRequest(
                messages=("message",),
                user_id=1,
                step_index=0,
                max_steps=4,
                system_prompt="system",
            )
        )
    ]

    final_event = events[-1]
    assert final_event.result is not None
    assert final_event.result.tool_calls == (
        ToolCall(
            id="call-bad",
            name="send_message",
            arguments={},
            parse_error="Malformed tool-call arguments (invalid JSON).",
            raw_arguments="{broken json",
        ),
    )
