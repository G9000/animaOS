from __future__ import annotations

import asyncio
import contextvars
import inspect
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC
from typing import Any

from anima_server.config import settings
from anima_server.services.agent.runtime_types import ToolCall, ToolExecutionResult

logger = logging.getLogger(__name__)


def unpack_inner_thoughts_from_kwargs(
    tool_call: ToolCall,
    inner_thoughts_key: str = "thinking",
) -> str | None:
    """Pop the ``thinking`` kwarg from *tool_call.arguments* (in-place).

    Returns the extracted thought string, or ``None`` if absent.
    The tool function never sees this parameter — it is consumed here.
    """
    if not isinstance(tool_call.arguments, dict):
        return None
    value = tool_call.arguments.pop(inner_thoughts_key, None)
    if value is None:
        return None
    # Coerce non-string values (e.g. model emits object/array) to string
    # so downstream .strip() calls never crash.
    return str(value) if not isinstance(value, str) else value


def unpack_heartbeat_from_kwargs(
    tool_call: ToolCall,
    heartbeat_key: str = "request_heartbeat",
) -> bool:
    """Pop the ``request_heartbeat`` kwarg from *tool_call.arguments*.

    Returns True if the model requested a follow-up step, False otherwise.
    Handles stringified booleans (``"true"``, ``"True"``) that some
    models produce instead of native JSON booleans.
    """
    if not isinstance(tool_call.arguments, dict):
        return False
    value = tool_call.arguments.pop(heartbeat_key, None)
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return bool(value)


