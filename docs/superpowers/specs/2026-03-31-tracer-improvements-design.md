# Tracer Improvements Design

**Date:** 2026-03-31
**Issue:** #45
**Scope:** 5 targeted changes to the agent tracer, ~85 lines total

## Context

While debugging agent memory issues (#41-#44) via trace analysis, several gaps in the tracer made diagnosis harder than necessary. These are all small, surgical fixes.

## Changes

### 1. Fix TTFT for tool-call-only responses

**Problem:** `first_content_time` in `openai_compatible.py` only triggers on `content_delta`. When `forceToolCall: true` and the model returns only tool calls, TTFT is always null.

**Fix:** Set `first_content_time` on the first tool call delta chunk when no content has arrived yet.

**File:** `adapters/openai_compatible.py` (both streaming paths)

### 2. Tool success/failure semantic tag

**Problem:** `tool_return` events have `isError` but it's buried. The `save_to_memory` false-OK case is fixed by #47 (raises on failure), so `isError` is now reliable.

**Fix:** Add `toolSucceeded: !is_error` to `tool_return` events for explicit semantic tagging.

**File:** `streaming.py` (`build_tool_return_event`)

### 3. Tool schemas in step_request (step 0 only)

**Problem:** Traces show `allowedTools` names but not their JSON schemas. Can't verify if `thinking` is marked required, what params exist, etc.

**Fix:** Add `toolSchemas` dict to the first `step_state` request event (step 0). Maps tool name to its JSON schema. Only emitted once per turn to keep trace size reasonable.

**Files:** `streaming.py` (`build_step_request_event`), `runtime.py` (pass schemas through)

### 4. Memory state snapshot event

**Problem:** No visibility into core memory block contents at turn start. Had to infer empty human memory from `recall_memory` returning nothing.

**Fix:** New `memory_state` event emitted before step 0 with full core memory block text:
```json
{
  "type": "memory_state",
  "blocks": {
    "persona": "full persona text...",
    "human": "full human block text..."
  }
}
```

**Files:** `streaming.py` (new builder), `runtime.py` (emit event at turn start)

### 5. Search path details in recall_memory

**Problem:** When `recall_memory` returns empty, no visibility into which search paths were tried or failed silently.

**Fix:** Include search path diagnostics in the tool return message: hybrid (count), keyword (count), episode (count). Replace `except Exception: pass` with `logger.warning`.

**File:** `tools.py` (`recall_memory`)

## Non-goals

- Reasoning capture: GPT-4o doesn't emit reasoning tokens. Not fixable at the tracer level.
- Frontend type updates: `TraceEvent` type in `types.ts` will need the new fields, but the frontend text serializer already handles unknown fields gracefully via `compactJson`.

## Testing

- Existing streaming/trace tests must pass
- TTFT fix: verify non-null TTFT on tool-call-only responses
- New events: verify they appear in trace output
