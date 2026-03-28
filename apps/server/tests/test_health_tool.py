# apps/server/tests/test_health_tool.py
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_check_system_health_tool():
    from anima_server.services.agent.tools import check_system_health

    result = await check_system_health(user_id=1)
    assert isinstance(result, str)
    assert "System Health:" in result


def test_check_system_health_in_extension_tools():
    from anima_server.services.agent.tools import get_extension_tools

    tools = get_extension_tools()
    tool_names = [t.__name__ if hasattr(t, '__name__') else str(t) for t in tools]
    assert "check_system_health" in tool_names
