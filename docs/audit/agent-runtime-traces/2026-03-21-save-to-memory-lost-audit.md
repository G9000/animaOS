# Agent Runtime Trace Audit — Lost Memory Save After Rule Violation

**Date**: 2026-03-21
**Auditor**: Claude (Opus 4.6) — Systematic Debugging
**Scope**: `save_to_memory` tool call blocked by InitToolRule, never retried — memory permanently lost
**Provider**: Ollama (local)
**Model**: `vaultbox/qwen3.5-uncensored:35b`

---

## Remediation Status

| Finding | Severity | Status | Fix Date | Implementer |
|---------|----------|--------|----------|-------------|
| C1 — `save_to_memory` blocked and never retried — user fact lost | Critical | **FIXED** | 2026-03-21 | Claude (Opus 4.6) + Codex (GPT-5.4) |
| D1 — Two empty assistant messages in conversation history | Degraded | Open | — | Messages at indices 3-4 have 0 chars but toolCallCount: 1 |
| D2 — 22s TTFT with no captured reasoning | Degraded | Open | — | Model "thinks" for 22s but nothing enters inner_thought |
| D3 — Model skips inner_thought entirely | Degraded | Recurring | — | Same pattern as prior audits — model goes straight to action |
| O1 — Plain text response duplicated between steps | Opportunity | Open | — | Step 0 and step 1 produce nearly identical text |

---

## Trace: User correction ("im in malaysia remember?")

### Turn Summary

- **Steps:** 2 LLM calls
- **Tools called:** `save_to_memory` (rule violation), `send_message` (synthetic)
- **Total wall time:** 23,891ms (22,812 + 1,079)
- **Stop reason:** `terminal_tool`
- **Status:** Degraded — response delivered but memory save lost

---

## Findings

### C1 — `save_to_memory` blocked and never retried

**Severity:** Critical

Step 0: the model correctly identified that "Julio is in Malaysia (UTC+8 timezone)" should be saved and called `save_to_memory`:

```json
{
  "name": "save_to_memory",
  "arguments": {
    "category": "fact",
    "importance": "3",
    "tags": "location,timezone",
    "value": "Julio is in Malaysia (UTC+8 timezone)"
  }
}
```

This is **exactly the right behavior** — the model recognized a user correction that should be persisted. But it was blocked:

```
"Tool rule violation: Tool 'save_to_memory' is not allowed yet.
 The first tool call must be one of: inner_thought."
```

Step 1: after the violation, the model produced only plain text (coerced to `send_message`). The `save_to_memory` call was **never retried**. The user's timezone information is permanently lost from the agent's memory.

**Impact:** The user explicitly corrected the agent ("im in malaysia remember?") and the agent acknowledged it in its response but failed to persist the information. Next conversation, the agent won't know the user's timezone.

**Root cause:** The ToolRulesSolver enforces InitToolRule by rejecting the first tool call and returning an error. The model sees the error and self-corrects by producing a response — but it forgets to re-attempt the blocked tool call. The runtime doesn't replay or queue blocked tool calls for later execution.

### D1 — Two empty assistant messages in history

**Severity:** Degraded

Messages at indices 3 and 4:
```json
{ "role": "assistant", "chars": 0, "preview": "", "toolCallCount": 1 }
{ "role": "assistant", "chars": 0, "preview": "", "toolCallCount": 1 }
```

Two consecutive assistant messages with zero text but each claiming 1 tool call. These appear to be from the prior turn's `current_datetime` tool call sequence. The empty messages waste context tokens and may confuse the model about the conversation structure.

### D2 — 22s TTFT with no captured reasoning

**Severity:** Degraded

`ttftMs: 22000` — the model spent 22 seconds before producing its first token. This is likely internal reasoning about timezone conversion (UTC → UTC+8, calculating 7:16 PM → 3:16 AM). But none of this reasoning is captured because:
- `inner_thought` was never called
- No reasoning tokens were captured (`reasoningChars: 0`)
- The model's chain-of-thought is invisible

### D3 — Model skips inner_thought entirely (recurring)

**Severity:** Degraded

Same pattern across all traces: the model goes straight to action tools (`save_to_memory`, `send_message`) without calling `inner_thought` first. The InitToolRule catches this, but the recovery path only produces a response — it never goes back to call `inner_thought`.

