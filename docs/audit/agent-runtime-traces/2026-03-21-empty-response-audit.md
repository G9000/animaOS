# Agent Runtime Trace Audit — Empty LLM Response

**Date**: 2026-03-21
**Auditor**: Claude (Opus 4.6) — Systematic Debugging
**Scope**: Agent turn producing zero output — empty assistant text, zero tool calls, despite `forceToolCall: true`
**Provider**: Ollama (local)
**Model**: `vaultbox/qwen3.5-uncensored:35b`
**Related**: [2026-03-20 audit](2026-03-20-agent-trace-audit.md) — finding D1 (model ignores `forceToolCall`) escalated

---

## Remediation Status

| Finding | Severity | Status | Fix Date | Implementer |
|---------|----------|--------|----------|-------------|
| C1 — Model returns completely empty response under `tool_choice: "required"` | Critical | **FIXED** | 2026-03-21 | Claude (Opus 4.6) + Codex (GPT-5.4) |
| C2 — No recovery path for empty forced-tool response | Critical | **FIXED** | 2026-03-21 | Claude (Opus 4.6) + Codex (GPT-5.4) |
| D1 — `ttftMs: null` confirms zero tokens generated | Degraded | By Design | — | Model limitation; adapter retry mitigates |
| O1 — 10.3s wall time for zero output | Opportunity | Mitigated | 2026-03-21 | Adapter retry avoids silence; latency remains model-bound |

---

## Trace: Casual Conversation (3rd user message)

### Raw Trace

```json
[
  {
    "type": "step_state",
    "stepIndex": 0,
    "phase": "request",
    "messageCount": 8,
    "allowedTools": ["inner_thought"],
    "forceToolCall": true,
    "messages": [
      { "role": "system", "chars": 13988 },
      { "role": "user", "chars": 15, "preview": "Hello I'm Julio" },
      { "role": "assistant", "chars": 63, "preview": "Hello Julio. It's good to see you again. How has your day been?", "toolCallCount": 1 },
      { "role": "tool", "chars": 63, "toolName": "send_message", "toolCallId": "synthetic-send_message-1-0" },
      { "role": "user", "chars": 11, "preview": "Quite tired" },
      { "role": "assistant", "chars": 127, "preview": "That weariness can be heavy. Would it help to just rest a bit...", "toolCallCount": 1 },
      { "role": "tool", "chars": 127, "toolName": "send_message", "toolCallId": "synthetic-send_message-0-0" },
      { "role": "user", "chars": 47, "preview": "Just now i was fixing my CEO vibe coded project" }
    ]
  },
  {
    "type": "step_state",
    "stepIndex": 0,
    "phase": "result",
    "assistantTextChars": 0,
    "assistantTextPreview": "",
    "toolCallCount": 0,
    "reasoningChars": 0,
    "reasoningCaptured": false
  },
  {
    "type": "warning",
    "stepIndex": 0,
    "code": "empty_step_result",
    "message": "LLM returned no assistant text and no tool calls for this step."
  },
  {
    "type": "timing",
    "stepIndex": 0,
    "stepDurationMs": 10375,
    "llmDurationMs": 10375,
    "ttftMs": null
  },
  {
    "type": "done",
    "status": "complete",
    "stopReason": "end_turn",
    "provider": "ollama",
    "model": "vaultbox/qwen3.5-uncensored:35b",
    "toolsUsed": []
  }
]
```

### Context

- 3rd user message in an ongoing conversation (user: "Just now i was fixing my CEO vibe coded project")
- System prompt: 13,988 chars
- 8 messages in context (system + 3 user messages + 2 assistant responses + 2 tool returns)
- Prior turns used synthetic `send_message` fallback (both `toolCallId` values are `synthetic-send_message-*`)
- `allowedTools: ["inner_thought"]` — only one tool available (InitToolRule constraint)
- `forceToolCall: true` — translated to `tool_choice: "required"` in OpenAI-compatible API

### Turn Summary

- **Steps:** 1 LLM call
- **Tools called:** none
- **Assistant text:** none (0 chars)
- **Total wall time:** 10,375ms
- **TTFT:** null (never produced a single token)
- **Stop reason:** `end_turn`
- **Status:** FAILED — completely empty response

---

## Findings

### C1 — Model returns completely empty response under `tool_choice: "required"`

**Severity:** Critical
**Escalation of:** D1 from 2026-03-20 audit

In the prior audit, D1 documented the model *ignoring* `forceToolCall` by emitting plain text instead of structured tool calls. This trace represents a **worse failure mode**: the model returns **absolutely nothing** — zero text, zero tool calls, zero reasoning tokens.

**Evidence:**
- `assistantTextChars: 0` — no text generated
- `toolCallCount: 0` — no tool calls generated
- `reasoningChars: 0` — no reasoning generated
- `ttftMs: null` — the model never emitted a first token

**Data flow trace:**