class ToolExecutor:
    def __init__(self, tools: list[Any]) -> None:
        self._tools = {
            (getattr(tool, "name", "") or getattr(tool, "__name__", "")): tool for tool in tools
        }
        self._tool_delegate: Callable[[str, str, dict[str, Any]], Awaitable[Any]] | None = None
        self._delegated_tool_names: frozenset[str] = frozenset()

    def set_delegation(
        self,
        delegate: Callable[[str, str, dict[str, Any]], Awaitable[Any]] | None,
        tool_names: frozenset[str] = frozenset(),
    ) -> None:
        """Set per-turn delegation. Call with None to clear."""
        self._tool_delegate = delegate
        self._delegated_tool_names = tool_names

    def clear_delegation(self) -> None:
        self._tool_delegate = None
        self._delegated_tool_names = frozenset()

    async def execute(
        self,
        tool_call: ToolCall,
        *,
        is_terminal: bool = False,
    ) -> ToolExecutionResult:
        # Strip injected kwargs early — before any error path can
        # echo them back in raw_arguments or error messages.
        thinking = unpack_inner_thoughts_from_kwargs(tool_call)
        heartbeat = unpack_heartbeat_from_kwargs(tool_call)

        # Check if this tool should be delegated to a connected client.
        # This MUST happen before the local tool lookup so that action
        # tools (bash, read_file, etc.) which aren't in self._tools
        # are forwarded to the client instead of erroring.
        if self._tool_delegate and tool_call.name in self._delegated_tool_names:
            clean_args = dict(tool_call.arguments) if tool_call.arguments else {}
            try:
                delegated = await self._tool_delegate(tool_call.id, tool_call.name, clean_args)
                formatted_output = (
                    _package_tool_response(delegated.output)
                    if not delegated.is_error
                    else _package_error_response(delegated.output)
                )
                return ToolExecutionResult(
                    call_id=tool_call.id,
                    name=tool_call.name,
                    output=formatted_output,
                    is_error=delegated.is_error,
                    inner_thinking=thinking,
                    heartbeat_requested=heartbeat,
                )
            except Exception as exc:
                return ToolExecutionResult(
                    call_id=tool_call.id,
                    name=tool_call.name,
                    output=_package_error_response(f"Tool delegation failed: {exc}"),
                    is_error=True,
                    inner_thinking=thinking,
                    heartbeat_requested=heartbeat,
                )

        tool = self._tools.get(tool_call.name)
        if tool is None:
            return ToolExecutionResult(
                call_id=tool_call.id,
                name=tool_call.name,
                output=f"Unknown tool: {tool_call.name}",
                is_error=True,
            )

        if tool_call.parse_error is not None:
            # Redact thinking from raw_arguments string (the dict pop
            # above only affects parsed args, not the raw JSON string).
            raw = tool_call.raw_arguments or ""
            if raw and "thinking" in raw:
                import json as _json

                try:
                    parsed_raw = _json.loads(raw)
                    if isinstance(parsed_raw, dict):
                        parsed_raw.pop("thinking", None)
                        raw = _json.dumps(parsed_raw)
                except (ValueError, TypeError):
                    pass
            return ToolExecutionResult(
                call_id=tool_call.id,
                name=tool_call.name,
                output=_package_error_response(
                    f"Tool {tool_call.name} received malformed arguments: "
                    f"{tool_call.parse_error} Raw arguments: {raw[:200]}"
                ),
                is_error=True,
            )

        # Shared flag container — the tool (running in a thread) writes to
        # this dict, and we read it back in the async context.
        validation_error = _validate_tool_arguments(tool, tool_call.arguments)
        if validation_error is not None:
            return ToolExecutionResult(
                call_id=tool_call.id,
                name=tool_call.name,
                output=_package_error_response(validation_error),
                is_error=True,
            )

        flags: dict[str, bool] = {"memory_modified": False}

        try:
            timeout = settings.agent_tool_timeout
            output = await asyncio.wait_for(
                _invoke_tool(tool, tool_call.arguments, flags),
                timeout=timeout,
            )
        except TimeoutError:
            return ToolExecutionResult(
                call_id=tool_call.id,
                name=tool_call.name,
                output=_package_error_response(
                    f"Tool {tool_call.name} timed out after {settings.agent_tool_timeout}s"
                ),
                is_error=True,
            )
        except Exception as exc:
            return ToolExecutionResult(
                call_id=tool_call.id,
                name=tool_call.name,
                output=_package_error_response(f"Tool {tool_call.name} failed: {exc}"),
                is_error=True,
            )

        # Terminal tools (send_message) return raw output — it's the
        # user-facing response.  Non-terminal tools get the JSON envelope
        # so the model sees structured status/message/time.
        formatted_output = (
            _stringify_output(output) if is_terminal else _package_tool_response(output)
        )

        return ToolExecutionResult(
            call_id=tool_call.id,
            name=tool_call.name,
            output=formatted_output,
            is_terminal=is_terminal,
            memory_modified=flags["memory_modified"],
            inner_thinking=thinking,
            heartbeat_requested=heartbeat,
        )

    async def execute_parallel(
        self,
        tool_calls: list[tuple[ToolCall, bool]],
    ) -> list[ToolExecutionResult]:
        """Execute multiple independent tool calls concurrently.

        Each entry is (tool_call, is_terminal).  Returns results in the
        same order as the input.
        """
        if len(tool_calls) <= 1:
            results = []
            for tc, terminal in tool_calls:
                results.append(await self.execute(tc, is_terminal=terminal))
            return results

        tasks = [self.execute(tc, is_terminal=terminal) for tc, terminal in tool_calls]
        return list(await asyncio.gather(*tasks))


async def _invoke_tool(
    tool: Any,
    arguments: dict[str, Any],
    flags: dict[str, bool],
) -> Any:
    payload: Any = arguments or {}

    if hasattr(tool, "ainvoke"):
        result = await tool.ainvoke(payload)
        _check_memory_modified(flags)
        return result

    if hasattr(tool, "invoke"):
        result = tool.invoke(payload)
        _check_memory_modified(flags)
        return result

    # Run synchronous tools in a thread, copying the current context
    # so ContextVar-based state (like ToolContext) is accessible.
    ctx = contextvars.copy_context()

    def _run() -> Any:
        if arguments:
            return tool(**arguments)
        return tool()

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, ctx.run, _run)

    # After the thread completes, check both the copied context and the
    # main context for memory_modified.
    _check_memory_modified(flags)
    # Also check the copied context's ToolContext (thread may have set it there).
    try:
        from anima_server.services.agent.tool_context import _current_context

        thread_ctx = ctx.get(_current_context)
        if thread_ctx is not None and thread_ctx.memory_modified:
            flags["memory_modified"] = True
            # Propagate back to the main context's ToolContext.
            main_ctx = _current_context.get()
            if main_ctx is not None:
                main_ctx.memory_modified = True
    except (RuntimeError, LookupError):
        pass

    return result