### O1 — Duplicate response across steps

**Severity:** Opportunity

Step 0 text (114 chars):
> "Right. Malaysia - that's UTC+8. So it's 3:16 AM for you, not evening.\n\nYou were up late coding again, weren't you?"

Step 1 text (101 chars):
> "Right. Malaysia - that's UTC+8. So it's 3:16 AM for you.\n\nYou were up late coding again, weren't you?"

Nearly identical. The model regenerated the same response minus "not evening" — wasting 1s of LLM time.

---

## Analysis: The Lost Memory Problem

This trace reveals a **systemic issue** with how tool rule violations interact with memory-saving tools:

1. Model correctly wants to: think → save fact → respond
2. InitToolRule forces: inner_thought must come first
3. Model skips inner_thought, calls save_to_memory → **blocked**
4. Model self-corrects by just responding → **save_to_memory lost**
5. No mechanism exists to replay or queue the blocked save

This is worse than previous audit findings because the model is **doing the right thing** (saving important user information) but the rule system prevents it, and the recovery path doesn't preserve the intent.

---

## Recommendations

### 1. Queue blocked tool calls for post-response execution

**Severity:** Critical
**Where:** `runtime.py` invoke loop, rule violation handling

When a tool call is blocked by a rule violation but is a non-terminal, non-init tool (like `save_to_memory`), queue it for execution after the turn's `inner_thought` → `send_message` sequence completes. The blocked tool was well-formed and intentional — it should be deferred, not discarded.

### 2. Include blocked tool call context in the violation error message

**Severity:** Degraded
**Where:** `runtime.py` rule violation error path

The current error message tells the model what it did wrong but doesn't remind it to retry the blocked call. Consider appending: "After calling inner_thought, you may retry save_to_memory."

### 3. Clean up empty assistant messages from history

**Severity:** Degraded
**Where:** Message persistence / `build_conversation_messages()`

Filter out assistant messages with 0 chars and no meaningful tool calls from persisted history. These add noise to the context.

---

## Fix Details (2026-03-21)

### Approach: Multi-Agent Competitive Fix (Round 3)

Three agents dispatched; Sonnet 4.5 was unavailable on Vertex, leaving two.

| Agent | Model | Duration | Tokens | Tests Run |
|-------|-------|----------|--------|-----------|
| Claude Code | Opus 4.6 | ~4.9 min | ~43K | Full suite (792 passed) |
| OpenAI Codex CLI | GPT-5.4 | ~7 min | ~96K | 19 targeted + inline integration probes |
| Claude Code | Sonnet 4.5 | — | — | Unavailable on Vertex |

### What each agent contributed

**Claude (Opus 4.6):**
- Correct overall design — queue, defer, execute post-loop
- Used private `_terminal_tools` attribute (encapsulation violation)
- Only skipped deferred execution on `CANCELLED` (missed `MAX_STEPS`, `END_TURN`, etc.)
- Used stale `step_index` for event emission

**Codex (GPT-5.4):**
- Same design, four refinements:
  1. Used public `rules_solver.is_terminal()` instead of private `_terminal_tools`
  2. Only executes deferred calls on `TERMINAL_TOOL` (safer — skips on all abnormal exits)
  3. Used `len(step_traces)` for correct step index in event emission
  4. Added deferred tool names to `tools_used` list
- Ran inline integration reproductions verifying the deferred save executes and `max_steps=1` skips correctly

**Winner: Codex** on all four refinements. Claude's design was correct but less polished.

### Changes made

**File:** `services/agent/runtime.py`

1. **Import** — added `InitToolRule` from `rules` module

2. **Queue initialization** (before main loop) — `deferred_tool_calls: list[ToolCall]` + `init_tool_names` set derived from `InitToolRule` entries in tool rules

3. **Deferral logic** (rule violation handler) — when a tool call is blocked and is deferrable (not terminal via `is_terminal()`, not init, is registered), append to queue with info log. Violation error still returned to model.

4. **Post-loop execution** — after main loop, if `stop_reason == TERMINAL_TOOL` and deferred calls exist, execute each via `ToolExecutor.execute(is_terminal=False)`. Emit stream events for observability. On non-terminal stop reasons, log warning and skip. Individual failures caught and logged without failing the turn.

### Verification

792 passed, 1 skipped (unchanged from baseline). No test files modified.
