# Agent Runtime Trace Audit

**Date**: 2026-03-20
**Auditor**: Claude Memory Tracer & Debugger
**Scope**: Agent turn execution — message assembly, tool dispatch, tool schema compliance, timing, token efficiency
**Provider**: Ollama (local)
**Model**: `vaultbox/qwen3.5-uncensored:35b`

## Remediation Status

| Finding | Severity | Status | Fix Date | Implementer |
|---------|----------|--------|----------|-------------|
| C1 — Duplicate user messages in conversation history | Critical | **FIXED** | 2026-03-20 | Codex (gpt-5.4) |
| C2 — `inner_thought` called with empty arguments | Critical | **FIXED** | 2026-03-20 | Codex (gpt-5.4) |
| D1 — Model ignores `forceToolCall: true`, emits plain text | Degraded | Won't Fix | — | Model limitation |
| D2 — Synthetic `send_message` fallback masks model non-compliance | Degraded | By Design | — | Runtime safety net for D1 |
| D3 — Stale tool-rule violation messages persist in context | Degraded | **FIXED** | 2026-03-20 | Codex (gpt-5.4) |
| O1 — No token metrics from Ollama provider | Opportunity | **FIXED** | 2026-03-20 | Codex (gpt-5.4) |
| O2 — TTFT not instrumented for Ollama streaming | Opportunity | **FIXED** | 2026-03-20 | Codex (gpt-5.4) |
| O3 — No argument validation before tool dispatch | Opportunity | **FIXED** | 2026-03-20 | Codex (gpt-5.4) |
| O4 — 8s wasted on failed `inner_thought` step | Opportunity | **FIXED** | 2026-03-20 | Codex (gpt-5.4) |

### Fix Details (2026-03-20)

**C1 — Duplicate user messages**: Root cause was `_prepare_turn` in `service.py` calling `companion.append_to_window()` which mutated the shared `history` list in-place, then `build_conversation_messages()` in `runtime.py` appending the same user message again. Fix: removed premature `append_to_window`, added `_refresh_companion_history()` that reloads the companion window from persisted DB state after each turn completes. DB is now the single source of truth.

**C2 / O3 / O4 — Empty tool arguments & validation**: Added `_validate_tool_arguments()` in `executor.py` that checks required args against the tool's JSON schema (with `inspect.signature` fallback) *before* invoking the function. Returns a friendly error like `"Tool inner_thought is missing required argument: thought"` instead of a Python traceback. Also added `"You MUST provide a thought string argument."` to `inner_thought`'s tool description.

**Verification (Round 1)**: 778 passed, 1 skipped (up from 746). 4 new regression tests added. Graded **A-** by Claude (Opus 4.6).

**Files changed (Round 1)**:
- `services/agent/service.py` — removed premature `append_to_window`, added `_refresh_companion_history()`
- `services/agent/executor.py` — added `_validate_tool_arguments()`, `_get_required_tool_arguments()`
- `services/agent/tools.py` — enhanced `inner_thought` description
- `services/agent/rules.py` — hardened warning logger level
- `tests/test_agent_service.py` — regression test for duplicate message fix
- `tests/test_agent_executor.py` — regression test for argument validation
- `tests/test_inner_thought.py` — test for description requirement
- `tests/test_tool_rules.py` — test for logger-level edge case

### Fix Details (2026-03-20, Round 2)

**D3 — Stale tool-rule violations**: Added `_is_stale_tool_rule_violation_message()` filter in `messages.py`. `build_conversation_messages()` now skips `ToolMessage` entries from history whose content starts with `"Tool rule violation:"`. These enforcement messages served their purpose in the turn they occurred and no longer waste context tokens in future turns.

**O1 — Ollama token metrics**: `openai_compatible_client.py` now extracts `prompt_eval_count` and `eval_count` from the Ollama streaming response's final chunk (`done=true`) and normalizes them into `prompt_tokens` / `completion_tokens` / `total_tokens` in the usage metadata. Token efficiency analysis is now possible for Ollama traces.

**O2 — TTFT instrumentation**: `openai_compatible.py` adapter now timestamps the first raw streamed content chunk (before reasoning-tag stripping) and carries `ttft_ms` via `StepExecutionResult`. `runtime.py` uses this value to populate `StepTiming.ttft_ms` even when the first visible text arrives later (e.g., hidden `<think>` blocks). `ttftMs` is now emitted in trace timing events.

**D1 — Won't Fix**: The 35B local model occasionally ignores `tool_choice: "required"`. The Ollama adapter correctly passes the parameter. This is a model compliance issue, not a code bug.

**D2 — By Design**: The synthetic `send_message` fallback is the intentional safety net that catches D1. Removing it would break turns when the model emits plain text despite forced tool mode.

**Verification (Round 2)**: 781 passed, 1 skipped. 3 new regression tests added. Graded **A** by Claude (Opus 4.6).

