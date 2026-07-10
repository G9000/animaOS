"""ARH-005: structured LLM errors, shared retry, and capability gating.

Retryability used to be decided by substring-matching stringified
exceptions (529 missing, a benign "429" in an ID retrying a permanent
400), background LLM calls had no retry at all, vision gating missed
current model generations, and temperature was sent to Anthropic models
that reject it.
"""

from __future__ import annotations

import httpx
import pytest
from anima_server.services.agent.anthropic_client import (
    AnthropicChatClient,
    _model_accepts_temperature,
    _normalize_response,
    _serialize_anthropic_tool,
)
from anima_server.services.agent.llm import (
    ContextWindowOverflowError,
    LLMInvocationError,
    invoke_with_retry,
    is_retryable_llm_error,
    retry_backoff_delay,
    wrap_llm_error,
)
from anima_server.services.agent.model_capabilities import supports_image_input


def _http_status_error(
    status: int,
    *,
    body: str = "",
    headers: dict[str, str] | None = None,
) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://provider.test/v1/messages")
    response = httpx.Response(
        status, content=body.encode(), headers=headers or {}, request=request
    )
    return httpx.HTTPStatusError("boom", request=request, response=response)


# --------------------------------------------------------------------------- #
# wrap_llm_error: structured status_code / retry_after
# --------------------------------------------------------------------------- #


class TestWrapLLMError:
    def test_attaches_status_code(self) -> None:
        error = wrap_llm_error(
            _http_status_error(529, body="overloaded"),
            provider="anthropic",
            base_url="http://provider.test",
        )
        assert error.status_code == 529
        assert error.retry_after is None

    def test_parses_numeric_retry_after(self) -> None:
        error = wrap_llm_error(
            _http_status_error(429, headers={"retry-after": "7"}),
            provider="anthropic",
            base_url="http://provider.test",
        )
        assert error.status_code == 429
        assert error.retry_after == 7.0

    def test_parses_http_date_retry_after(self) -> None:
        error = wrap_llm_error(
            _http_status_error(
                429, headers={"retry-after": "Wed, 21 Oct 2015 07:28:00 GMT"}
            ),
            provider="anthropic",
            base_url="http://provider.test",
        )
        # A date in the past clamps to zero rather than going negative.
        assert error.retry_after == 0.0

    def test_context_overflow_keeps_status(self) -> None:
        error = wrap_llm_error(
            _http_status_error(400, body="prompt is too long: 250000 tokens"),
            provider="anthropic",
            base_url="http://provider.test",
        )
        assert isinstance(error, ContextWindowOverflowError)
        assert error.status_code == 400


# --------------------------------------------------------------------------- #
# is_retryable_llm_error: integer classification
# --------------------------------------------------------------------------- #


class TestRetryClassification:
    def test_529_overloaded_is_retryable(self) -> None:
        error = wrap_llm_error(
            _http_status_error(529), provider="anthropic", base_url="http://x"
        )
        assert is_retryable_llm_error(error) is True

    def test_408_and_409_are_retryable(self) -> None:
        for status in (408, 409):
            error = wrap_llm_error(
                _http_status_error(status), provider="anthropic", base_url="http://x"
            )
            assert is_retryable_llm_error(error) is True

    def test_permanent_400_with_429_in_body_is_not_retryable(self) -> None:
        """The old substring matcher retried any error whose text contained
        '429' — e.g. a count or an ID inside a permanent 400 body."""
        error = wrap_llm_error(
            _http_status_error(400, body="invalid request: item 429 not found"),
            provider="anthropic",
            base_url="http://x",
        )
        assert is_retryable_llm_error(error) is False

    def test_context_overflow_never_retries(self) -> None:
        error = wrap_llm_error(
            _http_status_error(429, body="prompt is too long"),
            provider="anthropic",
            base_url="http://x",
        )
        assert isinstance(error, ContextWindowOverflowError)
        assert is_retryable_llm_error(error) is False

    def test_statusless_message_fallback(self) -> None:
        assert is_retryable_llm_error(LLMInvocationError("provider overloaded")) is True
        assert is_retryable_llm_error(LLMInvocationError("request timed out")) is True
        # Bare numeric substrings no longer count.
        assert (
            is_retryable_llm_error(LLMInvocationError("counted 429 items in batch"))
            is False
        )

    def test_timeout_and_connection_errors_retry(self) -> None:
        assert is_retryable_llm_error(TimeoutError()) is True
        assert is_retryable_llm_error(ConnectionError("reset")) is True


# --------------------------------------------------------------------------- #
# retry_backoff_delay: retry-after floor
# --------------------------------------------------------------------------- #


class TestBackoffDelay:
    def test_retry_after_is_the_floor(self) -> None:
        error = wrap_llm_error(
            _http_status_error(429, headers={"retry-after": "5"}),
            provider="anthropic",
            base_url="http://x",
        )
        delay = retry_backoff_delay(
            error, attempt=1, backoff_factor=0.5, max_delay=10.0
        )
        assert delay == 5.0

    def test_retry_after_capped_at_max_delay(self) -> None:
        error = wrap_llm_error(
            _http_status_error(429, headers={"retry-after": "120"}),
            provider="anthropic",
            base_url="http://x",
        )
        delay = retry_backoff_delay(
            error, attempt=1, backoff_factor=0.5, max_delay=10.0
        )
        assert delay == 10.0

    def test_exponential_without_retry_after(self) -> None:
        error = LLMInvocationError("overloaded")
        assert (
            retry_backoff_delay(error, attempt=3, backoff_factor=0.5, max_delay=10.0)
            == 2.0
        )


