import asyncio
import pytest
from anima_server.services.agent.delegation import ToolDelegator, DelegationTimeout


@pytest.mark.asyncio
async def test_delegate_resolves_on_result():
    """Delegator sends tool_execute and resolves when tool_result arrives."""
    sent_messages = []

    async def mock_send(msg):
        sent_messages.append(msg)

    delegator = ToolDelegator(send_fn=mock_send)

    # Start delegation in background
    task = asyncio.create_task(
        delegator.delegate("tc_1", "bash", {"command": "ls"})
    )
    await asyncio.sleep(0.01)  # Let it send

    assert len(sent_messages) == 1
    assert sent_messages[0]["type"] == "tool_execute"
    assert sent_messages[0]["tool_name"] == "bash"

    # Resolve it
    delegator.resolve("tc_1", {"status": "success", "result": "file1.txt\nfile2.txt"})
    result = await task

    assert result.output == "file1.txt\nfile2.txt"
    assert not result.is_error


@pytest.mark.asyncio
async def test_delegate_timeout():
    """Delegation times out if no result arrives."""

    async def mock_send(msg):
        pass

    delegator = ToolDelegator(send_fn=mock_send, timeout=0.05)

    with pytest.raises(DelegationTimeout):
        await delegator.delegate("tc_2", "bash", {"command": "sleep 999"})
