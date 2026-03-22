# Agent Runtime Trace Audit — Rules Solver Not Updated on Coerced Tool Calls

**Date**: 2026-03-21
**Auditor**: Claude (Opus 4.6) — Memory Tracer & Debugger
**Scope**: `rules_solver.update_state()` never called after coerced tool calls; `<parameter=...>` tags not parsed; model non-compliance loop
**Provider**: Ollama (local)
**Model**: `vaultbox/qwen3.5-uncensored:35b`
**Related**: [2026-03-21 tool-rule-violation audit](2026-03-21-tool-rule-violation-audit.md) — findings C1 (text-as-tool-call), D1 (InitToolRule skip)

---

## Remediation Status

| Finding | Severity | Status | Fix Date | Implementer |
|---------|----------|--------|----------|-------------|
| C1 — `rules_solver.update_state()` never called on coerced tool-call path | Critical | **FIXED** | 2026-03-21 | Claude (Opus 4.6) |
| C2 — `<parameter=name>` tags inside `<function=...>` blocks not parsed | Critical | **FIXED** | 2026-03-21 | Claude (Opus 4.6) |
| C3 — Model ignores `forceToolCall` + `allowedTools` for 4 consecutive steps | Critical | By Design | — | Rule enforcement rejects it; model self-corrects via text coercion |
| D1 — No `usage` event emitted from Ollama adapter | Degraded | Open | — | — |
| D2 — `max_steps` too generous for repeated identical failures | Degraded | Open | — | — |
| O1 — Deferred tool calls dropped on `max_steps` stop reason | Opportunity | Open | — | — |
| O2 — No circuit breaker for repeated identical tool violations | Opportunity | Open | — | — |

---

## Trace: Conversation Turn (user: "What do you think about dlss")

### Turn Summary

- **Steps:** 6 LLM calls (step 0 through step 5)
- **Tools called:** `send_message` (blocked x1), `save_to_memory` (blocked x3, malformed x1), `inner_thought` (coerced success x1)
- **Total wall time:** 53,360ms (12,063 + 9,687 + 8,844 + 10,750 + 8,922 + 3,094)
- **Stop reason:** `max_steps`
- **Status:** **FAILED** — user received no answer; no memory was saved

### Timing Breakdown

| Step | TTFT (ms) | LLM Duration (ms) | Tool Called | Result |
|------|-----------|-------------------|-------------|--------|
| 0 | 10,360 | 12,063 | `send_message` | Rule violation — not `inner_thought` |
| 1 | 2,657 | 9,687 | `save_to_memory` | Rule violation — not `inner_thought` |
| 2 | 2,656 | 8,844 | `save_to_memory` | Rule violation — identical repeat |
| 3 | 2,828 | 10,750 | `save_to_memory` | Rule violation — identical repeat |
| 4 | 2,235 | 8,922 | `inner_thought` (coerced) | Success — but solver not updated |
| 5 | 2,562 | 3,094 | `save_to_memory` (coerced) | Missing required arg `key` |

- `stepDurationMs == llmDurationMs` on every step — tool execution is instant (synchronous rule violations). All time is LLM inference.
- Step 0 TTFT of 10.3s suggests Ollama model loading or KV cache miss for the 18k system prompt.

---

## Findings

### C1 — `rules_solver.update_state()` never called on coerced tool-call path

**Severity:** Critical

**The root cause of this entire failure.**

At step 4, the model outputs `<function=inner_thought>` as plain text. The `_coerce_text_tool_calls` pipeline correctly parses this and executes it — `inner_thought` succeeds with `"Thought recorded. Proceed with your next action or send_message."`

However, in `runtime.py` (around line 314–349), the coerced-tool-call success branch:
1. Appends tool results to messages
2. Records `tools_used`
3. Checks for terminal tools

But **never calls `rules_solver.update_state(tc.name, tr.output)`**.

This means:
- `rules_solver._call_history` remains empty after step 4
- `get_allowed_tools()` returns `["inner_thought"]` again at step 5
- The model cannot call any other tool — permanently stuck

**Impact:** Any model that emits tool calls as text (common for smaller/local models fine-tuned on Letta/MemGPT format) will get permanently stuck at the InitToolRule gate, even when the required init tool succeeds.

