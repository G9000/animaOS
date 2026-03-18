---
title: "PRD: F7 — Intentional Forgetting"
description: Passive decay, active suppression, and user-initiated cryptographic deletion of memories
category: prd
version: "1.0"
---

# PRD: F7 — Intentional Forgetting

**Version**: 1.0
**Date**: 2026-03-18
**Status**: Draft
**Roadmap Phase**: 10.5
**Priority**: P1
**Depends on**: None (standalone); benefits from F2 (cold items as decay candidates)
**Blocks**: Nothing directly

---

## 1. Overview

Implement forgetting as a first-class memory operation — not just archival or soft-deletion. Three mechanisms cover the spectrum from passive to active:

1. **Passive decay** — memories that are never accessed gradually lose retrieval priority and eventually fall below the visibility threshold
2. **Active suppression** — explicitly corrected or superseded memories have their associative connections weakened, reducing their influence on the self-model
3. **User-initiated forgetting** — cryptographic deletion of specific memories, episodes, or conversation segments with derived-reference cleanup

Forgetting is not a failure mode. Biological memory systems forget deliberately — it is essential for generalization, noise reduction, and identity coherence (Hatua et al., ICAART 2026; Richards & Frankland, Neuron 2017). An AI that never forgets accumulates noise, outdated beliefs, and contradictory self-model fragments.

---

## 2. Problem Statement

### Current Implementation

- **Soft supersession**: `memory_store.py` marks contradicted facts as `superseded=True`, excluding them from active retrieval but retaining them in the database
- **No decay**: memories retain full retrieval weight indefinitely if accessed even once; there is no mechanism for gradual fading
- **No deletion**: users can delete individual memories via the API, but this is a raw database delete with no cleanup of derived references (episodes, growth log entries, self-model sections that cite the memory)
- **No suppression**: corrected memories are superseded but their prior influence on episodes, behavioral rules, and self-model sections is not unwound

### The Gaps

| Gap | Impact |
|-----|--------|
| **No passive decay** | The memory store grows monotonically. Old, irrelevant facts compete with current knowledge for retrieval budget. |
| **No derived-reference cleanup** | Deleting a memory leaves orphaned citations in growth log entries, episode summaries, and self-model text. The AI may still "remember" deleted information through these derived references. |
| **No suppression mechanism** | A corrected fact is superseded, but episodes and behavioral rules derived from it remain unchanged. The old belief continues to influence behavior through its downstream effects. |
| **Whitepaper claim mismatch** | The comparison table claims "passive decay + active suppression" but neither is implemented. |

### Theoretical Foundation

