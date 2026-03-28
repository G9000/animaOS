---
title: "PRD: F9 - Episode Extraction Upgrade (Dual-Time, Entity-Grounded, Concise)"
description: Improve episode generation quality with temporal anchoring, entity-first narration, and conciseness rules
category: prd
version: "1.0"
---

# PRD: F9 - Episode Extraction Upgrade

**Version**: 1.0
**Date**: 2026-03-28
**Status**: Not Started (0%) — episode system exists but none of the F9 upgrades applied
**Roadmap Phase**: 10.9
**Priority**: P1 - High
**Depends on**: None
**Blocked by**: Nothing
**Blocks**: Improves F6 (batch segmentation), eval accuracy
**Inspired by**: Agent memory research (arXiv:2601.02163) — episode generation prompts with dual-time format and entity grounding

---

## 1. Overview

Upgrade the episode generation prompt and post-processing to produce higher-quality episodes that are easier to retrieve, more temporally precise, and less bloated. Three specific improvements drawn from SOTA agent memory research:

1. **Dual-time format** — Store both relative and absolute time references
2. **Entity-grounded narration** — Use names, not pronouns, for retrieval precision
3. **Conciseness rules** — Explicit constraints to prevent episode bloat

These are prompt-level and post-processing changes. No new tables, no new services, no architectural changes.

---

## 2. Problem Statement

### Current Episode Format

AnimaOS generates episodes during reflection via `episodes.py` → `maybe_generate_episode()`. The current prompt produces episodes like:

> The user discussed their work situation. They mentioned feeling stressed about a deadline. The assistant suggested breaking the task into smaller pieces. They seemed to appreciate the advice and mentioned they'd try it tomorrow.

Problems:
- **"They" everywhere** — Pronoun-heavy text is hard to retrieve. A search for "Alex's deadline" won't match "they mentioned feeling stressed about a deadline."
- **No temporal anchoring** — "tomorrow" is meaningless without knowing when the conversation happened. Six months later, "tomorrow" is useless.
- **Verbose** — Filler phrases ("The user discussed," "The assistant suggested") waste context budget without adding information.

### What Prior Art Does Better

Research implementations (arXiv:2601.02163) enforce:
- `"last week (May 7, 2023)"` — Dual format preserving both relative and absolute time, supporting both types of queries
- `"Use specific names consistently rather than pronouns to avoid ambiguity in retrieval"` — Entity-first
- `"Remove redundant expressions and verbose descriptions while preserving all facts"` — Conciseness as a hard rule

---

## 3. Design

### 3.1 Dual-Time Format

**Before:**
> They mentioned their presentation is tomorrow.

**After:**
> Alex mentioned their presentation is tomorrow (April 3, 2026).

The extraction prompt receives the conversation start timestamp. All relative time references must include the resolved absolute date in parentheses. This supports:
- Absolute queries: "What happened in April 2026?" → matches `(April 3, 2026)`
- Relative queries: "What happened recently?" → recency scoring still works via episode `created_at`
- Temporal reasoning: "When was Alex's presentation?" → matches both "tomorrow" and "April 3, 2026"

### 3.2 Entity-Grounded Narration

**Before:**
> The user talked about their partner's job situation. They said they were worried about them.

**After:**
> Alex discussed their partner Sam's job situation. Alex expressed worry about Sam's workload and upcoming performance review.

Rules for the extraction prompt:
- Use the user's name (from agent profile or conversation) instead of "the user"
- Use mentioned people's names instead of pronouns when first referenced
- Pronouns are acceptable for immediate back-references within the same sentence
- Include specific details (job title, project name, location) when mentioned

### 3.3 Conciseness Rules

Add explicit constraints to the episode generation prompt:

1. No filler phrases: remove "The user discussed," "The assistant mentioned," "They talked about"
2. Lead with facts, not framing: "Alex's quarterly review is next week" not "The conversation covered Alex's upcoming quarterly review"
3. Maximum episode length: 200 words for single-topic episodes, 400 for multi-topic
4. One sentence per distinct fact — no run-on paragraphs
5. Emotional arc in one sentence, not a paragraph: "Alex started frustrated but relaxed after finding a solution" not three sentences describing the shift

### 3.4 Updated Prompt Template

The episode generation prompt in `services/agent/episodes.py` should be updated. Key additions to the existing prompt:

```
IMPORTANT TIME HANDLING:
- Use the provided conversation timestamp as the anchor for all time references
- When the conversation mentions relative times (e.g., "tomorrow", "last week"),
  preserve both the original expression AND the resolved absolute date
- Format: "relative time (absolute date)" — e.g., "tomorrow (April 3, 2026)"

ENTITY RULES:
- Use {user_name} instead of "the user" or "they"
- Use mentioned people's names on first reference, not pronouns
- Include specific details (names, places, dates, project names) when mentioned

CONCISENESS:
- No filler phrases ("The user discussed," "They talked about")
- Lead with facts, not framing
- One sentence per distinct fact
- Emotional arc in one sentence
- Maximum 200 words for single-topic episodes
```

### 3.5 Post-Processing Validation