def _check_memory_modified(flags: dict[str, bool]) -> None:
    """Check if the current ToolContext has memory_modified set."""
    try:
        from anima_server.services.agent.tool_context import get_tool_context

        ctx = get_tool_context()
        if ctx.memory_modified:
            flags["memory_modified"] = True
            ctx.memory_modified = False  # reset for next tool
    except RuntimeError:
        pass


def _validate_tool_arguments(
    tool: Any,
    arguments: dict[str, Any],
    *,
    ignore_keys: tuple[str, ...] = ("thinking", "request_heartbeat"),
) -> str | None:
    required_arguments = _get_required_tool_arguments(tool)
    if not required_arguments:
        return None

    # Skip injected keys (e.g. ``thinking``) that are stripped before dispatch.
    effective_required = tuple(name for name in required_arguments if name not in ignore_keys)
    if not effective_required:
        return None

    payload = arguments if isinstance(arguments, dict) else {}
    missing = [name for name in effective_required if name not in payload or payload[name] is None]
    if not missing:
        return None

    noun = "argument" if len(missing) == 1 else "arguments"
    missing_list = ", ".join(missing)
    tool_name = getattr(tool, "name", "") or getattr(tool, "__name__", "unknown")
    return (
        f"Tool {tool_name} is missing required {noun}: {missing_list}. "
        "Provide a JSON object with all required fields."
    )


def _get_required_tool_arguments(tool: Any) -> tuple[str, ...]:
    schema = _get_tool_schema(tool)
    if schema is not None:
        required = schema.get("required", [])
        return tuple(name for name in required if isinstance(name, str))

    if hasattr(tool, "ainvoke") or hasattr(tool, "invoke"):
        return ()

    try:
        signature = inspect.signature(tool)
    except (TypeError, ValueError):
        return ()

    required: list[str] = []
    for name, param in signature.parameters.items():
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        if param.default is inspect.Parameter.empty:
            required.append(name)
    return tuple(required)


def _get_tool_schema(tool: Any) -> dict[str, Any] | None:
    schema = getattr(tool, "args_schema", None)
    if schema is None or not hasattr(schema, "model_json_schema"):
        return None

    try:
        resolved = schema.model_json_schema()
    except Exception:
        return None

    return resolved if isinstance(resolved, dict) else None


_TOOL_RETURN_CHAR_LIMIT = 50_000
_ERROR_MESSAGE_CHAR_LIMIT = 1000


def _package_tool_response(
    output: Any,
    *,
    was_success: bool = True,
    char_limit: int = _TOOL_RETURN_CHAR_LIMIT,
) -> str:
    """Format a tool result into a uniform JSON envelope.

    Returns ``{"status": "OK"|"Failed", "message": "...", "time": "..."}``.
    Truncates the message with an in-band warning if it exceeds *char_limit*.
    """
    from datetime import datetime

    message = _stringify_output(output)

    if char_limit and len(message) > char_limit:
        message = (
            f"{message[:char_limit]}... [NOTE: output truncated, "
            f"{len(message)} > {char_limit} chars]"
        )

    return json.dumps(
        {
            "status": "OK" if was_success else "Failed",
            "message": message,
            "time": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
        }
    )


def _package_error_response(error_msg: str) -> str:
    """Format an error into the uniform envelope with truncation."""
    if len(error_msg) > _ERROR_MESSAGE_CHAR_LIMIT:
        error_msg = error_msg[:_ERROR_MESSAGE_CHAR_LIMIT] + "..."
    return _package_tool_response(error_msg, was_success=False)


def _stringify_output(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return str(value)
