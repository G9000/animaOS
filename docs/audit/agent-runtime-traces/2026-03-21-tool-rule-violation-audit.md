# Agent Runtime Trace Audit — Tool Rule Violation & Text-as-Tool-Call

**Date**: 2026-03-21
**Auditor**: Claude (Opus 4.6) — Systematic Debugging
**Scope**: Model emits tool calls as plain text, skips `inner_thought` InitToolRule, sends empty tool arguments
**Provider**: Ollama (local)
**Model**: `vaultbox/qwen3.5-uncensored:35b`
**Related**: [2026-03-20 audit](2026-03-20-agent-trace-audit.md) — findings C2 (empty arguments), D1/D2 (tool protocol non-compliance)

---

## Remediation Status

| Finding | Severity | Status | Fix Date | Implementer |
|---------|----------|--------|----------|-------------|
| C1 — Model emits `<function=inner_thought>` as plain text instead of structured tool call | Critical | **FIXED** | 2026-03-21 | Claude (Opus 4.6) + Codex (GPT-5.4) |
| C2 — `inner_thought` called with empty arguments (again) | Critical | Previously Fixed | 2026-03-20 | Argument validation catches it |
| D1 — Model calls `send_message` first, skipping `inner_thought` InitToolRule | Degraded | By Design | — | Rule enforcement rejects it; model self-corrects on retry |
| D2 — Parallel `inner_thought({}) + send_message({...})` on recovery step | Degraded | Mitigated | 2026-03-21 | C1 fix ensures inner_thought text is parsed before structured calls |
| D3 — All prior assistant messages contain synthetic tool history | Degraded | Open | — | Escalation of D2 from 2026-03-20 audit |
| O1 — 30s for step 0 due to text-mode inner_thought generation | Opportunity | Mitigated | 2026-03-21 | Text is now parsed as tool call instead of discarded |

---

## Trace: Conversation Turn (user: "why you keep saying your name?")

### Turn Summary

- **Steps:** 2 LLM calls
- **Tools called:** `inner_thought` (empty args, error), `send_message` (rule violation step 0, success step 1)
- **Total wall time:** 33,265ms (30,156 + 3,109)
- **Stop reason:** `terminal_tool`
- **Status:** Degraded — turn completed but with wasted step and persistent model non-compliance

---

## Findings

### C1 — Model emits `<function=inner_thought>` as plain text

**Severity:** Critical

Step 0 `assistantTextPreview`:
```
<function=inner_thought>
Good catch from Julio. Saying ANIMA every response might be part of the
establishment phase of this naming convention, but it's a bit e...
```

The model is using **Letta/MemGPT-style function-call syntax** (`<function=tool_name>`) as plain text output instead of producing a structured OpenAI-format tool call. This means:

1. The model *did* think — the reasoning content is in the text (354 chars)
2. But it was never processed as an `inner_thought` tool call
3. Instead, the model produced a *separate* structured `send_message` tool call (`call_9rtkicas`)
4. The text-coercion system (`_coerce_text_tool_calls`) didn't parse it because the `<function=...>` syntax doesn't match the existing regex patterns (`tool_name("content")` or `tool_name({"key": "value"})`)

**Root cause:** The model was likely fine-tuned on Letta/MemGPT function-calling format (`<function=name>\ncontent\n</function>`) rather than OpenAI's structured tool-call format. The system prompt and tool schemas expect structured calls, but the model falls back to its training format.

**Impact:** The inner thought content is lost — it exists in the assistant text but isn't captured by the `inner_thought` tool, so it doesn't enter the cognitive loop properly.

### C2 — `inner_thought` called with empty arguments (recurring)

**Severity:** Critical (previously fixed, recurring)

Step 1: model calls `inner_thought` with `arguments: {}` — missing the required `thought` parameter. This is the same issue as finding C2 from the 2026-03-20 audit. The argument validation fix correctly catches it:
```
"Tool inner_thought is missing required argument: thought. Provide a JSON object with all required fields."
```

But the model keeps doing it. The enhanced tool description ("You MUST provide a thought string argument") isn't preventing the behavior.

### D1 — Model calls `send_message` first, skipping InitToolRule

**Severity:** Degraded

Step 0: `allowedTools: ["inner_thought"]` but model called `send_message` directly.

The rule enforcement correctly blocked it:
```
"Tool rule violation: Tool 'send_message' is not allowed yet. The first tool call must be one of: inner_thought."
```

The model self-corrected on step 1 by calling both tools. This is the rule system working as designed — but it costs an extra LLM call (3.1s).

### D2 — Parallel `inner_thought({}) + send_message({...})` on recovery

**Severity:** Degraded

Step 1: after seeing the rule violation, the model calls both `inner_thought` and `send_message` in the same step — but with empty `inner_thought` arguments. Pattern:

1. Model learns "I need to call inner_thought first"
2. Model calls `inner_thought({})` + `send_message({message: "..."})` in parallel
3. `inner_thought` fails (empty args), `send_message` succeeds
4. Turn completes because `send_message` is terminal

The model is technically "obeying" the rule (calling inner_thought) but not meaningfully — it's a hollow compliance.

### D3 — Synthetic tool history contamination (ongoing)