# --------------------------------------------------------------------------- #
# invoke_with_retry: background calls survive transient failures
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_invoke_with_retry_recovers_from_transient_429() -> None:
    attempts = 0

    async def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise wrap_llm_error(
                _http_status_error(429), provider="anthropic", base_url="http://x"
            )
        return "ok"

    result = await invoke_with_retry(
        flaky, retry_limit=2, backoff_factor=0.01, max_delay=0.02
    )
    assert result == "ok"
    assert attempts == 2


@pytest.mark.asyncio
async def test_invoke_with_retry_raises_permanent_errors_immediately() -> None:
    attempts = 0

    async def broken() -> str:
        nonlocal attempts
        attempts += 1
        raise wrap_llm_error(
            _http_status_error(401, body="bad key"),
            provider="anthropic",
            base_url="http://x",
        )

    with pytest.raises(LLMInvocationError):
        await invoke_with_retry(
            broken, retry_limit=3, backoff_factor=0.01, max_delay=0.02
        )
    assert attempts == 1


@pytest.mark.asyncio
async def test_call_llm_for_json_survives_one_transient_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every extraction path funnels through call_llm_for_json; a single
    transient 429 used to permanently lose the memories."""
    from anima_server.config import settings
    from anima_server.services.agent.llm_json import call_llm_for_json

    monkeypatch.setattr(settings, "agent_llm_retry_backoff_factor", 0.01)
    monkeypatch.setattr(settings, "agent_llm_retry_max_delay", 0.02)

    class _FlakyClient:
        def __init__(self) -> None:
            self.calls = 0

        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                raise wrap_llm_error(
                    _http_status_error(429),
                    provider="anthropic",
                    base_url="http://x",
                )

            class _Response:
                content = '{"memories": ["remembered"]}'

            return _Response()

    client = _FlakyClient()
    parsed = await call_llm_for_json("system", "prompt", client=client)
    assert parsed == {"memories": ["remembered"]}
    assert client.calls == 2


# --------------------------------------------------------------------------- #
# Vision capability gating
# --------------------------------------------------------------------------- #


class TestVisionGating:
    def test_current_claude_generations_supported(self) -> None:
        for model in ("claude-sonnet-5", "claude-fable-5", "claude-opus-4-8",
                      "claude-haiku-4-5-20251001"):
            assert supports_image_input("anthropic", model) is True

    def test_non_vision_models_still_rejected(self) -> None:
        assert supports_image_input("openrouter", "mistral-7b-instruct") is False
        assert supports_image_input("scaffold", "claude-sonnet-5") is False


# --------------------------------------------------------------------------- #
# Anthropic client: temperature gating, stop_reason, strict passthrough
# --------------------------------------------------------------------------- #


class TestAnthropicClient:
    def test_temperature_dropped_for_rejecting_models(self) -> None:
        assert _model_accepts_temperature("claude-sonnet-5") is False
        assert _model_accepts_temperature("claude-opus-4-8") is False
        assert _model_accepts_temperature("claude-fable-5") is False
        assert _model_accepts_temperature("claude-haiku-4-5-20251001") is True

        from anima_server.services.agent.messages import HumanMessage

        rejecting = AnthropicChatClient(
            model="claude-sonnet-5",
            base_url="http://x",
            temperature=0.3,
        )
        payload = rejecting._build_payload([HumanMessage(content="hi")], stream=False)
        assert "temperature" not in payload

        accepting = AnthropicChatClient(
            model="claude-haiku-4-5-20251001",
            base_url="http://x",
            temperature=0.3,
        )
        payload = accepting._build_payload([HumanMessage(content="hi")], stream=False)
        assert payload["temperature"] == 0.3

    def test_stop_reason_surfaced_and_truncation_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        from anima_server.services.agent.anthropic_client import (
            _warn_on_abnormal_stop,
        )

        normalized = _normalize_response(
            {
                "content": [{"type": "text", "text": "partial answer"}],
                "stop_reason": "max_tokens",
            }
        )
        assert normalized.stop_reason == "max_tokens"

        with caplog.at_level(logging.WARNING, logger="anima.runtime.degraded"):
            _warn_on_abnormal_stop("max_tokens", model="claude-opus-4-8")
            _warn_on_abnormal_stop("refusal", model="claude-fable-5")
            _warn_on_abnormal_stop("end_turn", model="claude-opus-4-8")

        degraded = [r for r in caplog.records if r.name == "anima.runtime.degraded"]
        assert len(degraded) == 2
        assert "truncated" in degraded[0].getMessage()
        assert "declined" in degraded[1].getMessage()

    def test_strict_flag_passes_through_tool_serialization(self) -> None:
        tool = {
            "type": "function",
            "function": {
                "name": "book_flight",
                "description": "Book a flight",
                "parameters": {
                    "type": "object",
                    "properties": {"destination": {"type": "string"}},
                    "required": ["destination"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        }
        payload = _serialize_anthropic_tool(tool)
        assert payload["strict"] is True

        tool["function"].pop("strict")
        payload = _serialize_anthropic_tool(tool)
        assert "strict" not in payload
