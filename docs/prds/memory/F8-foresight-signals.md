---
title: "PRD: F8 - Foresight Signals (Time-Bounded Predictions from Conversation)"
description: Extract temporal predictions from conversation to enable proactive anticipation
category: prd
version: "1.0"
---

# PRD: F8 - Foresight Signals

**Version**: 1.0
**Date**: 2026-03-28
**Status**: Not Started (0%)
**Roadmap Phase**: 10.8
**Priority**: P1 - High
**Depends on**: None (uses existing consolidation pipeline)
**Blocked by**: Nothing
**Blocks**: Nothing (enhances Phase 7 proactive companion)
**Inspired by**: Agent memory research (arXiv:2601.02163) — foresight signal extraction with temporal anchoring

---

## 1. Overview

Extract time-bounded predictions from conversation during the existing consolidation pass. When a user mentions a future event — a deadline, an appointment, a plan, a recurring obligation — the system captures it as a structured foresight signal with start/end dates, evidence, and status.

Foresight signals feed directly into the proactive companion (Phase 7): the AI can anticipate events, follow up on outcomes, and demonstrate temporal awareness without being explicitly told to track something.

This is not a calendar or task system. It is a temporal awareness layer for the memory system — the AI notices that you mentioned something upcoming and remembers to care about it later.

---

## 2. Problem Statement

### Current State

AnimaOS extracts facts, emotions, and episodes from conversation. It has an intentions system for goals. But none of these capture **temporal predictions** — specific future events with bounded timeframes.

When a user says "I have a dentist appointment next Tuesday" or "the quarterly review is in two weeks," this information is stored as a generic fact or not captured at all. The AI has no structured way to:

- Know that next Tuesday is relevant
- Follow up after the event ("how did the dentist go?")
- Anticipate stress before the event ("your quarterly review is tomorrow — how are you feeling about it?")

### The Gap

| What user says | What AnimaOS captures today | What it should capture |
|---|---|---|
| "I have a presentation next Thursday" | fact: "user has a presentation" (no date) | foresight: presentation, 2026-04-03, 1 day |
| "My mom's visiting for two weeks starting the 10th" | fact: "user's mom is visiting" | foresight: mom visiting, 2026-04-10 → 2026-04-24, 14 days |
| "I need to submit this proposal by end of month" | fact: "user has a proposal deadline" | foresight: proposal deadline, end 2026-03-31 |
| "We have standup every morning at 9" | fact: "user has daily standups" | foresight: recurring standup, daily, ongoing |

### User-Visible Impact

Without foresight signals, the AI cannot demonstrate temporal awareness — the quality that makes a companion feel like it is paying attention to the arc of your life, not just the current message.

---

## 3. Design

### 3.1 Data Model

New table: `foresight_signals`

| Column | Type | Notes |
|--------|------|-------|
| id | int PK | auto-increment |
| user_id | int FK → users | |
| content | text | What is predicted ("user has a presentation") |
| evidence | text | The conversation excerpt that triggered extraction |
| start_date | date | When the event begins (nullable for open-ended) |
| end_date | date | When the event ends (nullable for point events) |
| duration_days | int | Computed or extracted duration |
| status | varchar | `active`, `occurred`, `expired`, `cancelled` |
| source_thread_id | int | Which conversation thread it was extracted from |
| created_at | datetime | |
| updated_at | datetime | |

### 3.2 Extraction

Foresight extraction happens during the existing LLM consolidation pass (`consolidation.py`). Add a `foresight` key to the extraction tool schema:

```python
# Added to the existing extraction tool call
{
    "foresight": [
        {
            "content": "User has a presentation at work",
            "evidence": "I have a big presentation next Thursday",
            "start_date": "2026-04-03",
            "end_date": "2026-04-03",
            "duration_days": 1
        }
    ]
}
```

The LLM already receives the conversation transcript for fact/emotion extraction. Foresight is one more structured output from the same call — no additional LLM invocation needed.

### 3.3 Date Resolution

The extraction prompt must resolve relative dates to absolute dates. The current conversation timestamp provides the anchor:

- "next Thursday" → compute from conversation date
- "in two weeks" → conversation date + 14 days
- "end of month" → last day of current month
- "every morning" → flag as recurring, no end date

The dual-format approach from prior research is worth adopting: store both the original relative expression and the resolved absolute date for retrieval flexibility.

### 3.4 Lifecycle

| Status | Meaning | Transition |
|--------|---------|------------|
| `active` | Event is in the future | Created during extraction |
| `occurred` | Event date has passed | Automatic (daily sweep or on next conversation after date) |
| `expired` | Event passed without follow-up | Automatic after 7 days post-event |
| `cancelled` | User explicitly cancelled | User says "that got cancelled" → consolidation updates status |