**Fix:** Add `rules_solver.update_state(tc.name, tr.output)` inside the coerced-tool-call success branch, after the tool result message is appended.

```python
# runtime.py, coerced tool-call success branch (~line 325)
messages.append(make_tool_message(...))
rules_solver.update_state(tc.name, tr.output)  # <-- ADD THIS
```

### C2 — `<parameter=name>` tags inside `<function=...>` blocks not parsed

**Severity:** Critical

At step 5, the model outputs:
```xml
<function=save_to_memory>
<parameter=category>
goal
</parameter>
<parameter=importance>
5
</parameter>
<parameter=tags>
project,coding,memory-module
</parameter>
<parameter=text>
Finish memory module for project by end of week
</parameter>
```

`_parse_function_tag_tool_calls` cannot parse this as JSON, so it falls back to `_infer_first_arg_name("save_to_memory")` which returns `"content"`, stuffing the entire XML-parameter block into:
```json
{"content": "<parameter=category>\ngoal\n</parameter>..."}
```

The tool's required parameter `key` is missing → error.

**Fix:** In `_parse_function_tag_tool_calls`, before the JSON parse attempt, add a parser for `<parameter=name>value</parameter>` tags. Extract each name/value pair into a dict:
```python
import re
param_re = re.compile(r'<parameter=(\w+)>\s*([\s\S]*?)\s*</parameter>')
params = dict(param_re.findall(content))
if params:
    return params  # {"category": "goal", "importance": "5", ...}
```

**Where:** `runtime.py`, function `_parse_function_tag_tool_calls` (~line 1116).

### C3 — Model ignores `forceToolCall` + `allowedTools` for 4 consecutive steps

**Severity:** Critical

Steps 0-3: `allowedTools: ["inner_thought"]` with `forceToolCall: true`, but the model produces native tool calls to `send_message` (step 0) and `save_to_memory` (steps 1-3). All rejected.

This is a model compliance issue — `vaultbox/qwen3.5-uncensored:35b` does not reliably respect the OpenAI-compatible tool-calling contract.

**Note:** The model also conflates the user's two requests. The user asked "What do you think about dlss" but the model tries to `save_to_memory` (from the earlier "save the goal" request) instead of answering. The context history confused the model about which request to handle.

### D1 — No `usage` event emitted

**Severity:** Degraded

No token counts in the trace. The Ollama adapter likely does not extract `prompt_eval_count` / `eval_count` from the Ollama API response. Without this, operators cannot monitor cost or detect prompt bloat.

**Where:** Ollama adapter (likely `adapters/openai_compatible.py`).

### D2 — `max_steps` too generous for repeated identical failures

**Severity:** Degraded

Steps 1, 2, and 3 are **identical**: same tool (`save_to_memory`), same arguments, same error. The runtime burned 3 steps (29.3 seconds) repeating the same failure with no chance of a different outcome.

**Fix:** Detect consecutive identical violations and break early or inject a corrective system message.

### O1 — Deferred tool calls dropped on `max_steps`

**Severity:** Opportunity

`save_to_memory` was deferred at steps 1-3 but never executed because the turn ended with `max_steps`, not `terminal_tool`. The user's intent to save the goal was valid — it should execute on `max_steps` too (not just `terminal_tool`).

**Where:** `runtime.py`, deferred execution guard (~line 521).

### O2 — No circuit breaker for repeated identical violations

**Severity:** Opportunity

If the same tool is rejected N times with identical arguments, the runtime should:
1. Inject a stronger hint message (e.g., "You MUST call inner_thought before any other tool")
2. Or break the loop early with a warning
3. Or auto-execute the required init tool with a placeholder

---

## Cross-Trace Pattern: Rules Solver State Gap

This audit reveals a systemic gap: the rules solver is only updated on the **native tool-call path** (where the LLM produces structured tool calls). The **coerced tool-call path** (text parsed as tool calls) bypasses state updates entirely.

Any tool rule that depends on call history (`InitToolRule`, `SequentialToolRule`, etc.) will malfunction when the model uses text-format tool calls.