- **Richards & Frankland (2017)**: "The Persistence and Transience of Memory" — forgetting is not a failure of memory but a functional feature that promotes generalization and prevents overfitting to specific experiences
- **Hatua et al. (ICAART 2026)**: Forgetting in Neural Networks — rank-based forgetting targeting most-activated neurons, Ebbinghaus-inspired decay curves, demonstrating that strategic forgetting improves network generalization
- **CLS theory (McClelland & O'Reilly, 1995)**: The neocortical system generalizes by gradually forgetting episode-specific details while retaining structural patterns — this is not a bug but the mechanism by which semantic knowledge emerges from episodic experience

---

## 3. Goals and Non-Goals

### Goals

1. **Passive decay**: heat-based decay (via F2) naturally reduces retrieval priority of untouched memories; memories below a configurable floor threshold are excluded from retrieval
2. **Active suppression**: when a memory is superseded via conflict resolution, flag derived references (episodes, growth log, behavioral rules) for regeneration
3. **User-initiated forgetting**: `DELETE /api/memories/{id}/forget` that performs cryptographic deletion with cascade cleanup
4. **Topic-scoped forgetting**: `DELETE /api/memories/forget?topic=...` to forget all memories matching a topic or entity (e.g., "forget everything about Alex")
5. **Forget audit trail**: record that forgetting occurred (timestamp, scope, trigger) without recording what was forgotten — preserving the right to forget while maintaining system integrity
6. **Derived-reference cleanup**: scan growth log, episodes, behavioral rules, and self-model sections for citations of forgotten memories; flag for regeneration or redaction

### Non-Goals

- Forgetting model weights or fine-tuned parameters (ANIMA uses prompted behavior, not fine-tuning)
- Automatic forgetting without user consent (passive decay reduces priority but does not delete)
- GDPR-specific compliance tooling (the mechanisms support GDPR requirements but we are not building a compliance dashboard)
- Forgetting conversation history (messages are outside memory scope; this PRD covers memory items, episodes, and derived references)

---

## 4. Detailed Design

### 4.1 Passive Decay

Passive decay is handled by F2's heat scoring system. This PRD adds:

**Visibility threshold**: memories with `heat < HEAT_VISIBILITY_FLOOR` are excluded from `get_memory_items_scored()` results. They still exist in the database but are invisible to retrieval.

```python
HEAT_VISIBILITY_FLOOR: float = 0.01  # Below this, memory is functionally forgotten
```

**Decay acceleration for superseded items**: superseded memories decay at 3x the normal rate (lower `tau`), so corrected information fades faster than merely unused information.

```python
SUPERSEDED_DECAY_MULTIPLIER: float = 3.0
```

### 4.2 Active Suppression

When a memory is superseded via conflict resolution (`consolidation.py`), the suppression pipeline runs:

```python
def suppress_memory(
    db: Session,
    *,
    memory_id: int,
    superseded_by: int,
    user_id: int,
) -> SuppressionResult:
    """
    1. Mark memory as superseded (existing behavior)
    2. Find derived references citing this memory
    3. Flag them for regeneration in the next sleep cycle
    4. Record suppression event in forget_audit_log
    """
    ...
```

**Derived reference detection** uses text matching against the memory's `content` field:
- Growth log entries containing the superseded fact text (stored in `self_model_blocks` where `section='growth_log'`)
- Episode summaries containing the superseded fact text (`memory_episodes.summary`)
- Behavioral rules containing the superseded fact text (stored in `self_model_blocks` where `section='intentions'`)

Flagged references get a `needs_regeneration=True` marker. During the next sleep cycle, the deep monologue agent regenerates these references using current (non-superseded) knowledge.

**Note on growth_log structure**: `SelfModelBlock` with `section='growth_log'` stores the entire growth log as a single text blob, not individual entries. Derived reference detection searches within this blob for substring matches against the suppressed memory's content. Redaction requires editing the blob text (removing or replacing the matching passage), not flipping a flag on individual rows. The `needs_regeneration=True` flag on the `SelfModelBlock` row triggers full regeneration of the growth log section during the next sleep cycle.

### 4.3 User-Initiated Forgetting

**Single memory forget**:

```
DELETE /api/memories/{memory_id}/forget
```

**Topic-scoped forget**:

```
DELETE /api/memories/forget?topic={topic}
```

The forget operation:

```python
def forget_memory(
    db: Session,
    *,
    memory_id: int,
    user_id: int,
    trigger: str = "user_request",
) -> ForgetResult:
    """
    1. Find all derived references (episodes, growth log, intentions in self_model_blocks)
    2. Redact or flag derived references for regeneration
    3. Delete associated MemoryClaim + MemoryClaimEvidence records (FK on memory_item_id)
    4. Delete the memory item (hard delete, not soft)
    5. Delete the embedding from the vector index (via vector_store.delete_memory())
    6. Invalidate BM25 index (if F1 is implemented)
    7. Record forget event in forget_audit_log (what was forgotten is NOT recorded)
    8. Return ForgetResult with counts of affected references
    """
    ...

def forget_by_topic(
    db: Session,
    *,
    topic: str,
    user_id: int,
) -> ForgetResult:
    """
    1. Use hybrid_search() to find all memories matching the topic
    2. Present candidates to the user for confirmation (via API response)
    3. On confirmation, call forget_memory() for each
    """
    ...
```

### 4.4 Forget Audit Log

New table: `forget_audit_log`

```python
class ForgetAuditLog(Base):
    __tablename__ = "forget_audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    forgotten_at: Mapped[datetime] = mapped_column(DateTime)
    trigger: Mapped[str]  # "user_request", "topic_forget", "suppression"
    scope: Mapped[str]  # "single", "topic:{topic}", "entity:{entity_name}"
    items_forgotten: Mapped[int]  # count only, not content
    derived_refs_affected: Mapped[int]  # count of episodes/growth_log/rules flagged
```

The audit log records *that* forgetting occurred and its scope, but never *what* was forgotten. This preserves the integrity of the forget operation.

### 4.5 Derived Reference Cleanup

```python
def find_derived_references(
    db: Session,
    *,
    memory_content: str,
    user_id: int,
) -> DerivedReferences:
    """
    Search for the memory's content (or key phrases) in:
    - memory_episodes.summary
    - self_model_blocks.content WHERE section='growth_log'
    - self_model_blocks.content WHERE section='intentions' (behavioral rules)
    Returns lists of affected records by type.
    """
    ...

def redact_derived_references(
    db: Session,
    *,
    refs: DerivedReferences,
    strategy: str = "flag_for_regeneration",  # or "immediate_redact"
) -> int:
    """
    flag_for_regeneration: mark refs with needs_regeneration=True
    immediate_redact: replace the citation with '[redacted]'
    Returns count of refs processed.
    """
    ...
```

---

## 5. New File

```
apps/server/src/anima_server/services/agent/forgetting.py
```

Contains: `suppress_memory()`, `forget_memory()`, `forget_by_topic()`, `find_derived_references()`, `redact_derived_references()`

---

## 6. Modified Files

| File | Change |
|------|--------|
| `memory_store.py` | Add `HEAT_VISIBILITY_FLOOR` filter to `get_memory_items_scored()` |
| `consolidation.py` | Call `suppress_memory()` after supersession in conflict resolution |
| `heat_scoring.py` | Apply `SUPERSEDED_DECAY_MULTIPLIER` in `decay_all_heat()` for superseded items |
| `sleep_tasks.py` | Add regeneration step for `needs_regeneration=True` derived references |
| `models/agent_runtime.py` | Add `ForgetAuditLog` model; add `needs_regeneration` column to episodes and growth log |
| `api/routes/memories.py` | Add `DELETE /memories/{id}/forget` and `DELETE /memories/forget?topic=` endpoints |

---

## 7. Data Model Changes

**New table**: `forget_audit_log` (see Section 4.4)

**New columns**:
- `memory_episodes.needs_regeneration` (Boolean, default False)
- `self_model_blocks.needs_regeneration` (Boolean, default False) — for growth_log section entries

**Migration**: `20260320_0001_add_forgetting_tables.py`

---

## 8. Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| F7.1 | `HEAT_VISIBILITY_FLOOR` filter excludes sub-threshold memories from retrieval | Must |
| F7.2 | Superseded memories decay at `SUPERSEDED_DECAY_MULTIPLIER` rate | Must |
| F7.3 | `suppress_memory()` finds and flags derived references on supersession | Must |
| F7.4 | `forget_memory()` performs hard delete + embedding removal + derived ref cleanup | Must |
| F7.5 | `forget_by_topic()` finds matching memories and returns candidates for confirmation | Must |
| F7.6 | `ForgetAuditLog` records scope and counts without recording forgotten content | Must |
| F7.7 | `find_derived_references()` searches `memory_episodes.summary`, `self_model_blocks.content` (sections: growth_log, intentions) | Must |
| F7.8 | `redact_derived_references()` supports flag-for-regeneration and immediate-redact strategies | Should |
| F7.9 | Sleep tasks regenerate `needs_regeneration=True` references using current knowledge | Should |
| F7.10 | REST API endpoints for single and topic-scoped forgetting | Must |
| F7.11 | All existing tests pass after implementation | Must |
| F7.12 | `forget_memory()` deletes associated `MemoryClaim` and `MemoryClaimEvidence` records for the forgotten memory | Must |
| F7.13 | `forget_memory()` invalidates BM25 index after deletion (if F1 is implemented) | Should |

---

## 9. Acceptance Criteria

| # | Criterion | Verification |
|---|-----------|--------------|
| AC1 | A memory with heat below `HEAT_VISIBILITY_FLOOR` does not appear in retrieval results | Unit test |
| AC2 | A superseded memory's heat decays 3x faster than a non-superseded memory | Unit test with known time deltas |
| AC3 | `forget_memory()` removes the memory, its embedding, and flags derived references | Integration test |
| AC4 | After `forget_memory()`, the forgotten content does not appear in any system prompt block | Integration test: forget a fact, build memory blocks, verify absence |
| AC5 | `forget_by_topic()` returns candidate memories matching the topic for confirmation | Unit test |
| AC6 | `ForgetAuditLog` records the event without storing forgotten content | Unit test: inspect log entry after forget |
| AC7 | Derived references flagged `needs_regeneration=True` are regenerated during sleep tasks | Integration test |
| AC8 | All existing tests pass | CI |

---

## 10. Test Plan

| # | Type | Test | Details |
|---|------|------|---------|
| T1 | Unit | Visibility floor filter | Create items with heat 0.005 and 0.5; verify only 0.5 returned |
| T2 | Unit | Superseded decay multiplier | Decay superseded vs non-superseded items; verify 3x faster decay |
| T3 | Unit | `suppress_memory()` | Supersede a memory, verify derived refs flagged |
| T4 | Unit | `forget_memory()` | Forget a memory, verify hard delete + embedding removal |
| T5 | Unit | `forget_by_topic()` | Create memories about "Alex", forget by topic, verify all found |
| T6 | Unit | `ForgetAuditLog` | Verify log entry contains counts but not content |
| T7 | Integration | End-to-end forget | Create memory, reference it in episode + growth log, forget it, verify cleanup |
| T8 | Integration | Prompt absence | Forget a fact, build all memory blocks, verify the fact is absent |
| T9 | Regression | Full suite | All existing tests pass |

---

## 11. Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| **Derived reference detection is fuzzy** | Medium | Text matching against memory content may miss paraphrased references. Start with exact substring match; iterate toward semantic matching if needed. |
| **Regeneration quality depends on LLM** | Low | Regenerated growth log entries may differ from originals. This is acceptable — the point is to remove the forgotten content's influence, not to preserve the exact text. |
| **Topic-scoped forget may be too broad** | Medium | Always return candidates for user confirmation before deleting. Never auto-delete on topic match. |
| **Performance of derived reference scan** | Low | SQLite LIKE queries on text columns. For <10,000 memories, this is fast. Index `memory_episodes.summary` if needed at scale. |

---

## 12. References

- Richards, B. A., & Frankland, P. W. (2017). "The Persistence and Transience of Memory." _Neuron_, 94(6), 1071-1084.
- Hatua, A. et al. (2026). "Forgetting in Neural Networks." _ICAART 2026_.
- McClelland, J. L., & O'Reilly, R. C. (1995). "Why There Are Complementary Learning Systems in the Hippocampus and Neocortex."
- AnimaOS Roadmap Phase 10.5 (Intentional Forgetting)
- AnimaOS Whitepaper Section 9.6 (Intentional Forgetting)
