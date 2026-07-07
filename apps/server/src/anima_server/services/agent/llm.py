from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from functools import lru_cache
from typing import Any, Final, Protocol, TypeVar

import httpx

from anima_server.config import settings
from anima_server.services.agent.anthropic_client import AnthropicChatClient
from anima_server.services.agent.openai_compatible_client import (
    OpenAICompatibleChatClient,
)

SUPPORTED_PROVIDERS: Final[tuple[str, ...]] = (
    "ollama",
    "openrouter",
    "moonshot",
    "vllm",
    "openai",
    "anthropic",
    "doubleword",
)
DEFAULT_BASE_URLS: Final[dict[str, str]] = {
    "ollama": "http://127.0.0.1:11434/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "moonshot": "https://api.moonshot.cn/v1",
    "vllm": "http://127.0.0.1:8000/v1",
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "doubleword": "https://api.doubleword.ai/v1",
}
PROVIDER_API_KEY_ENV: Final[dict[str, str]] = {
    "doubleword": "DOUBLEWORD_API_KEY",
}
REQUIRED_API_KEY_PROVIDERS: Final[tuple[str, ...]] = (
    "openrouter",
    "moonshot",
    "openai",
    "anthropic",
    "doubleword",
)


class LLMConfigError(RuntimeError):
    """Raised when the LLM provider is misconfigured."""


class LLMInvocationError(RuntimeError):
    """Raised when a configured provider cannot be reached or returns an error.

    ``status_code`` and ``retry_after`` are populated by ``wrap_llm_error``
    when the underlying failure carried an HTTP response, so retryability
    can be decided on the integer instead of substring-matching the
    stringified exception.
    """

    status_code: int | None = None
    retry_after: float | None = None


class ContextWindowOverflowError(LLMInvocationError):
    """Raised when the LLM reports that the input exceeds the context window."""


