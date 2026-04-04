"""Centralised JSON extraction from LLM response text.

Uses ``json_repair`` to handle the messy outputs local models tend to
produce (markdown fences, trailing commas, truncated responses, etc.).
"""

from __future__ import annotations

import json
from typing import Any

from json_repair import repair_json

from anima_server.services.agent.output_filter import strip_reasoning_traces


def parse_json_object(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from *text*, repairing if needed.

    Returns ``None`` when no object can be recovered.
    """
    parsed = _extract_json_value(text, opening="{", closing="}")
    if not isinstance(parsed, dict):
        return None
    return parsed


def parse_json_array(text: str) -> list[Any]:
    """Extract a JSON array from *text*, repairing if needed.

    Returns an empty list when no array can be recovered.
    """
    parsed = _extract_json_value(text, opening="[", closing="]")
    if not isinstance(parsed, list):
        return []
    return parsed


# ── private helpers ──────────────────────────────────────────────────


def _strip_fences(text: str) -> str:
    """Remove markdown code fences wrapping the text."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text


def _extract_json_value(text: str, *, opening: str, closing: str) -> Any | None:
    text = _strip_fences(text).strip()
    if not text:
        return None
    text, _ = strip_reasoning_traces(text)
    text = text.strip()
    if not text:
        return None

    parsed = _extract_strict_json_value(text, opening=opening)
    if parsed is not None:
        return parsed

    return _extract_repaired_json_value(text, opening=opening, closing=closing)


def _extract_strict_json_value(text: str, *, opening: str) -> Any | None:
    decoder = json.JSONDecoder()
    for start in _iter_candidate_starts(text, opening):
        try:
            parsed, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        return parsed
    return None


def _extract_repaired_json_value(text: str, *, opening: str, closing: str) -> Any | None:
    best_match: tuple[int, Any] | None = None
    for candidate in _iter_candidate_segments(text, opening=opening, closing=closing):
        try:
            parsed = repair_json(candidate, return_objects=True)
        except Exception:
            continue
        if best_match is None or len(candidate) > best_match[0]:
            best_match = (len(candidate), parsed)
    return None if best_match is None else best_match[1]


def _iter_candidate_starts(text: str, opening: str):
    for index, char in enumerate(text):
        if char == opening:
            yield index


def _iter_candidate_segments(text: str, *, opening: str, closing: str):
    for start in _iter_candidate_starts(text, opening):
        yield _slice_candidate_segment(text, start=start, opening=opening, closing=closing)


def _slice_candidate_segment(text: str, *, start: int, opening: str, closing: str) -> str:
    depth = 0
    quote_char: str | None = None
    escaping = False

    for index in range(start, len(text)):
        char = text[index]

        if quote_char is not None:
            if escaping:
                escaping = False
                continue
            if char == "\\":
                escaping = True
                continue
            if char == quote_char:
                quote_char = None
            continue

        if char in ('"', "'"):
            quote_char = char
            continue
        if char == opening:
            depth += 1
            continue
        if char == closing:
            depth -= 1
            if depth == 0:
                return text[start: index + 1]

    return text[start:]
