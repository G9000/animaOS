from __future__ import annotations

from anima_server.services.agent.json_utils import parse_json_array, parse_json_object


def test_parse_json_array_skips_non_json_bracket_noise() -> None:
    text = """Reasoning: [group by topic]\n\n[[1, 3, 5], [2, 4]]\n\nTrailing note [ignored]"""

    assert parse_json_array(text) == [[1, 3, 5], [2, 4]]


def test_parse_json_array_skips_reasoning_block_with_brackets() -> None:
    text = """<think>I should probably emit [[1], [2]] once I am done.</think>\n[[1, 3], [2]]"""

    assert parse_json_array(text) == [[1, 3], [2]]


def test_parse_json_object_skips_prefixed_array_noise() -> None:
    text = """Metadata: [ignored]\n{\"topic\": \"memory\", \"confidence\": 0.8}\nTrailing note"""

    assert parse_json_object(text) == {"topic": "memory", "confidence": 0.8}


def test_parse_json_array_returns_empty_when_no_array_is_recoverable() -> None:
    assert parse_json_array("no structured payload here") == []