class ChatClient(Protocol):
    async def ainvoke(self, input: Sequence[Any]) -> Any:
        """Invoke the chat model with a normalized message list."""

    async def astream(self, input: Sequence[Any]) -> AsyncGenerator[Any, None]:
        """Stream chat model deltas for a normalized message list."""

    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Return a tool-bound chat client."""


@lru_cache(maxsize=1)
def create_llm() -> ChatClient:
    """Create a concrete chat client for the configured provider."""
    provider = settings.agent_provider

    return create_provider_chat_client(
        provider=provider,
        model=settings.agent_model,
        timeout=settings.agent_llm_timeout,
        max_tokens=settings.agent_max_tokens,
        temperature=settings.agent_temperature,
    )


def invalidate_llm_cache() -> None:
    create_llm.cache_clear()


def create_provider_chat_client(
    *,
    provider: str,
    model: str,
    timeout: float,
    max_tokens: int | None = None,
    temperature: float | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ChatClient:
    """Create an uncached chat client for a concrete provider/model pair."""
    validate_provider_configuration(provider)
    base_url = resolve_base_url(provider)
    headers = build_provider_headers(provider)

    if provider == "anthropic":
        return AnthropicChatClient(
            model=model,
            base_url=base_url,
            headers=headers,
            timeout=timeout,
            max_tokens=max_tokens,
            temperature=temperature,
            transport=transport,
        )

    return OpenAICompatibleChatClient(
        provider=provider,
        model=model,
        base_url=base_url,
        headers=headers,
        timeout=timeout,
        max_tokens=max_tokens,
        temperature=temperature,
        transport=transport,
    )


def resolve_base_url(provider: str) -> str:
    validate_provider(provider)
    configured_base_url = settings.agent_base_url.strip()
    if configured_base_url:
        if provider == "ollama" and not configured_base_url.rstrip("/").endswith("/v1"):
            return configured_base_url.rstrip("/") + "/v1"
        return configured_base_url
    return DEFAULT_BASE_URLS[provider]


def build_provider_headers(provider: str) -> dict[str, str]:
    validate_provider(provider)
    headers: dict[str, str] = {}

    api_key = resolve_provider_api_key(provider)
    if provider == "openrouter":
        api_key = require_provider_api_key(provider)
        headers["Authorization"] = f"Bearer {api_key}"
        headers["HTTP-Referer"] = "https://anima.local"
        headers["X-Title"] = "ANIMA"
        return headers

    if provider == "moonshot":
        api_key = require_provider_api_key(provider)
        # Log key prefix for debugging (never log full key)
        key_preview = api_key[:10] + "..." if len(api_key) > 10 else "[too short]"
        import logging

        logging.getLogger(__name__).info(
            f"Moonshot auth header using key starting with: {key_preview}"
        )
        headers["Authorization"] = f"Bearer {api_key}"
        headers["Content-Type"] = "application/json"
        return headers

    if provider == "openai":
        api_key = require_provider_api_key(provider)
        headers["Authorization"] = f"Bearer {api_key}"
        return headers

    if provider == "doubleword":
        api_key = require_provider_api_key(provider)
        headers["Authorization"] = f"Bearer {api_key}"
        return headers

    if provider == "anthropic":
        api_key = require_provider_api_key(provider)
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
        return headers

    if provider == "vllm" and api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    return headers


def validate_provider(provider: str) -> None:
    if provider not in SUPPORTED_PROVIDERS:
        raise LLMConfigError(
            f"Unsupported agent_provider: {provider!r}. "
            f"Expected one of: {', '.join(SUPPORTED_PROVIDERS)}"
        )


def validate_provider_configuration(provider: str) -> None:
    validate_provider(provider)
    require_provider_api_key(provider)


def resolve_provider_api_key(provider: str) -> str:
    validate_provider(provider)
    from anima_server.config import get_provider_api_key, has_provider_api_keys

    env_name = PROVIDER_API_KEY_ENV.get(provider)
    if env_name is not None:
        env_key = os.getenv(env_name, "").strip()
        if env_key:
            return env_key

    per_provider = get_provider_api_key(provider).strip()
    if per_provider:
        return per_provider

    if not has_provider_api_keys():
        return settings.agent_api_key.strip()
    return ""


def require_provider_api_key(provider: str) -> str:
    api_key = resolve_provider_api_key(provider)
    if provider in REQUIRED_API_KEY_PROVIDERS and not api_key:
        env_name = PROVIDER_API_KEY_ENV.get(provider)
        key_hint = "ANIMA_AGENT_API_KEY"
        if env_name is not None:
            key_hint = f"{key_hint} or {env_name}"
        raise LLMConfigError(
            f"{key_hint} is required when agent_provider='{provider}'"
        )
    return api_key


_CONTEXT_OVERFLOW_PATTERNS = (
    "context length",
    "context_length",
    "maximum context",
    "token limit",
    "context window",
    "too many tokens",
    "input is too long",
    "prompt is too long",
    "exceeds the model",
    "reduce the length",
    "maximum number of tokens",
)


def _is_context_overflow_message(text: str) -> bool:
    lowered = text.lower()
    return any(pattern in lowered for pattern in _CONTEXT_OVERFLOW_PATTERNS)


def wrap_llm_error(exc: Exception, *, provider: str, base_url: str) -> LLMInvocationError:
    if isinstance(exc, httpx.HTTPStatusError):
        detail = exc.response.text.strip()
        msg = (
            f"{provider} returned {exc.response.status_code} from {base_url!r}: {detail}"
            if detail
            else f"{provider} returned {exc.response.status_code} from {base_url!r}."
        )
        if detail and _is_context_overflow_message(detail):
            error: LLMInvocationError = ContextWindowOverflowError(msg)
        else:
            error = LLMInvocationError(msg)
        error.status_code = exc.response.status_code
        error.retry_after = _parse_retry_after(exc.response.headers.get("retry-after"))
        return error

    if isinstance(exc, httpx.HTTPError):
        return LLMInvocationError(f"Failed to reach {provider} at {base_url!r}: {exc}")

    error_str = str(exc)
    if _is_context_overflow_message(error_str):
        return ContextWindowOverflowError(error_str)

    return LLMInvocationError(error_str)


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header (delta-seconds or HTTP-date) into seconds."""
    if not value:
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        seconds = (retry_at - datetime.now(UTC)).total_seconds()
    return max(0.0, seconds)


