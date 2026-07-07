"""ARH-008: bounded tool-output replay, slim step snapshots, boundary-aware
block truncation, and a conservative token estimate."""

from __future__ import annotations

import json

from anima_server.services.agent.compaction import estimate_message_tokens
from anima_server.services.agent.executor import _package_tool_response
from anima_server.services.agent.memory_blocks import _truncate_lines
from anima_server.services.agent.messages import (
    TOOL_HISTORY_CHAR_LIMIT,
    make_tool_message,
)
from anima_server.services.agent.persistence import (
    _STEP_SNAPSHOT_PREVIEW_CHARS,
    _slim_message_snapshot,
)
from anima_server.services.agent.runtime_types import MessageSnapshot


# --------------------------------------------------------------------------- #
# Tool-output history clamp
# --------------------------------------------------------------------------- #


class TestToolHistoryClamp:
    def test_large_envelope_is_clamped_but_stays_valid_json(self) -> None:
        """A 50k tool result used to enter history verbatim and get re-billed
        on every subsequent LLM call until compaction."""
        big_output = "x" * 40_000
        envelope = _package_tool_response(big_output)
        assert len(envelope) > TOOL_HISTORY_CHAR_LIMIT

        message = make_tool_message(envelope, tool_call_id="tc-1", name="search")
        assert len(message.content) <= TOOL_HISTORY_CHAR_LIMIT + 200
        parsed = json.loads(message.content)  # still valid JSON
        assert parsed["status"] == "OK"
        assert "clamped for conversation history" in parsed["message"]

    def test_small_outputs_pass_through_unchanged(self) -> None:
        envelope = _package_tool_response("normal result")
        message = make_tool_message(envelope, tool_call_id="tc-1", name="search")
        assert message.content == envelope

    def test_non_json_content_is_clamped_with_marker(self) -> None:
        raw = "y" * 20_000
        message = make_tool_message(raw, tool_call_id="tc-1")
        assert len(message.content) <= TOOL_HISTORY_CHAR_LIMIT + 200
        assert "clamped for conversation history" in message.content


# --------------------------------------------------------------------------- #
# Slim step snapshots
# --------------------------------------------------------------------------- #


class TestStepSnapshotSlimming:
    def test_long_content_becomes_preview_with_length(self) -> None:
        snapshot = MessageSnapshot(
            role="tool",
            content="z" * 5_000,
            tool_name="search",
        )
        payload = _slim_message_snapshot(snapshot)
        assert len(payload["content"]) == _STEP_SNAPSHOT_PREVIEW_CHARS + 3
        assert payload["content_chars"] == 5_000
        assert payload["role"] == "tool"
        assert payload["tool_name"] == "search"

    def test_short_content_is_untouched(self) -> None:
        snapshot = MessageSnapshot(role="user", content="hello")
        payload = _slim_message_snapshot(snapshot)
        assert payload["content"] == "hello"
        assert "content_chars" not in payload


# --------------------------------------------------------------------------- #
# Boundary-aware block truncation
# --------------------------------------------------------------------------- #


class TestBlockTruncation:
    def test_truncates_at_line_boundary_not_mid_fact(self) -> None:
        lines = [f"- user fact number {i} about something" for i in range(100)]
        value = "\n".join(lines)
        truncated = _truncate_lines(value, 500)
        assert len(truncated) <= 500
        # No half-line at the end: every remaining line is complete.
        assert truncated == "\n".join(
            line for line in truncated.split("\n") if line in lines
        )


# --------------------------------------------------------------------------- #
# Conservative token estimate
# --------------------------------------------------------------------------- #


class TestConservativeEstimate:
    def test_estimate_is_chars_over_three(self) -> None:
        assert estimate_message_tokens(content_text="x" * 300) == 100

    def test_cjk_heavy_text_is_not_underestimated(self) -> None:
        """CJK runs ~1 char/token; chars/4 estimated a quarter of reality.
        chars/3 is still under, but the whole-prompt reserve plus the
        conservative direction keeps budget checks failing toward
        compaction instead of context overflow."""
        cjk = "私は毎朝緑茶を飲みます" * 30  # 330 chars
        estimate = estimate_message_tokens(content_text=cjk)
        assert estimate >= len(cjk) / 3