**Files changed (Round 2)**:
- `services/agent/messages.py` — added `_is_stale_tool_rule_violation_message()` filter
- `services/agent/openai_compatible_client.py` — extract `prompt_eval_count`/`eval_count` from Ollama final chunk
- `services/agent/adapters/openai_compatible.py` — TTFT timestamping on first content chunk
- `services/agent/runtime.py` — propagate adapter-reported `ttft_ms` to `StepTiming`
- `services/agent/runtime_types.py` — added `ttft_ms` field to `StepExecutionResult`
- `tests/test_agent_messages.py` — regression test for tool-rule violation filtering
- `tests/test_agent_openai_compatible_client.py` — regression test for Ollama token extraction
- `tests/test_step_progression.py` — regression test for TTFT with hidden reasoning chunks

---

## Trace 1: Casual Conversation (Returning User)

### Context
- 3rd message in an ongoing conversation
- User says "Quite good. Coding whole day" after prior greeting exchange
- System prompt: 17,491 chars (full memory blocks loaded)

### Turn Summary
- **Steps:** 1 LLM call
- **Tools called:** `inner_thought`, `send_message`
- **Total tokens:** not reported (Ollama)
- **Total wall time:** 10,015ms
- **Stop reason:** `terminal_tool`
- **Status:** success

### Verdict
Turn executed correctly — model obeyed `forceToolCall`, reflected via `inner_thought`, and delivered a natural reply via `send_message`. One data quality issue found.

### Findings

#### Memory & Context
- **System prompt: 17,491 chars** — stable, no truncation warnings. This is the dominant context cost.
- **Message count: 11** — includes system prompt, 2 prior turns with tool call/return pairs, and current user message.
- **C1: Duplicate user message** — `"Quite good. Coding whole da"` (27 chars) appears at message indices 9 AND 10. The user input was appended twice before dispatching to the LLM. This is a client-side message assembly bug.
- **D3: Stale tool-rule violation** — A rejected `current_datetime` call from a prior turn (115 chars) persists in history. The model self-corrected, but the error message wastes context space.

#### Tool Execution
- **`inner_thought`** — called correctly with a well-formed `thought` argument. Content is contextually appropriate (reflects on user mood, plans warm response). No errors.
- **`send_message`** — called as second tool in same step. Natural conversational reply. No errors.
- Both tools emitted in a single parallel batch (`toolCallCount: 2`). This is efficient but means the inner thought doesn't inform the response — they're generated simultaneously.

#### Token Efficiency
- **Token counts not reported** — Ollama omits `prompt_eval_count` / `eval_count` from the trace. Monitoring gap (O1).
- **Estimated context:** ~18,000 chars (~5,000-6,000 tokens input), ~523 chars (~150 tokens output).
- **No reasoning tokens** — `reasoningChars: 0`. Model relies on `inner_thought` tool for reflection. Acceptable.

#### Timing
- **10,015ms total** — entirely LLM duration. No tool execution overhead.
- **TTFT: null** — not instrumented (O2).
- 10s for a simple conversational reply is acceptable for 35B local inference but on the slower side.

---

## Trace 2: First Message (New User Greeting)

### Context
- First message in a new conversation
- User says "Hello I'm Julio"
- System prompt: 12,773 chars (fewer memory blocks — fresh conversation)

### Turn Summary
- **Steps:** 2 LLM calls
- **Tools called:** `inner_thought` (failed), `send_message` (synthetic)
- **Total tokens:** not reported (Ollama)
- **Total wall time:** 10,672ms (8,094 + 2,578)
- **Stop reason:** `terminal_tool`
- **Status:** degraded

### Verdict
Turn completed but with two defects: the model called `inner_thought` with empty arguments (tool error), then bypassed tools entirely by emitting plain text — which the runtime wrapped in a synthetic `send_message`.

### Findings

#### Memory & Context
- **System prompt: 12,773 chars** — ~4,700 chars smaller than Trace 1. Expected for a fresh conversation with no episodic/semantic/working memory loaded.
- **C1: Duplicate user message (again)** — `"Hello I'm Julio"` (15 chars) at indices 1 AND 2. Same bug as Trace 1. Confirms this is a systematic issue, not a one-off.
- **Message count grows 3 → 5** between steps — the failed `inner_thought` call + error return were appended correctly.

#### Tool Execution
- **C2: `inner_thought` called with `{}` (empty arguments)** — The model failed to provide the required `thought` parameter. Tool errored with: `"inner_thought() missing 1 required positional argument: 'thought'"`. This is the primary failure — the model didn't generate valid tool arguments.
- **D1: Model ignored `forceToolCall: true` on step 1** — After seeing the error, the model responded with plain text (`assistantTextChars: 63`) instead of calling any tool. `forceToolCall` was set to `true` with 17 tools available, but the model produced no tool calls (`toolCallCount: 0`).
- **D2: Synthetic `send_message` fallback** — The runtime detected plain text output and wrapped it in a synthetic tool call (`callId: "synthetic-send_message-1-0"`). This saved the turn from failing entirely, but masks the model's non-compliance with the tool-use protocol.

