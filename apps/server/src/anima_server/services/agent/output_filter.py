from __future__ import annotations

import re

_TAG_NAMES = ("think", "analysis", "reasoning")
_OPEN_RE = re.compile(
    r"<\s*(?P<tag>think|analysis|reasoning)\b[^>]*>",
    re.IGNORECASE,
)
_CLOSE_RE = re.compile(
    r"</\s*(?P<tag>think|analysis|reasoning)\s*>",
    re.IGNORECASE,
)
_BLOCK_RE = re.compile(
    r"<\s*(think|analysis|reasoning)\b[^>]*>.*?</\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_OPEN_MARKERS = tuple(f"<{name}" for name in _TAG_NAMES)
_CLOSE_MARKERS = tuple(f"</{name}>" for name in _TAG_NAMES)


def strip_reasoning_traces(text: str) -> tuple[str, str | None]:
    """Strip reasoning tags and return ``(visible_text, reasoning_text)``."""
    if not text:
        return "", None

    reasoning_parts: list[str] = []

    def _capture(m: re.Match[str]) -> str:
        reasoning_parts.append(m.group(0))
        return ""

    sanitized = text
    previous = None
    while sanitized != previous:
        previous = sanitized
        sanitized = _BLOCK_RE.sub(_capture, sanitized)

    visible = sanitized.strip()
    reasoning = "".join(reasoning_parts).strip() or None
    return visible, reasoning


class ReasoningTraceFilter:
    def __init__(self) -> None:
        self._buffer = ""
        self._inside_reasoning = False
        self._emitted_visible_text = False
        self._reasoning_parts: list[str] = []

    @property
    def captured_reasoning(self) -> str | None:
        """Return accumulated reasoning content, or ``None`` if empty."""
        text = "".join(self._reasoning_parts).strip()
        return text or None

    def feed(self, text: str) -> str:
        if not text:
            return ""
        self._buffer += text
        return self._drain(final=False)

    def flush(self) -> str:
        return self._drain(final=True)

    def _drain(self, *, final: bool) -> str:
        visible_parts: list[str] = []

        while self._buffer:
            if self._inside_reasoning:
                close_match = _CLOSE_RE.search(self._buffer)
                if close_match is None:
                    if final:
                        # Unclosed reasoning block — capture what we have.
                        self._reasoning_parts.append(self._buffer)
                        self._buffer = ""
                    else:
                        self._buffer = _longest_partial_suffix(
                            self._buffer,
                            _CLOSE_MARKERS,
                        )
                    break

                # Capture the reasoning content before the close tag.
                self._reasoning_parts.append(
                    self._buffer[: close_match.start()])
                self._buffer = self._buffer[close_match.end():]
                self._inside_reasoning = False
                continue

            open_match = _OPEN_RE.search(self._buffer)
            if open_match is not None:
                visible_parts.append(self._consume_visible(
                    self._buffer[: open_match.start()]))
                self._buffer = self._buffer[open_match.end():]
                self._inside_reasoning = True
                continue

            if final:
                visible_parts.append(self._consume_visible(self._buffer))
                self._buffer = ""
            else:
                pending = _longest_partial_suffix(self._buffer, _OPEN_MARKERS)
                visible_length = len(self._buffer) - len(pending)
                if visible_length > 0:
                    visible_parts.append(
                        self._consume_visible(self._buffer[:visible_length])
                    )
                    self._buffer = self._buffer[visible_length:]
                break

        return "".join(part for part in visible_parts if part)

    def _consume_visible(self, text: str) -> str:
        if not text:
            return ""

        if not self._emitted_visible_text:
            text = text.lstrip()
            if not text:
                return ""

        self._emitted_visible_text = True
        return text


def _longest_partial_suffix(text: str, markers: tuple[str, ...]) -> str:
    lowered = text.lower()
    max_length = min(len(text), max(len(marker) for marker in markers))

    for suffix_length in range(max_length, 0, -1):
        suffix = lowered[-suffix_length:]
        if any(marker.startswith(suffix) for marker in markers):
            return text[-suffix_length:]
    return ""
