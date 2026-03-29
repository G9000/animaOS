# apps/server/tests/test_health_tool.py
from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_check_system_health_tool():
    from anima_server.services.agent.tools import check_system_health

    # Mock the tool context so the tool can get user_id.
    # The lazy import inside check_system_health resolves from tool_context module.
    mock_ctx = MagicMock()
    mock_ctx.user_id = 1

    with patch(
        "anima_server.services.agent.tool_context.get_tool_context",
        return_value=mock_ctx,
    ):
        result = check_system_health()

    assert isinstance(result, str)
    assert "System Health:" in result


def test_check_system_health_in_extension_tools():
    from anima_server.services.agent.tools import get_extension_tools

    tools = get_extension_tools()
    tool_names = [getattr(t, "name", None) or t.__name__ for t in tools]
    assert "check_system_health" in tool_names


def test_check_system_health_has_tool_decorator():
    from anima_server.services.agent.tools import check_system_health

    # Verify the @tool decorator was applied (gives the function a .name attribute)
    assert hasattr(check_system_health, "name")
    assert check_system_health.name == "check_system_health"
    assert hasattr(check_system_health, "args_schema")