1. `rules.py:293-294` — `build_default_tool_rules()` creates `InitToolRule(tool_name="inner_thought")`
2. `rules.py:223-225` — `should_force_tool_call()` returns `True` (empty call history + init rules exist)
3. `runtime.py:240-243` — `force_tool_call = True`
4. `openai_compatible.py:62-65` — `bind_tools(tools, tool_choice="required")`
5. Ollama receives request with `tool_choice: "required"` and the `inner_thought` tool schema
6. Model processes for 10.3 seconds and returns an empty completion
7. LangChain adapter returns `AIMessage` with empty content and no tool calls

**Root cause:** The Qwen 3.5 35B uncensored model does not reliably support `tool_choice: "required"` via Ollama's OpenAI-compatible endpoint. When the model cannot comply with the forced tool constraint, it produces an empty response rather than erroring or falling back to text.

**Contributing factor:** All prior assistant turns in this conversation used synthetic `send_message` fallbacks (`synthetic-send_message-1-0`, `synthetic-send_message-0-0`). The model's conversation history contains tool return messages for tool calls it never actually made. This "phantom tool history" may confuse the model about the expected tool-calling protocol.

### C2 — No recovery path for empty forced-tool response

**Severity:** Critical

When `forceToolCall: true` and the model returns nothing, the runtime has no recovery mechanism:

1. **Text coercion fails** — `runtime.py:873`: `_coerce_text_tool_calls()` exits immediately because `step_result.assistant_text.strip()` is empty. Nothing to coerce.

2. **No retry logic for this case** — `_invoke_llm_with_retry()` only retries on exceptions (timeouts, rate limits, server errors). An empty-but-successful HTTP response is not retried.

3. **Runtime proceeds to break** — `runtime.py:296-360`: no tool calls means the branch at line 296 is taken. Coercion returns `None` (no text). The step trace is recorded and the loop breaks with `stop_reason = StopReason.END_TURN`.

4. **Empty response delivered** — `runtime.py:484-485`: `_default_response(StopReason.END_TURN)` returns `""`. The user sees nothing.

**The user sent a message and received silence.** This is a broken conversation turn with no error signal to the frontend.

### D1 — `ttftMs: null` confirms zero token generation

**Severity:** Degraded

`ttftMs: null` means the streaming adapter never received a single content chunk from the model. The model consumed 10.3 seconds of compute and produced literally nothing. This is distinct from the prior audit's D1 where the model at least produced text (just not as tool calls).

### O1 — 10.3s wall time for zero output

**Severity:** Opportunity

The model spent 10,375ms processing the request before returning empty. This suggests:
- The model processed the full prompt (~14K system + ~500 chars conversation)
- It may have performed internal reasoning but produced no output tokens
- Or Ollama's tool_choice enforcement caused an internal timeout/deadlock

---

## Cross-Trace Pattern Analysis (with 2026-03-20 audit)

| Behavior | 2026-03-20 Trace 1 | 2026-03-20 Trace 2 | This Trace | Trend |
|----------|--------------------|--------------------|------------|-------|
| `forceToolCall` compliance | Obeyed | Ignored (plain text) | Ignored (empty) | **Degrading** |
| Output produced | Tool calls | Plain text | Nothing | **Degrading** |
| Synthetic fallback saved turn | N/A | Yes | No | Fallback insufficient |
| Prior synthetic tool history | No | No | Yes (2 synthetic calls) | Possible confounding factor |

**Key observation:** This trace has two prior turns that both used synthetic `send_message` fallbacks. The model sees tool return messages for tool calls it never made. This corrupted tool-call history may be contributing to the model's increasing inability to produce structured tool calls.

---

## Recommendations

### 1. Add empty-response retry with tool_choice downgrade

**Severity:** Critical
**Where:** `runtime.py:_run_step()` or `openai_compatible.py:invoke()`/`stream()`

When `force_tool_call=True` and the response is completely empty (no text, no tool calls):
1. Retry the same request with `tool_choice: "auto"` instead of `"required"`
2. If still empty, retry without tools entirely (let the model produce plain text)
3. Apply existing text coercion on the plain-text response

This is a targeted fix for the "model can't comply with required" failure mode.

### 2. Detect and signal empty forced-tool responses to the frontend

**Severity:** Critical
**Where:** `runtime.py` (invoke loop exit), streaming events

Instead of silently returning `""`, emit a specific error event:
```python
StopReason.EMPTY_RESPONSE  # new variant
```
The frontend can then show a retry button or "Something went wrong" message instead of blank silence.

### 3. Consider Ollama-specific tool_choice strategy

**Severity:** Degraded
**Where:** `openai_compatible.py:invoke()` and `stream()`

For the Ollama provider specifically, always use `tool_choice: "auto"` instead of `"required"`. The system prompt + InitToolRule + text coercion fallback already provide strong guidance. The `"required"` constraint causes more harm than good with models that don't support it.

### 4. Investigate synthetic tool history contamination

