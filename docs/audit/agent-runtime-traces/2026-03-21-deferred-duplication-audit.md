# Agent Runtime Trace Audit — Deferred Tool Call Duplication

**Date**: 2026-03-21
**Auditor**: Claude (Opus 4.6) — Systematic Debugging
**Scope**: Deferred tool call executes after model already self-corrected — duplicate + wrong args
**Provider**: Ollama (local)
**Model**: `vaultbox/qwen3.5-uncensored:35b`
**Related**: [2026-03-21 save-to-memory-lost audit](2026-03-21-save-to-memory-lost-audit.md) — the fix that introduced deferral

---

## Remediation Status

| Finding | Severity | Status | Fix Date | Implementer |
|---------|----------|--------|----------|-------------|
| C1 — Deferred tool call duplicates successful self-correction | Critical | Open | — | Deferred `core_memory_append` fires at step 5 despite model already calling it successfully at step 3 |
| C2 — Deferred tool call has wrong argument name (`block_label` vs `label`) | Critical | Open | — | Original blocked call used `block_label`; model self-corrected with `label`; deferred replays the wrong one |
| D1 — 5 steps consumed for single user message | Degraded | Open | — | Steps 0-4: rule violation, rule violation + deferral, inner_thought({}), core_memory_append + send_message, then deferred execution |
| D2 — Model calls inner_thought with empty args (recurring) | Degraded | Recurring | — | Same as prior audits — step 2 calls `inner_thought({})` |

---

## Trace: User correction triggers memory save + deferred duplication

### Turn Summary

- **Steps:** 5 LLM calls (steps 0-4) + 1 deferred execution (step 5)
- **Tools called:** `send_message` (rule violation x2), `inner_thought` (empty args), `core_memory_append` (success), `send_message` (terminal), `core_memory_append` (deferred — fails with wrong args)
- **Stop reason:** `terminal_tool`
- **Status:** Degraded — response delivered but deferred tool fires redundantly with wrong args

---

## Findings

### C1 — Deferred tool call duplicates successful self-correction

**Severity:** Critical

The deferral system (introduced in commit `fa40b77`) correctly queued a blocked `core_memory_append` call from step 1. But the model also self-corrected and successfully called `core_memory_append` at step 3 with the correct arguments. After `send_message` completed at step 4, the deferred queue executed the **stale, incorrect** copy at step 5.

Timeline:
1. **Step 0:** Model calls `send_message` directly — blocked by InitToolRule
2. **Step 1:** Model calls `send_message` again + `core_memory_append` — both blocked. `core_memory_append` **deferred** (uses `block_label` argument)
3. **Step 2:** Model calls `inner_thought({})` — fails (empty args)
4. **Step 3:** Model calls `core_memory_append(label=..., content=...)` — **succeeds** with correct args
5. **Step 4:** Model calls `send_message(...)` — terminal, turn ends
6. **Step 5 (deferred):** `core_memory_append(block_label=..., content=...)` — **executes with wrong arg name**, fails

The deferred execution is:
- **Redundant** — the model already accomplished the same intent at step 3
- **Incorrect** — uses `block_label` (the model's first attempt) instead of `label` (the correct parameter name)

**Root cause:** The deferral queue has no deduplication. It doesn't check whether the deferred tool was already successfully executed during the turn. The `tools_used` list tracks tool names but the deferred execution only checks `if dtc.name not in tools_used` for *adding* to the list, not for *skipping* execution.

**Impact:** Wasted execution, potential data corruption (if the args were valid, the memory append would duplicate), and confusing trace output. In this case, the wrong arg name caused a silent failure, which is a lucky accident.

### C2 — Deferred tool call preserves stale/wrong arguments

**Severity:** Critical

The deferred `core_memory_append` was captured at step 1 with the model's original (incorrect) arguments:
```json
{
  "block_label": "julio_profile",
  "content": "Location: Malaysia (UTC+8 timezone)"
}
```

The model self-corrected at step 3 with the right arguments:
```json
{
  "label": "julio_profile",
  "content": "Location: Malaysia (UTC+8 timezone)"
}
```

The deferral system replays the original blocked call verbatim — it doesn't know the model learned from the error and improved its arguments.

### D1 — 5 steps consumed for single user message

**Severity:** Degraded

A simple user correction ("im in malaysia remember?") consumed all `max_steps=4` loop iterations (steps 0-3) plus the terminal step 4. Two steps were wasted on rule violations, one on empty `inner_thought` args. Only steps 3-4 did useful work.

Step budget breakdown:
| Step | Action | Useful? |
|------|--------|---------|
| 0 | `send_message` blocked (InitToolRule) | No |
| 1 | `send_message` + `core_memory_append` blocked | No (but `core_memory_append` deferred) |
| 2 | `inner_thought({})` — empty args error | No |
| 3 | `core_memory_append` succeeds | Yes |
| 4 | `send_message` — terminal | Yes |
| 5 | Deferred `core_memory_append` — duplicate, wrong args | No |

Efficiency: 2/6 useful actions (33%).

### D2 — Model calls inner_thought with empty args (recurring)

**Severity:** Degraded

Same pattern as all prior audits. Step 2 calls `inner_thought` with `{}` instead of `{"thought": "..."}`. The argument validation catches it, but it wastes a step.

---

## Analysis: The Deduplication Gap

The deferred tool call system correctly preserves model intent when a tool is blocked by rule violations. But it has two gaps:

1. **No deduplication against successful execution.** If the model retries the blocked tool and succeeds during the turn, the deferred copy still fires. The fix should check `tools_used` (or a more specific success tracker) before executing deferred calls.

2. **Stale arguments.** The deferred call preserves the arguments from the moment of blocking. If the model's retry used different (correct) arguments, the deferred call replays the wrong version. Deduplication (gap 1) makes this moot — if we skip deferred calls that already succeeded, we never replay stale args.

---

## Recommendations

### 1. Deduplicate deferred tool calls against successful execution

**Severity:** Critical
**Where:** `runtime.py` post-loop deferred execution block (lines 520-558)

Before executing each deferred tool call, check if the same tool name was already successfully executed during the turn (present in `tools_used` list). If so, skip the deferred call with an info log.

```python
for dtc in deferred_tool_calls:
    if dtc.name in tools_used:
        logger.info(
            "Skipping deferred tool call %r — already executed "
            "successfully during this turn",
            dtc.name,
        )
        continue
    # ... existing execution code
```

### 2. Consider deduplication by tool name + semantic similarity

**Severity:** Opportunity
**Where:** Future enhancement

For cases where the same tool is called multiple times with different arguments (e.g., two separate `core_memory_append` calls for different blocks), simple name-based deduplication might incorrectly skip a legitimate deferred call. For now, name-based is sufficient since the model is retrying the *same* intent. If this becomes a problem, consider comparing argument keys or content similarity.

---