After LLM generation, apply a lightweight validation pass:

1. **Pronoun density check**: If the episode contains >5 instances of "they/them/their" without a named entity, flag for regeneration
2. **Length check**: If >400 words, truncate or flag
3. **Time anchor check**: If the episode mentions relative time without absolute dates, log a warning (don't block, just track quality)

These are soft checks for monitoring, not hard blocks.

---

## 4. Implementation Plan

| Step | File | Change |
|------|------|--------|
| 1 | `services/agent/episodes.py` | Update episode generation prompt with dual-time, entity, conciseness rules |
| 2 | `services/agent/episodes.py` | Add `user_name` to template context (from agent profile) |
| 3 | `services/agent/episodes.py` | Add conversation timestamp to template context for time resolution |
| 4 | `services/agent/episodes.py` | Add post-generation quality checks (pronoun density, length) |
| 5 | `services/agent/templates/prompts/` | Update episode prompt template if using Jinja2 |

**Estimated effort**: Small. Prompt changes + minor post-processing. No schema changes.

---

## 5. Success Criteria

- [ ] Generated episodes use the user's name instead of "the user" / "they"
- [ ] Relative time references include resolved absolute dates
- [ ] Average episode length decreases by 20%+ without information loss
- [ ] Retrieval precision for name-based queries improves (measurable via eval harness)
- [ ] Post-generation quality checks catch >80% of pronoun-heavy episodes

---

## 6. Eval Impact

This change directly impacts LoCoMo and LongMemEval scores:
- **Temporal reasoning questions** (LoCoMo cat 2, LongMemEval temporal-reasoning) benefit from dual-time format
- **Single-hop factual questions** (LoCoMo cat 1) benefit from entity grounding (names searchable)
- **Multi-hop questions** (LoCoMo cat 3) benefit from conciseness (more episodes fit in context)

Run `apps/server/eval/run_locomo.py` before and after this change to measure impact.

---

## 7. Implementation Audit (2026-03-28)

### Summary: NOT STARTED (0%)

The episode system works end-to-end (generation, batching via F6, memory blocks), but none of the three F9 prompt upgrades have been applied. The episode prompt is a 17-line generic template with no time handling, entity rules, or conciseness constraints.

### Current Prompt vs. PRD Specification

| PRD requirement | Current prompt (`episode_generation.md.j2`) | Status |
|-----------------|---------------------------------------------|--------|
| Dual-time format: `"tomorrow (April 3, 2026)"` | No time handling at all. No conversation timestamp in template context. | NOT DONE |
| Entity grounding: use `{user_name}` instead of "the user" | Template receives `{{ agent_name }}` (the AI's name) but NOT the user's name. Says "generate a concise episode summary" with no entity rules. | NOT DONE |
| Conciseness rules: no filler, lead with facts, word limits | Single line: "Be concise but capture the essence." No word limits, no "no filler phrases" rule, no "one sentence per fact" constraint. | NOT DONE |
| Post-generation quality checks (pronoun density, length) | `_build_episode_from_parsed()` does basic field validation (summary exists, significance 1-5, topics list). No pronoun density check, no length check, no time anchor check. | NOT DONE |

### What Exists (Current Episode System)

All of these work correctly — F9 is about upgrading *quality*, not *functionality*:

- `MemoryEpisode` model: `date`, `time`, `summary`, `topics_json`, `emotional_arc`, `significance_score`, `turn_count`, `message_indices_json`, `segmentation_method`, `needs_regeneration`
- `episodes.py`: `maybe_generate_episode()` with sequential path (≤6 turns) and batch path (≥8 turns via F6)
- `episode_generation.md.j2`: 17-line template requesting JSON with summary, topics, emotional_arc, significance
- `build_episodes_memory_block()`: renders 5 most recent episodes as bullet points
- Episode merging: `_merge_episodes()` merges same-day overlapping-topic episodes
- LLM fallback: `_create_fallback_episode()` for when LLM fails

### Implementation Effort

**Small** — this is entirely a prompt + post-processing change:
1. Update `episode_generation.md.j2` with time handling, entity rules, conciseness sections (~20 lines added)
2. Pass `user_name` + conversation timestamp to template context in `_call_llm_for_episode_safe()`
3. Add lightweight post-generation checks in `_build_episode_from_parsed()` (pronoun density, length)
4. No schema changes, no new tables, no new services

### Success Criteria Status

| Criterion | Status |
|-----------|--------|
| Episodes use user's name instead of "the user" | NOT DONE |
| Relative times include resolved absolute dates | NOT DONE |
| Average episode length decreases 20%+ | NOT DONE |
| Retrieval precision for name-based queries improves | NOT DONE |
| Post-generation quality checks catch pronoun-heavy episodes | NOT DONE |

---

## 8. References

- Agent memory systems research (arXiv:2601.02163) — episode generation prompt with dual-time format, entity rules, conciseness constraints
- AnimaOS `services/agent/episodes.py` — current episode generation pipeline
- Zacks & Swallow (2007) — Event Segmentation Theory (episode boundary detection)
