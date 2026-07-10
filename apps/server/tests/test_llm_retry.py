"""Tests for LLM retry with exponential backoff and context overflow recovery."""

from __future__ import annotations

import asyncio
from collections import deque
from unittest.mock import patch

import pytest
from anima_server.services.agent.adapters.base import BaseLLMAdapter
from anima_server.services.agent.llm import (
    ContextWindowOverflowError,
    LLMInvocationError,
    _is_context_overflow_message,
    wrap_llm_error,
)
from anima_server.services.agent.rules import TerminalToolRule
from anima_server.services.agent.runtime import AgentRuntime, _is_retryable_error
from anima_server.services.agent.runtime_types import (
    LLMRequest,
    StepExecutionResult,
    StepFailedError,
    StopReason,
    ToolCall,
)
from anima_server.services.agent.tools import send_message

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FailThenSucceedAdapter(BaseLLMAdapter):
    """Adapter that fails N times with a given exception, then succeeds."""

    provider = "test"
    model = "test-model"

    def __init__(
        self,
        *,
        fail_times: int,
        fail_exc: Exception,
        success_result: StepExecutionResult,
    ) -> None:
        self._fail_times = fail_times
        self._fail_exc = fail_exc
        self._success_result = success_result
        self.call_count = 0

    async def invoke(self, request: LLMRequest) -> StepExecutionResult:
        self.call_count += 1
        if self.call_count <= self._fail_times:
            raise self._fail_exc
        return self._success_result


class AlwaysFailAdapter(BaseLLMAdapter):
    """Adapter that always raises a given exception."""

    provider = "test"
    model = "test-model"

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.call_count = 0

    async def invoke(self, request: LLMRequest) -> StepExecutionResult:
        self.call_count += 1
        raise self._exc


class QueueAdapter(BaseLLMAdapter):
    """Adapter that returns responses from a queue."""

    provider = "test"
    model = "test-model"

    def __init__(self, responses: list[StepExecutionResult]) -> None:
        self._responses = deque(responses)

    async def invoke(self, request: LLMRequest) -> StepExecutionResult:
        if not self._responses:
            raise AssertionError("No queued responses.")
        return self._responses.popleft()


# ---------------------------------------------------------------------------
# _is_context_overflow_message
# ---------------------------------------------------------------------------


def test_context_overflow_detection_patterns() -> None:
    assert _is_context_overflow_message("This model's maximum context length is 4096 tokens")
    assert _is_context_overflow_message("Request exceeds the model's token limit")
    assert _is_context_overflow_message("Please reduce the length of the messages")
    assert _is_context_overflow_message("input is too long for this model")
    assert not _is_context_overflow_message("Connection refused")
    assert not _is_context_overflow_message("Rate limit exceeded")


# ---------------------------------------------------------------------------
# wrap_llm_error — context overflow detection
# ---------------------------------------------------------------------------


def test_wrap_llm_error_detects_context_overflow() -> None:
    import httpx

    # Simulate an HTTP 400 with a context overflow message
    request = httpx.Request("POST", "http://localhost/v1/chat/completions")
    response = httpx.Response(
        400,
        request=request,
        text='{"error": {"message": "This model\'s maximum context length is 4096 tokens"}}',
    )
    exc = httpx.HTTPStatusError("", request=request, response=response)
    wrapped = wrap_llm_error(exc, provider="ollama", base_url="http://localhost/v1")
    assert isinstance(wrapped, ContextWindowOverflowError)


def test_wrap_llm_error_non_overflow_400_stays_generic() -> None:
    import httpx

    request = httpx.Request("POST", "http://localhost/v1/chat/completions")
    response = httpx.Response(400, request=request, text='{"error": {"message": "Invalid model"}}')
    exc = httpx.HTTPStatusError("", request=request, response=response)
    wrapped = wrap_llm_error(exc, provider="ollama", base_url="http://localhost/v1")
    assert isinstance(wrapped, LLMInvocationError)
    assert not isinstance(wrapped, ContextWindowOverflowError)


def test_wrap_llm_error_detects_overflow_from_generic_exception() -> None:
    exc = RuntimeError("prompt is too long for the context window")
    wrapped = wrap_llm_error(exc, provider="vllm", base_url="http://localhost/v1")
    assert isinstance(wrapped, ContextWindowOverflowError)


# ---------------------------------------------------------------------------
# _is_retryable_error
# ---------------------------------------------------------------------------


def test_timeout_is_retryable() -> None:
    assert _is_retryable_error(TimeoutError()) is True


