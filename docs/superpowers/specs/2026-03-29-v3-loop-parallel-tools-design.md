# V3-Style Loop + Parallel Tool Calls

**Date:** 2026-03-29
**Status:** Approved

## Problem

1. The `request_heartbeat` parameter adds complexity, wastes tokens (~200-400 per request across 15 tool schemas), and fails silently when models forget to set it — causing turns with no user-visible response.
2. Tool calls execute sequentially even when independent, adding unnecessary LLM round-trips.

## Design

### V3-Style Loop

Drop `request_heartbeat`. Always continue the agent loop after non-terminal tool calls. Only stop on:
- Terminal tool (`send_message`) called
- `max_steps` reached
- Cancellation or approval

Inject a sandwich message between steps: "Continue with your next tool call or send_message when ready."

### Parallel Tool Calls

When the LLM returns multiple tool_calls in one step:
- Split into terminal (`send_message`) and non-terminal tools
- Execute all non-terminal tools concurrently via `asyncio.gather`
- Execute `send_message` last (sequentially, after others complete)
- Call `_decide_continuation` per tool — if any is terminal, loop stops after this step
- Pass `parallel_tool_calls: true` in the OpenAI API request

All cognitive tools are safe to parallelize (no shared mutable state between calls).

### Files Changed

- `runtime.py` — loop continuation, parallel execution
- `executor.py` — remove heartbeat unpacking
- `tools.py` — remove heartbeat injection
- `runtime_types.py` — remove `heartbeat_requested` field
- `openai_compatible_client.py` — pass `parallel_tool_calls`
- `streaming.py` — stop stripping `request_heartbeat`
- `system_prompt.md.j2` — remove heartbeat instructions

## Prior Art

Letta V3 (`letta_agent_v3.py`) uses the same approach: no heartbeat, auto-continue after non-terminal tools, `asyncio.gather` for parallel execution, per-tool continuation decisions.
