"""Shared helpers for the LLM call-and-parse boilerplate.

Many background extraction tasks follow the same pattern: build a
``SystemMessage``/``HumanMessage`` pair, ``ainvoke`` the configured chat
client, defensively pull the ``content`` attribute off the response, then
parse it as JSON with the repairing parsers from ``json_utils``.

``call_llm_for_text`` covers the invoke-and-extract half; ``call_llm_for_json``
adds the JSON parsing on top. Malformed LLM output never raises — it yields
``None`` — while invocation errors (network, provider, configuration)
propagate so callers keep their existing error-handling semantics.
"""

from __future__ import annotations

from typing import Any, Literal

from anima_server.services.agent.json_utils import parse_json_array, parse_json_object


async def call_llm_for_text(
    system: str,
    prompt: str,
    *,
    client: Any | None = None,
) -> str:
    """Invoke the chat client with a system+human message pair and return the text.

    Uses the injected ``client`` when given (useful for tests and call sites
    that build a custom client), otherwise the cached ``create_llm()``.

    The response content is extracted defensively: a missing ``content``
    attribute yields ``""`` and non-string content is coerced via ``str()``.
    Invocation errors propagate to the caller.
    """
    from anima_server.services.agent.messages import HumanMessage, SystemMessage

    if client is None:
        # Resolved lazily at call time so monkeypatching
        # ``anima_server.services.agent.llm.create_llm`` keeps working.
        from anima_server.services.agent.llm import create_llm

        client = create_llm()

    from anima_server.services.agent.llm import invoke_with_retry

    # Every background extraction path funnels through here; without the
    # retry a single transient 429/timeout silently lost the work.
    messages = [SystemMessage(content=system), HumanMessage(content=prompt)]
    response = await invoke_with_retry(
        lambda: client.ainvoke(messages),
        description="background LLM call",
    )
    content = getattr(response, "content", "")
    if not isinstance(content, str):
        content = str(content)
    return content


async def call_llm_for_json(
    system: str,
    prompt: str,
    *,
    expect: Literal["object", "array"] = "object",
    client: Any | None = None,
) -> dict[str, Any] | list[Any] | None:
    """Call the LLM and parse its output as a JSON object or array.

    Returns the parsed value, or ``None`` when nothing parseable can be
    recovered from the model output (including an empty array result, which
    ``json_utils.parse_json_array`` cannot distinguish from unparseable
    output). Malformed output never raises; invocation errors propagate.
    """
    content = await call_llm_for_text(system, prompt, client=client)
    if expect == "array":
        parsed = parse_json_array(content)
        return parsed if parsed else None
    return parse_json_object(content)