def test_connection_error_is_retryable() -> None:
    assert _is_retryable_error(ConnectionError("refused")) is True


def test_rate_limit_is_retryable() -> None:
    exc = LLMInvocationError("ollama returned 429 from 'http://localhost': rate limit")
    assert _is_retryable_error(exc) is True


def test_server_error_is_retryable() -> None:
    exc = LLMInvocationError("ollama returned 503 from 'http://localhost': temporarily unavailable")
    assert _is_retryable_error(exc) is True


def test_raw_httpx_status_error_retryable_by_status() -> None:
    # call_llm_for_text uses the raw provider clients, whose ainvoke calls
    # response.raise_for_status() — a 429/5xx reaches the retry classifier as
    # httpx.HTTPStatusError, not LLMInvocationError.
    import httpx
    from anima_server.services.agent.llm import is_retryable_llm_error

    req = httpx.Request("POST", "http://provider")
    overloaded = httpx.HTTPStatusError(
        "529", request=req, response=httpx.Response(529, request=req)
    )
    permanent = httpx.HTTPStatusError(
        "400", request=req, response=httpx.Response(400, request=req)
    )
    assert is_retryable_llm_error(overloaded) is True
    assert is_retryable_llm_error(permanent) is False


def test_raw_httpx_transport_error_retryable() -> None:
    import httpx
    from anima_server.services.agent.llm import is_retryable_llm_error

    assert is_retryable_llm_error(httpx.ConnectError("refused")) is True
    assert is_retryable_llm_error(httpx.ReadTimeout("slow")) is True


def test_context_overflow_not_retryable() -> None:
    exc = ContextWindowOverflowError("context length exceeded")
    assert _is_retryable_error(exc) is False


def test_generic_llm_error_not_retryable() -> None:
    exc = LLMInvocationError("ollama returned 401: unauthorized")
    assert _is_retryable_error(exc) is False


def test_value_error_not_retryable() -> None:
    assert _is_retryable_error(ValueError("bad input")) is False


# ---------------------------------------------------------------------------
# Runtime retry: transient errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_succeeds_after_transient_failures() -> None:
    """Adapter fails twice with a timeout, then succeeds on attempt 3."""
    success = StepExecutionResult(
        tool_calls=(ToolCall(id="call-1", name="send_message", arguments={"message": "hello"}),)
    )
    adapter = FailThenSucceedAdapter(
        fail_times=2,
        fail_exc=TimeoutError(),
        success_result=success,
    )
    runtime = AgentRuntime(
        adapter=adapter,
        tools=[send_message],
        tool_rules=[TerminalToolRule(tool_name="send_message")],
        max_steps=2,
    )

    with patch("anima_server.services.agent.runtime.settings") as mock_settings:
        mock_settings.agent_llm_timeout = 5.0
        mock_settings.agent_llm_retry_limit = 3
        mock_settings.agent_llm_retry_backoff_factor = 0.01  # fast for tests
        mock_settings.agent_llm_retry_max_delay = 0.05
        mock_settings.agent_max_steps = 2
        result = await runtime.invoke("hi", user_id=1, history=[])

    assert result.response == "hello"
    assert result.stop_reason == StopReason.TERMINAL_TOOL.value
    assert adapter.call_count == 3


@pytest.mark.asyncio
async def test_retry_exhausted_raises_original_error() -> None:
    """After all retries are used up, the original error propagates."""
    adapter = AlwaysFailAdapter(TimeoutError())
    runtime = AgentRuntime(
        adapter=adapter,
        tools=[send_message],
        max_steps=1,
    )

    with patch("anima_server.services.agent.runtime.settings") as mock_settings:
        mock_settings.agent_llm_timeout = 5.0
        mock_settings.agent_llm_retry_limit = 2
        mock_settings.agent_llm_retry_backoff_factor = 0.01
        mock_settings.agent_llm_retry_max_delay = 0.05
        mock_settings.agent_max_steps = 1

        with pytest.raises(StepFailedError) as exc_info:
            await runtime.invoke("hi", user_id=1, history=[])

    assert isinstance(exc_info.value.cause, asyncio.TimeoutError)
    # 1 initial + 2 retries = 3 total
    assert adapter.call_count == 3