#### Token Efficiency
- **O4: 8,094ms wasted on step 0** — The model spent 8 seconds generating... empty arguments. This entire step produced no useful output.
- Step 1 was efficient: 2,578ms total, 63 chars of good output.

#### Timing
- **Step 0:** 8,094ms — all LLM time, zero useful output. Pure waste.
- **Step 1:** 2,578ms total, TTFT 2,453ms — prompt processing dominated. Generation was fast (~125ms for 63 chars).
- **Combined:** 10.6s for a simple greeting. The failed step added 76% overhead.

---

## Cross-Trace Analysis

### Systematic Issues

| Issue | Trace 1 | Trace 2 | Pattern |
|-------|---------|---------|---------|
| Duplicate user message | Yes (indices 9-10) | Yes (indices 1-2) | Every trace. Client-side message assembly bug. |
| Token metrics missing | Yes | Yes | Ollama provider never reports tokens. |
| TTFT null | Yes | Yes (step 0 only) | Inconsistent — step 1 captured TTFT but step 0 didn't. |
| Empty tool arguments | No | Yes | Model-dependent. May correlate with conversation length or prompt complexity. |
| `forceToolCall` ignored | No | Yes | Model non-compliance after error recovery. |

### Model Behavior Observations

1. **Error recovery is weak** — When `inner_thought` failed in Trace 2, the model abandoned the tool-use protocol entirely and fell back to plain text. It did not retry `inner_thought` with correct arguments.
2. **Parallel tool calls** — In Trace 1, the model emitted `inner_thought` + `send_message` simultaneously. This means the "thought" doesn't actually inform the response. Consider whether this is acceptable or whether sequential execution (thought → response) is required.
3. **Response quality is good despite issues** — Both final responses were natural and contextually appropriate. The model's conversational ability is solid; the issues are in tool-use compliance.

---

## Recommendations (ranked by impact)

### 1. Fix duplicate user message assembly
**Severity:** Critical
**Where:** Message assembly logic — likely in the chat route handler or `services/agent/runtime.py` where user messages are pushed to the conversation array before the LLM call.
**Fix:** Add a guard that prevents appending the same user message consecutively. Check for identical content + role at the tail of the message list before appending.

### 2. Add argument validation before tool dispatch
**Severity:** Critical
**Where:** `services/agent/executor.py`
**Fix:** Before invoking a tool function, validate that all required arguments are present per the tool's JSON schema. Return a structured error like `"inner_thought requires argument 'thought' (string)"` instead of a Python traceback. This gives the model better signal for self-correction.

### 3. Harden `inner_thought` tool schema
**Severity:** Critical
**Where:** Tool definition file (wherever tool schemas are registered for the LLM)
**Fix:** Make the `thought` parameter more prominent — add an example value in the schema description, ensure it appears as `required: true` in the JSON schema. Consider adding a schema-level `default` or making the tool description explicitly state: "You MUST provide a 'thought' string argument."

### 4. Investigate `forceToolCall` enforcement with Ollama
**Severity:** Degraded
**Where:** Ollama provider adapter
**Fix:** Verify that `forceToolCall: true` is translated to the appropriate Ollama API parameter (e.g., `tool_choice: "required"`). If Ollama doesn't support forced tool calling, document this as a known limitation and ensure the synthetic `send_message` fallback is robust.

### 5. Capture Ollama token metrics
**Severity:** Opportunity
**Where:** Ollama provider adapter (streaming response handler)
**Fix:** Extract `prompt_eval_count` and `eval_count` from the Ollama `/api/chat` response and populate `promptTokens` / `completionTokens` in the trace events. This enables all token-efficiency analysis.

### 6. Instrument TTFT consistently
**Severity:** Opportunity
**Where:** Ollama streaming adapter
**Fix:** Record the timestamp of the first streamed chunk relative to request start. Currently captured inconsistently (null in some steps, present in others).

### 7. Prune stale tool-error messages from context
**Severity:** Opportunity
**Where:** `services/agent/runtime.py` or `services/agent/compaction.py`
**Fix:** After a turn completes successfully, collapse or remove tool-rule-violation messages from prior turns. They served their purpose and now waste tokens (~115 chars per violation).

### 8. Consider sequential inner_thought → send_message
**Severity:** Opportunity
**Where:** Step configuration / tool sequencing logic
**Fix:** If the inner thought is meant to genuinely inform the response, enforce a two-step pipeline: step 0 allows only `inner_thought`, step 1 allows `send_message` (with the thought in context). The current setup allows parallel emission, which defeats the purpose of "thinking before speaking."
