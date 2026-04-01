"""Incremental extraction of ``thinking`` from streamed tool-call JSON.

When the LLM streams a tool call whose arguments contain a ``thinking``
key (some models may still produce it even though it is no longer
injected into tool schemas), this module lets the UI surface that
reasoning in real-time rather than waiting for the full response.

The executor's :func:`unpack_inner_thoughts_from_kwargs` remains the
safety net for anything the streaming parser misses.
"""

from __future__ import annotations

import enum


class _Phase(enum.Enum):
    """Parser state machine phases."""

    # Haven't seen the opening '{' yet.
    WAIT_OPEN = "wait_open"
    # Seen '{', scanning for the thinking key pattern.
    SEEK_KEY = "seek_key"
    # Matched the key, waiting for the opening '"' of the value.
    AWAIT_VALUE_QUOTE = "await_value_quote"
    # Inside the thinking string value.
    IN_THINKING_VALUE = "in_thinking_value"
    # Done with thinking value, skipping comma/whitespace before next key.
    SKIP_SEPARATOR = "skip_separator"
    # Everything passes through to main_json.
    PASSTHROUGH = "passthrough"


class ThinkingExtractor:
    """Stateful, incremental extractor for the ``thinking`` key in streamed
    tool-call JSON fragments.

    Parameters
    ----------
    thinking_key:
        The JSON key whose value should be routed to a separate buffer.
        Defaults to ``"thinking"``.

    Usage::

        ext = ThinkingExtractor()
        for fragment in stream:
            main_delta, thinking_delta = ext.process_fragment(fragment)
            if thinking_delta:
                yield_to_ui(thinking_delta)
    """

    def __init__(self, thinking_key: str = "thinking") -> None:
        self._thinking_key = thinking_key
        self._phase = _Phase.WAIT_OPEN
        # Pattern to match: "thinking": (with quotes and colon)
        self._key_pattern = f'"{thinking_key}":'
        # Buffer for characters consumed during SEEK_KEY phase.
        self._scan_buf = ""
        # How many non-whitespace chars from after '{' have matched so far.
        self._match_pos = 0
        # Accumulated outputs.
        self._main_parts: list[str] = []
        self._thinking_parts: list[str] = []
        # Escape tracking for IN_THINKING_VALUE.
        self._escaped = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_fragment(self, fragment: str) -> tuple[str, str]:
        """Process a JSON fragment and return ``(main_json_delta, thinking_delta)``.

        Both elements may be empty strings if the fragment was buffered
        internally (e.g. while scanning for the key pattern).
        """
        if not fragment:
            return ("", "")

        main_chars: list[str] = []
        thinking_chars: list[str] = []

        for ch in fragment:
            m, t = self._feed_char(ch)
            if m:
                main_chars.append(m)
            if t:
                thinking_chars.append(t)

        main_delta = "".join(main_chars)
        thinking_delta = "".join(thinking_chars)

        if main_delta:
            self._main_parts.append(main_delta)
        if thinking_delta:
            self._thinking_parts.append(thinking_delta)

        return (main_delta, thinking_delta)

    @property
    def main_json(self) -> str:
        """All accumulated non-thinking JSON content."""
        return "".join(self._main_parts)

    @property
    def thinking(self) -> str:
        """All accumulated thinking content (the raw string value)."""
        return "".join(self._thinking_parts)

    # ------------------------------------------------------------------
    # Internal state machine
    # ------------------------------------------------------------------

    def _feed_char(self, ch: str) -> tuple[str, str]:
        """Feed a single character. Returns ``(main_char, thinking_char)``."""
        phase = self._phase

        if phase == _Phase.WAIT_OPEN:
            if ch == "{":
                self._phase = _Phase.SEEK_KEY
                self._scan_buf = "{"
                self._match_pos = 0
            return ("", "")

        if phase == _Phase.SEEK_KEY:
            return self._handle_seek_key(ch)

        if phase == _Phase.AWAIT_VALUE_QUOTE:
            return self._handle_await_value_quote(ch)

        if phase == _Phase.IN_THINKING_VALUE:
            return self._handle_in_thinking_value(ch)

        if phase == _Phase.SKIP_SEPARATOR:
            return self._handle_skip_separator(ch)

        # PASSTHROUGH
        return (ch, "")

    def _handle_seek_key(self, ch: str) -> tuple[str, str]:
        """Accumulate characters while looking for ``"thinking":``."""
        self._scan_buf += ch

        # Skip whitespace between '{' and the key.
        if ch in (" ", "\t", "\n", "\r"):
            return ("", "")

        # Check if this non-whitespace char matches the expected position
        # in the key pattern.
        if self._match_pos < len(self._key_pattern) and ch == self._key_pattern[self._match_pos]:
            self._match_pos += 1
            # Full match?
            if self._match_pos == len(self._key_pattern):
                # Matched "thinking": — wait for opening quote.
                self._phase = _Phase.AWAIT_VALUE_QUOTE
                self._scan_buf = ""
            return ("", "")

        # Mismatch — not the thinking key.  Flush scan_buf to main_json.
        self._phase = _Phase.PASSTHROUGH
        flushed = self._scan_buf
        self._scan_buf = ""
        return (flushed, "")

    def _handle_await_value_quote(self, ch: str) -> tuple[str, str]:
        """Wait for the opening ``"`` of the thinking string value."""
        if ch in (" ", "\t", "\n", "\r"):
            return ("", "")
        if ch == '"':
            self._phase = _Phase.IN_THINKING_VALUE
            self._escaped = False
            return ("", "")
        # Unexpected character — not a string value.  This shouldn't happen
        # with well-formed JSON, but fall back to passthrough.
        self._phase = _Phase.PASSTHROUGH
        return ("{" + ch, "")

    def _handle_in_thinking_value(self, ch: str) -> tuple[str, str]:
        """Accumulate thinking value characters until the closing quote."""
        if self._escaped:
            self._escaped = False
            unescaped = _UNESCAPE_MAP.get(ch)
            if unescaped is not None:
                return ("", unescaped)
            # Unknown escape (e.g. \uXXXX) — emit raw for now.
            return ("", "\\" + ch)

        if ch == "\\":
            self._escaped = True
            return ("", "")

        if ch == '"':
            # Closing quote — done with thinking value.
            # Skip comma/whitespace, then emit '{' before the next key.
            self._phase = _Phase.SKIP_SEPARATOR
            return ("", "")

        return ("", ch)

    def _handle_skip_separator(self, ch: str) -> tuple[str, str]:
        """Skip comma and whitespace after the thinking value, then emit
        ``{`` and switch to PASSTHROUGH."""
        if ch in (" ", "\t", "\n", "\r"):
            return ("", "")
        if ch == ",":
            return ("", "")
        # First real character — emit '{' + this char as start of main JSON.
        self._phase = _Phase.PASSTHROUGH
        return ("{" + ch, "")


_UNESCAPE_MAP = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    '"': '"',
    "\\": "\\",
    "/": "/",
}
