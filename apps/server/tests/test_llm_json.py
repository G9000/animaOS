"""Tests for the shared call_llm_for_json / call_llm_for_text helpers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from anima_server.services.agent.llm_json import call_llm_for_json, call_llm_for_text


class StubClient:
    """Minimal chat client stub recording ainvoke calls."""

    def __init__(self, content: Any) -> None:
        self._content = content
        self.calls: list[list[Any]] = []

    async def ainvoke(self, messages: list[Any]) -> SimpleNamespace:
        self.calls.append(list(messages))
        return SimpleNamespace(content=self._content)


@pytest.mark.asyncio
async def test_call_llm_for_json_parses_object() -> None:
    client = StubClient('{"verdict": "ok", "confidence": 0.9}')

    parsed = await call_llm_for_json("system", "prompt", client=client)

    assert parsed == {"verdict": "ok", "confidence": 0.9}


@pytest.mark.asyncio
async def test_call_llm_for_json_parses_array() -> None:
    client = StubClient('Here you go:\n```json\n[[1, 2], [3]]\n```')

    parsed = await call_llm_for_json(
        "system", "prompt", expect="array", client=client
    )

    assert parsed == [[1, 2], [3]]


@pytest.mark.asyncio
async def test_call_llm_for_json_returns_none_on_malformed_output() -> None:
    client = StubClient("I could not produce JSON, sorry.")

    assert await call_llm_for_json("system", "prompt", client=client) is None
    assert (
        await call_llm_for_json("system", "prompt", expect="array", client=client)
        is None
    )


@pytest.mark.asyncio
async def test_call_llm_for_json_returns_none_when_object_expected_but_array_given() -> None:
    client = StubClient("[1, 2, 3]")

    assert await call_llm_for_json("system", "prompt", client=client) is None


@pytest.mark.asyncio
async def test_injected_client_is_used_instead_of_create_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_create_llm() -> Any:
        raise AssertionError("create_llm must not be called when a client is injected")

    monkeypatch.setattr(
        "anima_server.services.agent.llm.create_llm", fail_create_llm
    )
    client = StubClient('{"ok": true}')

    parsed = await call_llm_for_json("system", "prompt", client=client)

    assert parsed == {"ok": True}
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_create_llm_resolved_lazily_when_no_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = StubClient('{"source": "patched"}')
    monkeypatch.setattr(
        "anima_server.services.agent.llm.create_llm", lambda: client
    )

    parsed = await call_llm_for_json("the system", "the prompt")

    assert parsed == {"source": "patched"}
    # Messages follow the SystemMessage/HumanMessage pair convention.
    [messages] = client.calls
    assert [getattr(m, "type", "") for m in messages] == ["system", "human"]
    assert messages[0].content == "the system"
    assert messages[1].content == "the prompt"


@pytest.mark.asyncio
async def test_call_llm_for_text_extracts_content_defensively() -> None:
    class NoContentClient:
        async def ainvoke(self, messages: list[Any]) -> object:
            return object()  # no .content attribute

    assert await call_llm_for_text("s", "p", client=NoContentClient()) == ""

    non_string = StubClient({"unexpected": "shape"})
    assert (
        await call_llm_for_text("s", "p", client=non_string)
        == str({"unexpected": "shape"})
    )
