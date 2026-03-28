---
title: "PRD: F10 - Structured User Profile (Evidence-Backed, Category-Typed)"
description: Replace free-text human memory with a structured, evidence-linked user profile extracted during consolidation
category: prd
version: "1.0"
---

# PRD: F10 - Structured User Profile

**Version**: 1.0
**Date**: 2026-03-28
**Status**: Partial (~15%) — claims system provides partial structured storage
**Roadmap Phase**: 10.10
**Priority**: P2 - Medium
**Depends on**: None
**Blocked by**: Nothing
**Blocks**: Improves retrieval precision, proactive companion quality, eval accuracy
**Inspired by**: Agent memory research (arXiv:2601.02163) — progressive user profiling with structured categories and evidence linking

---

## 1. Overview

Replace the free-text `human` memory block with a structured user profile that categorizes user information into typed fields with evidence linking. Instead of a single prose block the agent writes mid-conversation, the profile is a structured document extracted systematically during consolidation — comprehensive, evidence-backed, and progressively refined.

The agent-written `human` block (via `update_human_memory` tool) remains for fast mid-conversation updates. The structured profile is the consolidation-time complement — more systematic, more complete, and typed for precise retrieval.

---

## 2. Problem Statement

### Current State

AnimaOS has a `human` memory block — a free-text section the agent writes via the `update_human_memory` tool during conversation. It contains whatever the agent decides to note:

> Leo is a solo founder building AnimaOS. Works late, prefers direct communication.
> Has a partner named Alex. Interested in cognitive science and crypto.
> Recently stressed about a deadline.

This works but has limitations:

- **Unstructured** — "Works late" and "partner named Alex" are in the same blob. Searching for relationship information requires parsing the whole block.
- **Agent-dependent quality** — The agent decides what to write and when. It may miss things. It may write vague summaries. It may not update consistently.
- **No evidence linking** — There is no record of which conversation produced each piece of information. If something is wrong, you can't trace it back.
- **No category typing** — Skills, preferences, relationships, work context, and personality traits are mixed together. Retrieval cannot filter by type.

### What Prior Art Does

Research implementations (arXiv:2601.02163) extract structured profiles during consolidation with typed categories:

| Category | What it captures | Example |
|----------|-----------------|---------|
| `hard_skills` | Technical abilities with level | Python (advanced), React (beginner) |
| `soft_skills` | Communication, leadership, etc. | Concise communicator, prefers async |
| `personality` | Behavioral tendencies | Introvert, systematic thinker |
| `opinion_tendency` | Stances and preferences | Prefers PostgreSQL, skeptical of cloud AI |
| `working_habit_preference` | Work patterns | Works late, deep work mornings |
| `role_responsibility` | Current role/position | Solo founder, full-stack |

Each field includes `evidences` — links to the conversation IDs where the information was stated. This makes the profile auditable and correctable.

### The Gap

AnimaOS stores user facts as generic `memory_items` (type: preference, fact, relationship, etc.) and the free-text `human` block. Neither provides the structured, categorical, evidence-linked profile that enables:
- Type-filtered retrieval ("what are the user's skills?" → query `hard_skills` only)
- Progressive refinement (each conversation adds to existing categories, not rewrites the whole block)
- Auditability (each profile field traces to a source conversation)

---

## 3. Design

### 3.1 Data Model

New table: `user_profile_fields`

| Column | Type | Notes |
|--------|------|-------|
| id | int PK | auto-increment |
| user_id | int FK → users | |
| category | varchar | `relationship`, `skill`, `preference`, `personality`, `work_context`, `life_context`, `communication_style` |
| key | varchar | Field name within category (e.g., "partner", "python_skill") |
| value | text | The profile value ("Alex — partner, supportive, works in finance") |
| confidence | float | 0.0-1.0, based on evidence strength |
| evidence_threads | text (JSON) | List of thread IDs where this was observed |
| evidence_quotes | text (JSON) | Key quotes that support this field |
| first_observed | datetime | When this field was first extracted |
| last_updated | datetime | When most recently confirmed or updated |
| superseded_by | int FK → self | If this field was replaced by a newer version |

### 3.2 Profile Categories

Tailored to AnimaOS's companion use case (not a team/project focus):