@pytest.mark.asyncio
async def test_non_retryable_error_fails_immediately() -> None:
    """Context overflow and auth errors are not retried."""
    adapter = AlwaysFailAdapter(ContextWindowOverflowError("context length exceeded"))
    runtime = AgentRuntime(
        adapter=adapter,
        tools=[send_message],
        max_steps=1,
    )

    with patch("anima_server.services.agent.runtime.settings") as mock_settings:
        mock_settings.agent_llm_timeout = 5.0
        mock_settings.agent_llm_retry_limit = 3
        mock_settings.agent_llm_retry_backoff_factor = 0.01
        mock_settings.agent_llm_retry_max_delay = 0.05
        mock_settings.agent_max_steps = 1

        with pytest.raises(StepFailedError) as exc_info:
            await runtime.invoke("hi", user_id=1, history=[])

    assert isinstance(exc_info.value.cause, ContextWindowOverflowError)
    assert adapter.call_count == 1  # no retries


@pytest.mark.asyncio
async def test_retry_with_rate_limit_error() -> None:
    """Rate-limit 429 is retried and succeeds."""
    success = StepExecutionResult(assistant_text="recovered")
    adapter = FailThenSucceedAdapter(
        fail_times=1,
        fail_exc=LLMInvocationError("openrouter returned 429: rate limit exceeded"),
        success_result=success,
    )
    runtime = AgentRuntime(
        adapter=adapter,
        tools=[send_message],
        tool_rules=[TerminalToolRule(tool_name="send_message")],
        max_steps=2,
    )

    with patch("anima_server.services.agent.runtime.settings") as mock_settings:
        mock_settings.agent_llm_timeout = 5.0
        mock_settings.agent_llm_retry_limit = 2
        mock_settings.agent_llm_retry_backoff_factor = 0.01
        mock_settings.agent_llm_retry_max_delay = 0.05
        mock_settings.agent_max_steps = 2
        result = await runtime.invoke("hi", user_id=1, history=[])

    assert result.response == "recovered"
    assert adapter.call_count == 2


@pytest.mark.asyncio
async def test_zero_retry_limit_means_no_retries() -> None:
    """With retry_limit=0, any error fails immediately."""
    adapter = AlwaysFailAdapter(TimeoutError())
    runtime = AgentRuntime(
        adapter=adapter,
        tools=[send_message],
        max_steps=1,
    )

    with patch("anima_server.services.agent.runtime.settings") as mock_settings:
        mock_settings.agent_llm_timeout = 5.0
        mock_settings.agent_llm_retry_limit = 0
        mock_settings.agent_llm_retry_backoff_factor = 0.01
        mock_settings.agent_llm_retry_max_delay = 0.05
        mock_settings.agent_max_steps = 1

        with pytest.raises(StepFailedError):
            await runtime.invoke("hi", user_id=1, history=[])

    assert adapter.call_count == 1


@pytest.mark.asyncio
async def test_pre_set_cancel_event_stops_before_retry() -> None:
    """If cancel is already set, the runtime stops at the step boundary
    without even attempting the LLM call."""

    adapter = AlwaysFailAdapter(TimeoutError())
    cancel_event = asyncio.Event()
    cancel_event.set()  # pre-cancelled

    runtime = AgentRuntime(
        adapter=adapter,
        tools=[send_message],
        max_steps=2,
    )

    with patch("anima_server.services.agent.runtime.settings") as mock_settings:
        mock_settings.agent_llm_timeout = 5.0
        mock_settings.agent_llm_retry_limit = 3
        mock_settings.agent_llm_retry_backoff_factor = 0.01
        mock_settings.agent_llm_retry_max_delay = 0.05
        mock_settings.agent_max_steps = 2

        result = await runtime.invoke(
            "hi",
            user_id=1,
            history=[],
            cancel_event=cancel_event,
        )

    assert result.stop_reason == StopReason.CANCELLED.value
    assert adapter.call_count == 0  # never even called


# ---------------------------------------------------------------------------
# Context overflow detection integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_overflow_raises_step_failed_with_correct_cause() -> None:
    """Verify StepFailedError wraps ContextWindowOverflowError properly."""
    adapter = AlwaysFailAdapter(ContextWindowOverflowError("maximum context length"))
    runtime = AgentRuntime(adapter=adapter, tools=[], max_steps=1)

    with patch("anima_server.services.agent.runtime.settings") as mock_settings:
        mock_settings.agent_llm_timeout = 5.0
        mock_settings.agent_llm_retry_limit = 0
        mock_settings.agent_llm_retry_backoff_factor = 0.01
        mock_settings.agent_llm_retry_max_delay = 0.05
        mock_settings.agent_max_steps = 1

        with pytest.raises(StepFailedError) as exc_info:
            await runtime.invoke("hi", user_id=1, history=[])

    assert isinstance(exc_info.value.cause, ContextWindowOverflowError)