### 3.5 Consumption

Foresight signals are consumed in three places:

**1. System prompt (memory blocks).** Active foresight signals are included in the prompt context as a dedicated block, similar to intentions:

```
[Upcoming]
- Presentation at work (Thursday, April 3)
- Mom visiting (April 10-24)
- Proposal deadline (March 31)
```

**2. Proactive companion (Phase 7).** The greeting and nudge system checks for:
- Events happening today → "Good luck with your presentation today"
- Events that just passed → "How did the presentation go?"
- Events approaching → natural awareness in conversation tone

**3. Reflection (deep monologue).** During daily reflection, the AI reviews foresight signals as part of its temporal awareness — connecting upcoming events to emotional patterns, workload, and relationship context.

### 3.6 Deduplication

If the user mentions the same event in multiple conversations ("my presentation Thursday" repeated three times), the system should not create three signals. Deduplication checks:
- Semantic similarity of `content` field
- Overlapping date ranges
- Same source thread

If a match is found, update the existing signal rather than creating a duplicate.

---

## 4. Implementation Plan

| Step | File | Change |
|------|------|--------|
| 1 | `alembic_core/versions/` | New migration: create `foresight_signals` table |
| 2 | `models/core.py` | Add `ForesightSignal` SQLAlchemy model |
| 3 | `services/agent/consolidation.py` | Add foresight extraction to LLM tool schema, persist results |
| 4 | `services/agent/memory_blocks.py` | Add `build_foresight_block()` for system prompt injection |
| 5 | `services/agent/proactive.py` | Query foresight signals for greetings/nudges |
| 6 | `api/routes/consciousness.py` | GET/PUT endpoints for viewing/editing foresight signals |

**Estimated effort**: Small. Mostly schema + prompt additions to existing pipeline.

---

## 5. Success Criteria

- [ ] Foresight signals are extracted during consolidation when the user mentions future events
- [ ] Active signals appear in the system prompt as a memory block
- [ ] Signals automatically transition to `occurred` after their date passes
- [ ] The proactive greeting references upcoming/recent events naturally
- [ ] Duplicate mentions of the same event do not create duplicate signals
- [ ] User can view and edit foresight signals via the consciousness API

---

## 6. Implementation Audit (2026-03-28)

### Summary: NOT STARTED (0%)

Zero implementation exists. No table, no model, no extraction, no consumption. Grep for `foresight` and `ForesightSignal` across the entire server codebase returns zero results.

### Implementation Plan vs. Codebase

| Step | What's needed | Status |
|------|---------------|--------|
| 1. Migration | `foresight_signals` table | NOT DONE — no migration |
| 2. Model | `ForesightSignal` SQLAlchemy model | NOT DONE — no model |
| 3. Extraction | Foresight key in consolidation LLM schema + persistence | NOT DONE — consolidation extracts facts + emotions only |
| 4. Memory block | `build_foresight_block()` in `memory_blocks.py` | NOT DONE |
| 5. Proactive | Query foresight signals in `proactive.py` for greetings/nudges | NOT DONE — proactive gathers episodes, tasks, emotional signals, self-model; no foresight |
| 6. API | GET/PUT endpoints in consciousness routes | NOT DONE |

### Adjacent Systems That Exist

- **Intentions** (`services/agent/intentions.py`, `set_intention` tool): provides goal tracking with optional deadlines. Agent-initiated (AI calls `set_intention`), not auto-extracted. No start/end date ranges, no evidence text, no lifecycle transitions. PRD notes foresight is "complementary, not overlapping."
- **Tasks** (`models/task.py`, `create_task` tool): user-facing task list with `due_date`, `priority`. Not temporal prediction — requires explicit creation.

### Success Criteria Status

| Criterion | Status |
|-----------|--------|
| Foresight extracted during consolidation | NOT DONE |
| Active signals appear in system prompt | NOT DONE |
| Automatic lifecycle transitions (active → occurred → expired) | NOT DONE |
| Proactive greeting references upcoming events | NOT DONE |
| Deduplication of repeated event mentions | NOT DONE |
| User can view/edit via consciousness API | NOT DONE |

---

## 7. References

- Agent memory systems research (arXiv:2601.02163) — foresight signal extraction with start/end dates and evidence grounding
- AnimaOS Phase 7 (Proactive Companion) — greeting, nudge, and brief endpoints
- AnimaOS intentions system — existing goal/intention lifecycle (foresight is complementary, not overlapping)
