from __future__ import annotations

import pytest
from anima_server.config import settings
from anima_server.services.agent import llm as llm_module
from anima_server.services.agent import proactive
from anima_server.services.agent.proactive import (
    GreetingContext,
    _normalize_greeting_pills,
    generate_thought_pills,
)


class _StubPromptLoader:
    """Minimal stand-in: generate_thought_pills only calls greeting_pills()."""

    def greeting_pills(self, *, greeting_message: str, context: str) -> str:
        return f"GREETING: {greeting_message}\nCONTEXT: {context}"


class _FakeLLM:
    def __init__(self, content: str) -> None:
        self._content = content

    async def ainvoke(self, _messages: object) -> object:
        return type("Resp", (), {"content": self._content})()


def test_normalize_accepts_bare_array_and_uppercases_labels() -> None:
    pills = _normalize_greeting_pills(
        [
            {"kind": "topic", "label": "Olympus Trip"},
            {"kind": "emotion", "label": "wistful"},
        ]
    )
    assert pills == [
        {"kind": "topic", "label": "OLYMPUS TRIP"},
        {"kind": "emotion", "label": "WISTFUL"},
    ]


def test_normalize_unwraps_object_with_tags_key() -> None:
    pills = _normalize_greeting_pills({"tags": [{"kind": "memory", "label": "the cabin"}]})
    assert pills == [{"kind": "memory", "label": "THE CABIN"}]


def test_normalize_drops_unknown_kinds_and_empty_labels() -> None:
    pills = _normalize_greeting_pills(
        [
            {"kind": "sentiment", "label": "BOGUS"},  # not in vocab
            {"kind": "topic", "label": ""},  # empty
            {"kind": "task", "label": "DEADLINE FRIDAY"},  # valid
            "not-a-dict",  # junk
        ]
    )
    assert pills == [{"kind": "task", "label": "DEADLINE FRIDAY"}]


def test_normalize_dedupes_and_caps_count() -> None:
    raw = [{"kind": "topic", "label": "ONE"}, {"kind": "topic", "label": "one"}]
    raw += [{"kind": "topic", "label": f"T{i}"} for i in range(10)]
    pills = _normalize_greeting_pills(raw)
    labels = [p["label"] for p in pills]
    assert labels[0] == "ONE"
    assert labels.count("ONE") == 1  # case-insensitive dedupe
    assert len(pills) <= proactive._MAX_GREETING_PILLS


def test_normalize_truncates_long_labels() -> None:
    pills = _normalize_greeting_pills([{"kind": "topic", "label": "A" * 50}])
    assert len(pills[0]["label"]) == proactive._MAX_GREETING_PILL_LABEL_CHARS


def test_normalize_handles_garbage_input() -> None:
    assert _normalize_greeting_pills(None) == []
    assert _normalize_greeting_pills("nope") == []
    assert _normalize_greeting_pills(42) == []


@pytest.mark.asyncio
async def test_generate_thought_pills_returns_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "agent_provider", "openai")
    monkeypatch.setattr(
        llm_module,
        "create_llm",
        lambda: _FakeLLM('[{"kind":"topic","label":"Olympus Trip"}]'),
    )

    pills = await generate_thought_pills(
        _StubPromptLoader(),
        greeting_message="That trip still feels like dreamland.",
        ctx=GreetingContext(current_focus="planning the next trip"),
    )
    assert pills == [{"kind": "topic", "label": "OLYMPUS TRIP"}]


@pytest.mark.asyncio
async def test_generate_thought_pills_scaffold_skips_llm() -> None:
    # scaffold provider must not call any LLM and yields no pills.
    pills = await generate_thought_pills(
        _StubPromptLoader(),
        greeting_message="hello",
        ctx=GreetingContext(),
    )
    # Default test provider may already be scaffold; assert explicitly.
    if settings.agent_provider == "scaffold":
        assert pills == []


@pytest.mark.asyncio
async def test_generate_thought_pills_swallows_llm_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom() -> object:
        raise RuntimeError("provider down")

    monkeypatch.setattr(settings, "agent_provider", "openai")
    monkeypatch.setattr(llm_module, "create_llm", boom)

    pills = await generate_thought_pills(
        _StubPromptLoader(),
        greeting_message="hello",
        ctx=GreetingContext(),
    )
    assert pills == []
