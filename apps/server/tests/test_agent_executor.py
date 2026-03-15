from __future__ import annotations

import pytest

from anima_server.services.agent.executor import ToolExecutor, _stringify_output
from anima_server.services.agent.runtime_types import ToolCall, ToolExecutionResult


# --------------------------------------------------------------------------- #
# Helpers – fake tool objects
# --------------------------------------------------------------------------- #


class AsyncTool:
    """Tool that supports ainvoke."""

    name = "async_tool"

    async def ainvoke(self, payload: dict) -> str:
        return f"async result: {payload}"


class SyncTool:
    """Tool that supports invoke (no ainvoke)."""

    name = "sync_tool"

    def invoke(self, payload: dict) -> str:
        return f"sync result: {payload}"


class CallableTool:
    """Tool that is callable (no invoke/ainvoke)."""

    name = "callable_tool"

    def __call__(self, **kwargs: object) -> str:
        return f"called with {kwargs}"


class CallableNoArgsTool:
    """Callable with no arguments."""

    name = "no_args_tool"

    def __call__(self) -> str:
        return "no args result"


class FailingTool:
    """Tool that raises on invoke."""

    name = "failing_tool"

    async def ainvoke(self, payload: dict) -> str:
        raise RuntimeError("tool exploded")


class DictReturningTool:
    """Tool that returns a dict."""

    name = "dict_tool"

    async def ainvoke(self, payload: dict) -> dict:
        return {"key": "value", "count": 3}


class ListReturningTool:
    """Tool that returns a list."""

    name = "list_tool"

    async def ainvoke(self, payload: dict) -> list:
        return [1, 2, 3]


class IntReturningTool:
    """Tool that returns an int."""

    name = "int_tool"

    async def ainvoke(self, payload: dict) -> int:
        return 42


# --------------------------------------------------------------------------- #
# ToolExecutor.__init__
# --------------------------------------------------------------------------- #


def test_executor_registers_tools_by_name() -> None:
    executor = ToolExecutor([AsyncTool(), SyncTool()])
    assert "async_tool" in executor._tools
    assert "sync_tool" in executor._tools


# --------------------------------------------------------------------------- #
# Unknown tool
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_executor_unknown_tool_returns_error() -> None:
    executor = ToolExecutor([AsyncTool()])
    tc = ToolCall(id="c1", name="nonexistent", arguments={})
    result = await executor.execute(tc)
    assert result.is_error is True
    assert "Unknown tool" in result.output
    assert result.name == "nonexistent"
    assert result.call_id == "c1"


# --------------------------------------------------------------------------- #
# Parse error
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_executor_parse_error_returns_error() -> None:
    executor = ToolExecutor([AsyncTool()])
    tc = ToolCall(
        id="c2",
        name="async_tool",
        arguments={},
        parse_error="invalid json",
        raw_arguments='{"bad": ',
    )
    result = await executor.execute(tc)
    assert result.is_error is True
    assert "malformed arguments" in result.output
    assert "invalid json" in result.output


@pytest.mark.asyncio
async def test_executor_parse_error_truncates_raw_arguments() -> None:
    """Raw arguments are truncated to 200 chars."""
    executor = ToolExecutor([AsyncTool()])
    long_raw = "x" * 500
    tc = ToolCall(
        id="c3",
        name="async_tool",
        arguments={},
        parse_error="bad",
        raw_arguments=long_raw,
    )
    result = await executor.execute(tc)
    assert result.is_error is True
    # The raw args slice [:200] limits what appears in the output
    assert "x" * 200 in result.output
    assert "x" * 201 not in result.output


# --------------------------------------------------------------------------- #
# Successful invocations
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_executor_async_tool() -> None:
    executor = ToolExecutor([AsyncTool()])
    tc = ToolCall(id="c4", name="async_tool", arguments={"q": "test"})
    result = await executor.execute(tc)
    assert result.is_error is False
    assert "async result" in result.output
    assert result.name == "async_tool"


@pytest.mark.asyncio
async def test_executor_sync_tool() -> None:
    executor = ToolExecutor([SyncTool()])
    tc = ToolCall(id="c5", name="sync_tool", arguments={"q": "test"})
    result = await executor.execute(tc)
    assert result.is_error is False
    assert "sync result" in result.output


@pytest.mark.asyncio
async def test_executor_callable_tool() -> None:
    executor = ToolExecutor([CallableTool()])
    tc = ToolCall(id="c6", name="callable_tool", arguments={"x": 1})
    result = await executor.execute(tc)
    assert result.is_error is False
    assert "called with" in result.output


@pytest.mark.asyncio
async def test_executor_callable_no_args() -> None:
    executor = ToolExecutor([CallableNoArgsTool()])
    tc = ToolCall(id="c7", name="no_args_tool", arguments={})
    result = await executor.execute(tc)
    assert result.is_error is False
    assert result.output == "no args result"


# --------------------------------------------------------------------------- #
# Terminal flag
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_executor_terminal_flag_passed_through() -> None:
    executor = ToolExecutor([AsyncTool()])
    tc = ToolCall(id="c8", name="async_tool", arguments={})
    result = await executor.execute(tc, is_terminal=True)
    assert result.is_terminal is True


@pytest.mark.asyncio
async def test_executor_non_terminal_by_default() -> None:
    executor = ToolExecutor([AsyncTool()])
    tc = ToolCall(id="c9", name="async_tool", arguments={})
    result = await executor.execute(tc)
    assert result.is_terminal is False


# --------------------------------------------------------------------------- #
# Tool execution exception
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_executor_tool_exception_returns_error() -> None:
    executor = ToolExecutor([FailingTool()])
    tc = ToolCall(id="c10", name="failing_tool", arguments={})
    result = await executor.execute(tc)
    assert result.is_error is True
    assert "tool exploded" in result.output
    assert result.name == "failing_tool"


# --------------------------------------------------------------------------- #
# Output stringification
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_executor_dict_output_json_serialized() -> None:
    executor = ToolExecutor([DictReturningTool()])
    tc = ToolCall(id="c11", name="dict_tool", arguments={})
    result = await executor.execute(tc)
    assert '"count": 3' in result.output
    assert '"key": "value"' in result.output


@pytest.mark.asyncio
async def test_executor_list_output_json_serialized() -> None:
    executor = ToolExecutor([ListReturningTool()])
    tc = ToolCall(id="c12", name="list_tool", arguments={})
    result = await executor.execute(tc)
    assert result.output == "[1, 2, 3]"


@pytest.mark.asyncio
async def test_executor_int_output_stringified() -> None:
    executor = ToolExecutor([IntReturningTool()])
    tc = ToolCall(id="c13", name="int_tool", arguments={})
    result = await executor.execute(tc)
    assert result.output == "42"


def test_stringify_output_string_passthrough() -> None:
    assert _stringify_output("hello") == "hello"


def test_stringify_output_dict() -> None:
    assert _stringify_output({"a": 1}) == '{"a": 1}'


def test_stringify_output_list() -> None:
    assert _stringify_output([1, 2]) == "[1, 2]"


def test_stringify_output_other() -> None:
    assert _stringify_output(42) == "42"