| Path | `rules_solver.update_state()` called? | Affected rules |
|------|---------------------------------------|----------------|
| Native tool call (structured) | Yes | All work correctly |
| Coerced tool call (text parsed) | **No** | `InitToolRule`, any sequential/ordering rule |

This is a **class of bug**, not a single instance. Every rule type that checks `_call_history` is affected.

---

## Estimated Waste

- **Wall time:** 53.4 seconds (zero useful output)
- **LLM calls:** 6 (all wasted)
- **Estimated tokens:** 40k-60k prompt tokens (18k system prompt x 6 calls with growing context)
- **User impact:** DLSS question unanswered, memory not saved, turn ended silently

---

## Fix Details (2026-03-21)

### Approach: Multi-Agent Competitive Fix

Three agents dispatched in parallel; two failed to launch, one delivered.

| Agent | Model | Duration | Tokens | Status |
|-------|-------|----------|--------|--------|
| Claude Code (Sonnet) | claude-sonnet-4-6 | — | — | Failed: model unavailable on Vertex |
| Claude Code (Opus) | Opus 4.6 | ~1.6 min | ~20K | **Complete** — diffs + 6 tests + edge case analysis |
| OpenAI Codex CLI | o4-mini | — | — | Failed: model not supported with ChatGPT account |

### Changes made

**File:** `services/agent/runtime.py`

1. **C1 fix — `rules_solver.update_state()` on coerced path** (line ~326)

   Added one line inside the `for tc, tr in coerced:` loop, after the tool result message is appended. This mirrors the native tool-call path at line 464.

   ```python
   # After make_tool_message(...) in the coerced tool-call branch:
   rules_solver.update_state(tc.name, tr.output)
   ```

2. **C2 fix — `_PARAMETER_TAG_RE` regex constant** (line ~1039)

   New compiled regex after `_TEXT_TOOL_CALL_FUNCTION_TAG_RE`:

   ```python
   _PARAMETER_TAG_RE = re.compile(
       r"<parameter=(\w+)>\s*([\s\S]*?)\s*</parameter>",
   )
   ```

3. **C2 fix — parameter tag parsing in `_parse_function_tag_tool_calls`** (line ~1128)

   After the JSON parse attempt fails, before falling back to `_infer_first_arg_name`, try extracting `<parameter=name>value</parameter>` pairs:

   ```python
   param_matches = _PARAMETER_TAG_RE.findall(content)
   if param_matches:
       results.append(_ParsedTextToolCall(
           name=name,
           arguments={k: v.strip() for k, v in param_matches},
       ))
       continue
   ```

   Priority order preserved: JSON > parameter tags > inferred first arg.

**File:** `tests/test_runtime_enhancements.py` — 6 new tests

| Test | Bug | Verifies |
|------|-----|----------|
| `test_coerced_tool_call_updates_rules_solver` | C1 | Coerced `inner_thought` unlocks `send_message` on next step |
| `test_coerced_tool_call_allows_second_step` | C1 | Exactly 2 steps needed (no max_steps loop) |
| `test_parse_function_tag_with_parameter_tags` | C2 | Multi-line `<parameter>` tags → proper dict |
| `test_parse_function_tag_with_parameter_tags_no_closing_function` | C2 | Missing `</function>` still parses |
| `test_parse_function_tag_json_still_preferred_over_parameter_tags` | C2 | JSON content takes priority |
| `test_parse_function_tag_plain_text_fallback_still_works` | C2 | Plain text → `_infer_first_arg_name` fallback preserved |

### Edge Cases Considered

- **Error tool results on coerced path:** `update_state` is called unconditionally (matches native path) — solver tracks that a tool was *called*, not that it *succeeded*.
- **Multiple coerced calls per step:** Loop iterates all pairs, each updates solver.
- **No double-update risk:** Coerced and native paths are mutually exclusive (`if not step_result.tool_calls:` guard).
- **Duplicate parameter names:** `dict()` keeps the last value — acceptable for model errors.
- **Empty parameter values:** `.strip()` produces empty string; tool executor validates downstream.

### Verification

798 passed, 1 skipped (up from 792 baseline). No existing tests modified.