| Category | What it captures | Examples |
|----------|-----------------|---------|
| `relationship` | People in the user's life | Partner: Alex (supportive, works in finance). Sister: Maria (lives abroad) |
| `skill` | Technical and professional abilities | Python (advanced), React (learning), public speaking (anxious about it) |
| `preference` | Stated preferences and opinions | Prefers direct communication. Likes dark mode. Skeptical of cloud AI |
| `personality` | Behavioral patterns the AI has observed | Introvert. Thinks out loud. Gets impatient with long explanations |
| `work_context` | Current professional situation | Solo founder at AnimaOS. Works late. Deadline-driven |
| `life_context` | Life situation and circumstances | Lives in Manila. Has a dog. Recently moved |
| `communication_style` | How the user prefers to interact | Prefers short answers. Likes examples over theory. Uses "huhuh" when amused |

### 3.3 Extraction Pipeline

Profile extraction happens during the existing LLM consolidation pass. Add a `profile_updates` key to the extraction tool schema:

```python
{
    "profile_updates": [
        {
            "category": "relationship",
            "key": "partner",
            "value": "Alex — partner, recently stressed about job",
            "evidence_quote": "my partner Alex is stressed about their job",
            "action": "update"  # or "add" or "no_change"
        }
    ]
}
```

The LLM receives:
1. The conversation transcript (already provided for fact extraction)
2. The current profile fields for this user (so it can decide update vs. add vs. no_change)

**Inertia principle** (from prior research): existing profile fields are considered correct but incomplete. The LLM should only add new information or update with explicit evidence — never remove or contradict without strong justification.

### 3.4 Relationship to Existing `human` Block

The `update_human_memory` tool and the free-text `human` block remain. They serve different purposes:

| | `human` block (existing) | Structured profile (new) |
|---|---|---|
| **When** | Mid-conversation, agent-initiated | Post-conversation, consolidation-time |
| **How** | Agent decides what to write | LLM systematically extracts all categories |
| **Format** | Free-text prose | Typed fields with evidence |
| **Speed** | Immediate | Async (after conversation ends) |
| **Use** | Fast-path for important observations | Comprehensive, auditable profile |

The structured profile is the **source of truth**. The `human` block is the **fast scratchpad**. During consolidation, observations from the `human` block should be reconciled into the structured profile.

### 3.5 System Prompt Injection

The profile is injected into the system prompt as a dedicated memory block, formatted as prose (not structured data) per the GWT natural-language requirement:

```
[Who You Are Talking To]
Leo is a solo founder building AnimaOS, based in Manila. Works late, deadline-driven,
prefers direct and concise communication — gets impatient with long explanations.
Technical: strong in Python, learning React, anxious about public speaking.
Key people: partner Alex (works in finance, recently stressed about job), sister Maria (lives abroad).
Communication: prefers short answers with examples. Uses "huhuh" when amused.
```

This replaces the existing `human` block rendering. The structured data is stored in the table; the prose rendering is generated at prompt-assembly time.

### 3.6 Open Mind Integration

Profile fields are viewable and editable via the consciousness API:

- `GET /api/consciousness/profile` — returns all profile fields grouped by category
- `PUT /api/consciousness/profile/{id}` — edit a specific field (value, confidence)
- `DELETE /api/consciousness/profile/{id}` — remove a field (user disagrees)

User edits are treated as highest-confidence evidence, logged with `evidence_quote: "user correction"`.

---

## 4. Implementation Plan

| Step | File | Change |
|------|------|--------|
| 1 | `alembic_core/versions/` | New migration: create `user_profile_fields` table |
| 2 | `models/core.py` | Add `UserProfileField` SQLAlchemy model |
| 3 | `services/agent/consolidation.py` | Add profile extraction to LLM tool schema, persist results |
| 4 | `services/agent/memory_blocks.py` | Add `build_profile_block()` — render structured fields as prose |
| 5 | `api/routes/consciousness.py` | Add GET/PUT/DELETE endpoints for profile fields |
| 6 | `services/agent/consolidation.py` | Reconcile `human` block observations into structured profile |

**Estimated effort**: Medium. New table + extraction prompt additions + API endpoints + rendering.

---

## 5. Success Criteria

- [ ] Profile fields are extracted during consolidation with category, value, and evidence
- [ ] Profile renders as a prose block in the system prompt
- [ ] Existing information is preserved across extractions (inertia principle)
- [ ] User can view, edit, and delete profile fields via consciousness API
- [ ] Profile fields have evidence linking to source conversations
- [ ] Retrieval queries can filter by profile category

---

## 6. Eval Impact

Structured profiles improve benchmark scores in specific ways:
- **LoCoMo single-hop** (cat 1): "What is Caroline's identity?" → `category: life_context` directly answers
- **LoCoMo multi-hop** (cat 3): "Would Caroline still want counseling if she hadn't received support?" → relationship + life_context + preference categories provide connected evidence
- **LongMemEval knowledge-update**: Structured supersession tracking (via `superseded_by`) handles "what is their CURRENT job?" better than flat fact search

