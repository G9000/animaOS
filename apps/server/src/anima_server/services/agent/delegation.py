"""Tool delegation: send tool_execute to a connected client and await tool_result."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)


class DelegationTimeout(Exception):
    """Raised when a delegated tool call times out."""

    pass


@dataclass
class DelegatedToolResult:
    """Result from a client-side tool execution."""

    call_id: str
    name: str
    output: str
    is_error: bool = False
    stdout: list[str] | None = None
    stderr: list[str] | None = None


class ToolDelegator:
    """Delegates tool execution to a connected WebSocket client.

    When the server's agent loop calls an action tool (bash, read_file, etc.),
    the delegator sends a ``tool_execute`` message to the CLI client and waits
    for the corresponding ``tool_result`` response.

    Usage::

        delegator = ToolDelegator(send_fn=ws.send_json)

        # In the agent loop — blocks until the client replies or timeout:
        result = await delegator.delegate("tc_123", "bash", {"command": "ls"})

        # When a tool_result message arrives from the client:
        delegator.resolve("tc_123", data)

        # On disconnect:
        delegator.cancel_all("Client disconnected")
    """

    def __init__(
        self,
        send_fn: Callable[[dict[str, Any]], Awaitable[None]],
        timeout: float = 300.0,
    ) -> None:
        self._send = send_fn
        self._timeout = timeout
        self._pending: dict[str, asyncio.Future[DelegatedToolResult]] = {}

    async def delegate(
        self,
        tool_call_id: str,
        tool_name: str,
        args: dict[str, Any],
    ) -> DelegatedToolResult:
        """Send ``tool_execute`` to the client and wait for ``tool_result``.

        Raises:
            DelegationTimeout: if no result arrives within the configured timeout.
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future[DelegatedToolResult] = loop.create_future()
        self._pending[tool_call_id] = future

        await self._send(
            {
                "type": "tool_execute",
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "args": args,
            }
        )

        try:
            return await asyncio.wait_for(future, timeout=self._timeout)
        except asyncio.TimeoutError:
            self._pending.pop(tool_call_id, None)
            raise DelegationTimeout(
                f"Tool {tool_name} (call_id={tool_call_id}) timed out after {self._timeout}s"
            )

    def resolve(self, tool_call_id: str, data: dict[str, Any]) -> None:
        """Resolve a pending delegation with the client's result.

        Called when a ``tool_result`` message arrives from the WebSocket client.
        If no pending future exists for *tool_call_id* (e.g. it already timed
        out), a warning is logged and the result is discarded.
        """
        future = self._pending.pop(tool_call_id, None)
        if future is None:
            logger.warning(
                "Received tool_result for unknown call_id: %s", tool_call_id
            )
            return
        if future.done():
            return

        is_error = data.get("status") == "error"
        result = DelegatedToolResult(
            call_id=tool_call_id,
            name=data.get("tool_name", ""),
            output=data.get("result", ""),
            is_error=is_error,
            stdout=data.get("stdout"),
            stderr=data.get("stderr"),
        )
        future.set_result(result)

    def cancel_all(self, reason: str = "Connection lost") -> None:
        """Cancel all pending delegations (e.g. on client disconnect)."""
        for call_id, future in self._pending.items():
            if not future.done():
                future.set_exception(DelegationTimeout(reason))
        self._pending.clear()