**Severity:** Degraded

Every prior assistant message in the conversation uses synthetic tool call IDs:
- `synthetic-send_message-2-0`
- `synthetic-send_message-0-0` (appears twice)

The conversation history shows a pattern the model never actually produced. Prior assistant messages contain `<function=inner_thought>` text that was coerced into synthetic `send_message` calls. The model sees tool return messages for tool calls it didn't make in the format it doesn't use.

This likely confuses the model about what the expected tool-calling protocol looks like, contributing to the persistent non-compliance.

### O1 — 30s wasted on step 0

**Severity:** Opportunity

Step 0 took 30,156ms. The model spent that time:
- Generating 354 chars of `<function=inner_thought>` text (useful reasoning, wrong format)
- Generating a `send_message` structured tool call (correct format, wrong sequence)

The useful reasoning exists but in the wrong form. If the `<function=...>` text format were parsed, this step would have succeeded.

---

## Cross-Trace Pattern: Model Tool-Calling Failure Modes

Across all audits, this model exhibits three distinct failure modes:

| Mode | Description | Frequency | Recovery |
|------|-------------|-----------|----------|
| **A — Empty response** | Returns nothing under `tool_choice: "required"` | Occasional | Fixed: adapter retry chain |
| **B — Text-as-tool-call** | Emits `<function=name>` syntax as plain text | Frequent | Partial: text coercion catches `send_message` but not `inner_thought` |
| **C — Empty tool args** | Calls tool with `{}` instead of required params | Frequent | Fixed: argument validation returns error |

Mode B is the dominant failure and is **not addressed by the current fixes**. The text coercion system handles `tool_name("content")` and `tool_name({"key": "value"})` patterns but NOT `<function=name>\ncontent\n</function>` patterns.

---

## Recommendations

### 1. Add `<function=name>` text coercion pattern

**Severity:** Critical
**Where:** `runtime.py:_parse_text_tool_calls()` or `_TEXT_TOOL_CALL_RE`

Add a regex pattern to recognize Letta/MemGPT-style function calls:
```
<function=tool_name>
content here
</function>
```

Parse the content between tags and route to the appropriate tool with the inferred argument name (e.g., `thought` for `inner_thought`, `message` for `send_message`).

This single fix would convert the most common failure mode (B) into successful tool calls.

### 2. Investigate synthetic tool history contamination

**Severity:** Degraded
**Where:** Message persistence layer

The model sees a conversation history full of tool patterns it never produced. Consider:
- Replacing synthetic tool call/return pairs with plain assistant messages in persisted history
- Or marking them with metadata so the model doesn't try to replicate the pattern
- Or stripping tool metadata from history messages before sending to the LLM

### 3. Consider model-specific tool-call format adaptation

**Severity:** Opportunity
**Where:** Adapter layer or system prompt

If this model consistently uses `<function=name>` syntax, the system prompt could be adapted to match the model's training format rather than fighting against it. Alternatively, a model-specific adapter could translate between formats.

---

## Fix Details (2026-03-21)

### Approach: Multi-Agent Competitive Fix (Round 2)

Same methodology as the empty-response fix earlier this session: two agents dispatched in parallel, outputs compared, best elements merged.

| Agent | Model | Duration | Tokens | Tests Run |
|-------|-------|----------|--------|-----------|
| Claude Code | Opus 4.6 | ~1.8 min | ~21K | None (subagent) |
| OpenAI Codex CLI | GPT-5.4 | ~5 min | ~100K | 23 targeted + inline parser probes |

### What each agent contributed

**Claude (Opus 4.6):**
- Clean implementation, correct overall design
- Single regex `<function=name>\s*(?P<content>[\s\S]*?)(?:</function>|$)` for capture
- Weakness: lazy `[\s\S]*?` misparses consecutive blocks when the first omits `</function>`

**Codex (GPT-5.4):**
- Opening-tag-only regex + manual content slicing between consecutive match positions
- Correctly handles missing `</function>` between consecutive blocks
- Ran inline parser verification probes (closed tags, missing tags, multiline, JSON inside, multiple blocks)
- More tokens but more robust edge-case handling

**Winner: Codex's content extraction strategy** — handles the "missing closing tag between consecutive blocks" edge case that Claude's regex misparses.

### Changes made

**File:** `services/agent/runtime.py`

1. **New regex `_TEXT_TOOL_CALL_FUNCTION_TAG_RE`** — matches `<function=tool_name>` opening tags only (content extracted by slicing between consecutive matches)

2. **New function `_parse_function_tag_tool_calls()`** — extracts content between `<function=...>` opening tags, trims at `</function>` if present, tries JSON parse first, falls back to `_infer_first_arg_name()` mapping. Handles:
   - Multiple `<function=...>` blocks in one response
   - Multi-line content
   - Missing `</function>` closing tag (consumes to next block or end-of-string)
   - JSON content inside tags
   - Empty content blocks (skipped)

3. **Updated `_parse_text_tool_calls()`** — after existing patterns (string-arg, JSON-arg, line-by-line) find nothing, falls back to `_parse_function_tag_tool_calls()`. Existing patterns keep priority.

### Verification

792 passed, 1 skipped (unchanged from baseline). No test files modified.