---

## 7. Implementation Audit (2026-03-28)

### Summary: PARTIAL (~15%)

No direct F10 implementation exists. However, the `MemoryClaim` + `MemoryClaimEvidence` system provides partial structured storage that covers ~15% of the vision.

### Implementation Plan vs. Codebase

| Step | What's needed | Status |
|------|---------------|--------|
| 1. Migration | `user_profile_fields` table | NOT DONE — no migration, no table |
| 2. Model | `UserProfileField` SQLAlchemy model | NOT DONE — no model |
| 3. Extraction | `profile_updates` key in consolidation LLM schema | NOT DONE — consolidation extracts facts + emotions, not typed profile fields |
| 4. Prompt rendering | `build_profile_block()` — structured fields rendered as prose | NOT DONE — `build_human_core_block()` renders User model fields + free-text `human` block |
| 5. API | GET/PUT/DELETE endpoints for profile fields | NOT DONE — no profile endpoints in consciousness routes |
| 6. Reconciliation | `human` block → structured profile reconciliation | NOT DONE |

### What Exists That Partially Addresses F10

| Component | What it provides | What F10 adds |
|-----------|-----------------|---------------|
| **`MemoryClaim`** (`agent_runtime.py:371-455`) | Structured claims with `subject_type`, `namespace` (fact/preference/goal/relationship), `slot` (age/occupation/location), `value_text`, `confidence`, `status`, `canonical_key`, `superseded_by_id` | F10's 7 categories (relationship, skill, preference, personality, work_context, life_context, communication_style), evidence quotes, prose rendering |
| **`MemoryClaimEvidence`** (`agent_runtime.py:458-480`) | Source evidence with `source_text`, `source_kind` per claim | F10's `evidence_threads` + `evidence_quotes` — similar concept, different shape |
| **`claims.py`** (`services/agent/claims.py`) | `upsert_claim()` with canonical keys like `user:fact:occupation`. Slot-detection patterns for age, birthday, occupation, employer, location, name, gender, likes, prefers, dislikes | F10's systematic extraction across 7 categories |
| **`update_human_memory` tool** (`tools.py`) | Agent writes free-text user understanding mid-conversation | F10 preserves this as "fast scratchpad" — separate from structured profile |
| **`build_human_core_block()`** (`memory_blocks.py:508-559`) | Combines User model fields (name, gender, age, birthday) + agent-authored `human` SelfModelBlock | F10 replaces this with prose rendering from structured profile fields |
| **`synthesize_profile()`** (`sleep_tasks.py`) | Merges related facts during sleep tasks (compaction, not extraction) | F10 adds systematic extraction pipeline, not just compaction |

### Gap Analysis

| F10 Feature | Claims System Coverage | Gap |
|-------------|----------------------|-----|
| 7 profile categories | 4 namespaces (fact, preference, goal, relationship) | Missing: skill, personality, work_context, life_context, communication_style |
| Systematic extraction in consolidation | Claims are dual-written alongside facts — not independently extracted by category | No category-driven extraction pipeline |
| Inertia principle (preserve existing fields) | No existing profile context sent to extraction LLM | LLM extracts blind — no awareness of existing profile |
| Prose rendering for system prompt | `build_human_core_block()` renders raw User fields + free text | No prose generation from structured data |
| User-facing profile API | No profile endpoints | No view/edit/delete for structured profile |
| Evidence linking to threads | `MemoryClaimEvidence.source_text` exists | No thread_id linkage |
| Confidence scoring | `MemoryClaim.confidence` exists | Populated but not used for retrieval ranking |

### Success Criteria Status

| Criterion | Status |
|-----------|--------|
| Profile fields extracted during consolidation with category + evidence | NOT DONE |
| Profile renders as prose block in system prompt | NOT DONE |
| Existing information preserved across extractions (inertia) | NOT DONE |
| User can view/edit/delete via consciousness API | NOT DONE |
| Evidence linking to source conversations | PARTIAL — `MemoryClaimEvidence` exists but not profile-scoped |
| Category-filtered retrieval | NOT DONE |

---

## 8. References

- Agent memory systems research (arXiv:2601.02163) — progressive user profiling with 8+ structured categories, evidence linking, inertia principle
- MemoryOS — 90-dimension personality profiling (Big Five + 85 custom dimensions)
- AnimaOS `services/agent/memory_blocks.py` — existing `human` block rendering
- AnimaOS `services/agent/tools.py` — `update_human_memory` tool definition
- GWT (Baars, 1988) — natural-language format requirement for global workspace broadcasting