**Severity:** Degraded
**Where:** `runtime.py:_coerce_text_tool_calls()`, message persistence

Prior synthetic `send_message` calls create tool return messages for tool calls the model never made. When these persist in conversation history, the model sees a tool-calling pattern it didn't produce, which may confuse its understanding of the expected protocol.

Options:
- Mark synthetic tool calls distinctly in history so they don't look like native model behavior
- Strip synthetic tool call/return pairs from history before sending to the LLM
- Replace synthetic pairs with plain assistant messages in persisted history

### 5. Add a circuit-breaker for consecutive empty responses

**Severity:** Opportunity
**Where:** `runtime.py` or `service.py`

If N consecutive turns produce empty responses, escalate:
- Switch to a different model
- Reduce system prompt size
- Log a diagnostic alert

This prevents the user from experiencing repeated silent failures.

---

## Architecture Note

This trace, combined with D1/D2 from the prior audit, reveals a pattern: **the `tool_choice: "required"` contract is unreliable with local Ollama models**. The runtime already has a good text-coercion safety net, but it only works when the model produces *some* output. The gap is the zero-output case.

The recommended fix (retry with downgraded tool_choice) is minimal, targeted, and preserves the existing architecture. It does not require changing the tool rule system or the cognitive loop design — it simply adds a fallback at the LLM adapter boundary where the model contract is violated.

---

## Fix Details (2026-03-21)

### Approach: Multi-Agent Competitive Fix

Two AI agents were dispatched in parallel to independently implement the same fix, then their outputs were compared and the best elements merged.

| Agent | Model | Interface | Duration | Tokens | Tests Run |
|-------|-------|-----------|----------|--------|-----------|
| Claude Code | Opus 4.6 | Subagent (Agent tool) | ~6.5 min | ~49K | Full suite (792 passed) |
| OpenAI Codex CLI | GPT-5.4 (xhigh reasoning) | `codex exec` (CLI) | ~8 min | ~252K | 3 targeted files (28 passed) |

**Why this approach:** Both agents have different strengths. Running them in parallel on the same task surfaces blind spots that a single agent would miss. The comparison revealed two gaps that neither agent alone would have caught.

### What each agent contributed

**Claude (Opus 4.6):**
- Clean, single-pass implementation with no self-corrections
- Full test suite verification (792 tests)
- Efficient token usage (5x less than Codex)
- Missed: approval re-entry path, streaming content delta for `auto` retry

**Codex (GPT-5.4):**
- Extracted `_resolve_empty_forced_tool_stop_reason()` as a reusable helper
- Applied empty-response detection to **both** the main invoke loop and `resume_after_approval()`
- Emitted `content_delta` for the `auto` retry path in `stream()`, not just the `none` path
- Messy execution: 15+ incremental patches, many duplicate diffs, self-corrections needed

**Merged strategy:** Codex's architectural coverage (more call sites, extracted helper) with Claude's execution quality (clean code, full test run).

### Changes made

**C1 + C2 — Adapter-level retry with tool_choice downgrade**

File: `services/agent/adapters/openai_compatible.py`

- Extracted `_invoke_once()` from `invoke()` — single non-streaming LLM call
- Extracted `_stream_once()` from `stream()` — single streaming LLM call returning assembled result
- Added `_downgrade_tool_choice(request, mode)` helper — creates new `LLMRequest` with either `force_tool_call=False` (mode `"auto"`) or empty tools (mode `"none"`)
- `invoke()` now retries: `required` → `auto` → no tools, checking for empty response between each
- `stream()` follows the same retry chain; emits `content_delta` events for recovered text at both retry levels so the streaming frontend sees the output

**C2 — Runtime-level recovery**

File: `services/agent/runtime.py`

- Added `_resolve_empty_forced_tool_stop_reason()` helper at module level — returns `StopReason.EMPTY_RESPONSE` when `force_tool_call=True` but the result has no text and no tool calls, otherwise `None`
- Applied in main invoke loop (line ~361) and `resume_after_approval()` (line ~707)
- `_default_response()` returns user-friendly message for `EMPTY_RESPONSE`: *"I'm sorry, I wasn't able to generate a response. Could you try rephrasing or sending your message again?"*
- `resume_after_approval()` now uses `stop_reason` from the helper instead of hardcoded `StopReason.END_TURN`

**New enum value**

File: `services/agent/runtime_types.py`

- Added `EMPTY_RESPONSE = "empty_response"` to `StopReason`

### Verification

792 passed, 1 skipped (unchanged from baseline). No test files modified.

### Files changed

- `services/agent/adapters/openai_compatible.py` — adapter retry chain, `_invoke_once`, `_stream_once`, `_downgrade_tool_choice`
- `services/agent/runtime.py` — `_resolve_empty_forced_tool_stop_reason` helper, applied in invoke loop + approval re-entry, `_default_response` update
- `services/agent/runtime_types.py` — `StopReason.EMPTY_RESPONSE`
