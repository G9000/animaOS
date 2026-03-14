from __future__ import annotations

import json
from typing import Any

from anima_server.services.agent.runtime_types import ToolCall, ToolExecutionResult


class ToolExecutor:
    def __init__(self, tools: list[Any]) -> None:
        self._tools = {
            (getattr(tool, "name", "") or getattr(tool, "__name__", "")): tool
            for tool in tools
        }

    async def execute(
        self,
        tool_call: ToolCall,
        *,
        is_terminal: bool = False,
    ) -> ToolExecutionResult:
        tool = self._tools.get(tool_call.name)
        if tool is None:
            return ToolExecutionResult(
                call_id=tool_call.id,
                name=tool_call.name,
                output=f"Unknown tool: {tool_call.name}",
                is_error=True,
            )

        if tool_call.parse_error is not None:
            raw = tool_call.raw_arguments or ""
            return ToolExecutionResult(
                call_id=tool_call.id,
                name=tool_call.name,
                output=(
                    f"Tool {tool_call.name} received malformed arguments: "
                    f"{tool_call.parse_error} Raw arguments: {raw[:200]}"
                ).rstrip(),
                is_error=True,
            )

        try:
            output = await _invoke_tool(tool, tool_call.arguments)
        except Exception as exc:
            return ToolExecutionResult(
                call_id=tool_call.id,
                name=tool_call.name,
                output=f"Tool {tool_call.name} failed: {exc}",
                is_error=True,
            )

        return ToolExecutionResult(
            call_id=tool_call.id,
            name=tool_call.name,
            output=_stringify_output(output),
            is_terminal=is_terminal,
        )


async def _invoke_tool(tool: Any, arguments: dict[str, Any]) -> Any:
    payload: Any = arguments or {}

    if hasattr(tool, "ainvoke"):
        return await tool.ainvoke(payload)

    if hasattr(tool, "invoke"):
        return tool.invoke(payload)

    if arguments:
        return tool(**arguments)

    return tool()


def _stringify_output(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return str(value)