# Transient statuses worth retrying: request timeout, conflict, rate
# limit, server errors, and Anthropic's 529 overloaded.
RETRYABLE_STATUS_CODES: Final[frozenset[int]] = frozenset(
    {408, 409, 429, 500, 502, 503, 504, 529}
)

# Fallback for errors that carry no HTTP status (local runtimes, transport
# wrappers).  The "returned NNN" forms match wrap_llm_error's own message
# format for older call sites; deliberately NOT bare numeric substrings —
# a permanent 400 whose body happens to contain "429" must not retry.
_RETRYABLE_MESSAGE_PATTERNS: Final[tuple[str, ...]] = (
    "returned 408",
    "returned 409",
    "returned 429",
    "returned 500",
    "returned 502",
    "returned 503",
    "returned 504",
    "returned 529",
    "rate limit",
    "overloaded",
    "temporarily unavailable",
    "try again",
    "timed out",
    "timeout",
)


def is_retryable_llm_error(exc: BaseException) -> bool:
    """Return True if the exception is transient and worth retrying."""
    if isinstance(exc, ContextWindowOverflowError):
        return False
    if isinstance(exc, LLMConfigError):
        return False
    if isinstance(exc, asyncio.TimeoutError):
        return True
    if isinstance(exc, LLMInvocationError):
        status = getattr(exc, "status_code", None)
        if status is not None:
            return status in RETRYABLE_STATUS_CODES
        msg = str(exc).lower()
        return any(pattern in msg for pattern in _RETRYABLE_MESSAGE_PATTERNS)
    return isinstance(exc, (ConnectionError, OSError))


def retry_backoff_delay(
    exc: BaseException,
    *,
    attempt: int,
    backoff_factor: float,
    max_delay: float,
) -> float:
    """Exponential backoff with the provider's retry-after as the floor."""
    delay = min(backoff_factor * (2 ** (attempt - 1)), max_delay)
    retry_after = getattr(exc, "retry_after", None)
    if retry_after:
        delay = max(delay, min(float(retry_after), max_delay))
    return delay


_T = TypeVar("_T")

_retry_logger = logging.getLogger(__name__)


async def invoke_with_retry(
    operation: Callable[[], Awaitable[_T]],
    *,
    retry_limit: int | None = None,
    backoff_factor: float | None = None,
    max_delay: float | None = None,
    description: str = "LLM call",
) -> _T:
    """Run *operation* with exponential backoff on transient LLM errors.

    Shared by background call sites (extraction, consolidation, compaction)
    that previously bypassed the interactive runtime's retry loop and lost
    work on a single transient 429.  Defaults come from the same settings
    the runtime uses.
    """
    limit = settings.agent_llm_retry_limit if retry_limit is None else retry_limit
    factor = (
        settings.agent_llm_retry_backoff_factor
        if backoff_factor is None
        else backoff_factor
    )
    ceiling = settings.agent_llm_retry_max_delay if max_delay is None else max_delay

    last_exc: BaseException | None = None
    for attempt in range(1, limit + 2):  # attempt 1 .. limit+1
        try:
            return await operation()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_exc = exc
            if attempt > limit or not is_retryable_llm_error(exc):
                raise
            delay = retry_backoff_delay(
                exc, attempt=attempt, backoff_factor=factor, max_delay=ceiling
            )
            _retry_logger.warning(
                "%s failed (attempt %d/%d): %s. Retrying in %.1fs",
                description,
                attempt,
                limit + 1,
                exc,
                delay,
            )
            await asyncio.sleep(delay)

    assert last_exc is not None  # unreachable; satisfies the type checker
    raise last_exc
