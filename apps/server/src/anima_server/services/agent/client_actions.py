"""Connected client action tools for Animus-style local execution."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from anima_server.services.agent.delegation import (
    DelegatedToolResult,
    DelegationTimeout,
)

logger = logging.getLogger(__name__)


SendJson = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class ActionToolConnection:
    """A connected client that may provide local action tools."""

    websocket: Any
    user_id: int
    username: str
    action_tool_schemas: list[dict[str, Any]] = field(default_factory=list)
    connected_at: float = 0.0


@dataclass
class _PendingActionCall:
    connection: ActionToolConnection
    future: asyncio.Future[DelegatedToolResult]
    tool_name: str


class ClientActionRegistry:
    """Tracks connected action-tool clients and routes delegated calls."""

    def __init__(self) -> None:
        self._connections: dict[int, list[ActionToolConnection]] = {}
        self._pending: dict[str, _PendingActionCall] = {}

    def add(self, conn: ActionToolConnection) -> None:
        if conn.connected_at == 0.0:
            conn.connected_at = time.monotonic()
        self._connections.setdefault(conn.user_id, []).append(conn)

    def remove(self, conn: ActionToolConnection) -> None:
        self.cancel_pending_for_connection(conn, "Client disconnected")
        conns = self._connections.get(conn.user_id, [])
        if conn in conns:
            conns.remove(conn)
        if not conns:
            self._connections.pop(conn.user_id, None)

    def get_connections(self, user_id: int) -> list[ActionToolConnection]:
        return list(self._connections.get(user_id, []))

    def has_connections(self, user_id: int) -> bool:
        return bool(self._connections.get(user_id))

    def update_tool_schemas(
        self,
        conn: ActionToolConnection,
        schemas: object,
    ) -> None:
        conn.action_tool_schemas = [
            schema
            for schema in schemas
            if isinstance(schema, dict) and str(schema.get("name", "")).strip()
        ] if isinstance(schemas, list) else []

    def get_delegate(
        self,
        user_id: int,
        tool_name: str,
    ) -> ActionToolConnection | None:
        """Return the newest connection that registered *tool_name*."""
        for conn in reversed(self._connections.get(user_id, [])):
            tool_names = {
                str(schema.get("name", "")).strip()
                for schema in conn.action_tool_schemas
            }
            if tool_name in tool_names:
                return conn
        return None

    def get_action_tool_schemas(self, user_id: int) -> list[dict[str, Any]]:
        """Return merged action tool schemas for a user, newest wins."""
        seen: set[str] = set()
        schemas: list[dict[str, Any]] = []
        for conn in reversed(self._connections.get(user_id, [])):
            for schema in conn.action_tool_schemas:
                name = str(schema.get("name", "")).strip()
                if not name or name in seen:
                    continue
                seen.add(name)
                schemas.append(schema)
        return schemas

    def get_action_tool_names(self, user_id: int) -> frozenset[str]:
        return frozenset(
            str(schema.get("name", "")).strip()
            for schema in self.get_action_tool_schemas(user_id)
            if str(schema.get("name", "")).strip()
        )

    async def delegate(
        self,
        user_id: int,
        tool_call_id: str,
        tool_name: str,
        args: dict[str, Any],
        *,
        timeout: float = 300.0,
    ) -> DelegatedToolResult:
        conn = self.get_delegate(user_id, tool_name)
        if conn is None:
            raise DelegationTimeout(
                f"No connected client has registered action tool {tool_name!r}"
            )

        loop = asyncio.get_running_loop()
        future: asyncio.Future[DelegatedToolResult] = loop.create_future()
        self._pending[tool_call_id] = _PendingActionCall(
            connection=conn,
            future=future,
            tool_name=tool_name,
        )

        try:
            await conn.websocket.send_json(
                {
                    "type": "tool_execute",
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "args": args,
                }
            )
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError as err:
            self._pending.pop(tool_call_id, None)
            raise DelegationTimeout(
                f"Tool {tool_name} (call_id={tool_call_id}) timed out after {timeout}s"
            ) from err
        except Exception:
            self._pending.pop(tool_call_id, None)
            raise

    def resolve_tool_result(
        self,
        tool_call_id: str,
        data: dict[str, Any],
    ) -> bool:
        pending = self._pending.pop(tool_call_id, None)
        if pending is None:
            logger.warning("Received tool_result for unknown call_id: %s", tool_call_id)
            return False
        if pending.future.done():
            return True

        pending.future.set_result(
            DelegatedToolResult(
                call_id=tool_call_id,
                name=str(data.get("tool_name", "")),
                output=str(data.get("result", "")),
                is_error=data.get("status") == "error",
                stdout=data.get("stdout"),
                stderr=data.get("stderr"),
            )
        )
        return True

    def cancel_pending_for_connection(
        self,
        conn: ActionToolConnection,
        reason: str,
    ) -> None:
        for call_id, pending in list(self._pending.items()):
            if pending.connection is not conn:
                continue
            self._pending.pop(call_id, None)
            if not pending.future.done():
                pending.future.set_exception(DelegationTimeout(reason))


@dataclass(frozen=True, slots=True)
class ClientActionRuntime:
    user_id: int
    registry: ClientActionRegistry
    action_tool_schemas: tuple[dict[str, Any], ...]
    delegated_tool_names: frozenset[str]

    async def delegate(
        self,
        tool_call_id: str,
        tool_name: str,
        args: dict[str, Any],
    ) -> DelegatedToolResult:
        return await self.registry.delegate(
            self.user_id,
            tool_call_id,
            tool_name,
            args,
        )

    def close(self) -> None:
        return None


action_registry = ClientActionRegistry()


def build_client_action_runtime(
    user_id: int,
    *,
    registry: ClientActionRegistry = action_registry,
) -> ClientActionRuntime | None:
    schemas = tuple(registry.get_action_tool_schemas(user_id))
    if not schemas:
        return None
    return ClientActionRuntime(
        user_id=user_id,
        registry=registry,
        action_tool_schemas=schemas,
        delegated_tool_names=registry.get_action_tool_names(user_id),
    )
