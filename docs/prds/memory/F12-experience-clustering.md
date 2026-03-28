---
title: "PRD: F12 — Experience Clustering (MemScene)"
description: Group similar agent experiences into semantic clusters for skill distillation and pattern analysis
category: prd
version: "1.0"
---

# PRD: F12 — Experience Clustering

**Version**: 1.0
**Date**: 2026-03-28
**Status**: Not Started (0%)
**Roadmap Phase**: 11.2
**Priority**: P2 — Medium
**Depends on**: F11 (Agent Experience Extraction) — needs experiences to cluster
**Blocked by**: F11 must be implemented first
**Blocks**: F13 (Skill Distillation) — needs clusters to distill from
**Inspired by**: Agent memory research (arXiv:2601.02163) — centroid-based incremental clustering for agent experiences

---

## 1. Overview

Group semantically similar agent experiences into clusters ("MemScenes"). When the agent has helped plan 5 different trips, those 5 experiences should be in the same cluster. When it has debugged 3 different technical problems, those go in another cluster.

Clustering serves two purposes:
1. **Skill distillation** (F13): Clusters provide the input — "here are 5 similar cases, distill the best approach"
2. **Pattern visibility**: The agent (and user) can see what kinds of tasks the AI handles repeatedly, and how it's improving

This is a pure computation component. It assigns a `cluster_id` to each new `AgentExperience` row created by F11. No new tables beyond a state table. No new infrastructure.

---

## 2. Problem Statement

### Current State

F11 stores individual experiences as flat rows. Each experience has an embedding on `task_intent`. But there is no grouping — the system cannot answer:
- "What types of tasks have I handled repeatedly?"
- "Which experiences are about the same kind of problem?"
- "Do I have enough similar cases to generalize a skill?"

### The Gap

Without clustering:
- F13 (Skill Distillation) has no input — it doesn't know which experiences to group together
- The growth log cannot report patterns like "I've helped with trip planning 5 times and my quality scores are improving"
- Retrieval returns individual experiences but cannot surface "I have deep experience with this type of task" vs. "I've only seen this once"

### Why Not Just Use Similarity at Query Time?

On-the-fly grouping (embed the query, find top-k similar experiences, treat them as a cluster) works for retrieval. But skill distillation needs **stable** groups — the same experiences grouped together every time, so incremental learning can accumulate. Ad-hoc clusters would produce different groupings on each run, preventing coherent skill evolution.

### Research Precedent

Prior art in agent memory systems (arXiv:2601.02163) demonstrates:
- **Incremental centroid-based clustering**: No batch recomputation. Each new experience is compared to existing cluster centroids and assigned to the nearest one (or starts a new cluster).
- **Time-gap gating**: Clusters have a maximum time gap — if the new event is too far from the cluster's last event, it starts a new cluster even if semantically similar. This prevents unrelated tasks from being merged just because they happen to be about the same topic.
- **Per-group state**: Each group (user/session) has its own cluster state with centroids, counts, and timestamps.
- **Pure computation**: The clustering component does not touch storage. Caller loads state, calls the clustering function, saves state.

This design is directly adoptable for AnimaOS with minor adaptation.

---

## 3. Design

### 3.1 Data Model

#### 3.1.1 Cluster State Table

New table: `experience_cluster_state` (in the **soul database**)

| Column | Type | Notes |
|--------|------|-------|
| id | int PK | auto-increment |
| user_id | int FK → users | One row per user (single-user system, but future-safe) |
| state_json | JSON | Serialized `ClusterState` — centroids, counts, assignments |
| updated_at | datetime | Last time clustering ran |

This is a single-row-per-user table. The `state_json` blob contains the full cluster state needed for incremental clustering. This avoids creating a separate table per cluster.

#### 3.1.2 AgentExperience.cluster_id

F11 already defines `cluster_id` (varchar, nullable) on the `agent_experiences` table. F12 populates this column.

### 3.2 Clustering Algorithm

Adapted from prior research's clustering approach. Pure computation, no external dependencies.

#### 3.2.1 Core Algorithm

```python
class ExperienceClusterManager:
    """Incremental centroid-based clustering for agent experiences."""

    def __init__(self, similarity_threshold: float = 0.75, max_time_gap_days: int = 90):
        self.similarity_threshold = similarity_threshold
        self.max_time_gap_days = max_time_gap_days

    def assign_cluster(
        self,
        experience_embedding: list[float],
        experience_timestamp: float,
        state: ClusterState,
    ) -> tuple[str, ClusterState]:
        """Assign a cluster to a new experience.

        1. Compare embedding to all existing cluster centroids (cosine similarity)
        2. Filter by time gap (skip clusters where last activity was > max_time_gap_days ago)
        3. If best match > similarity_threshold → assign to that cluster, update centroid
        4. Else → create a new cluster

        Returns (cluster_id, updated_state).
        """
```

#### 3.2.2 Centroid Update

Running centroid update (no need to recompute from all members):

```
new_centroid = (old_centroid * count + new_vector) / (count + 1)
```

This is O(1) per assignment — no batch recomputation needed regardless of how many experiences exist.

#### 3.2.3 Cluster ID Format

`cluster_{user_id}_{sequential_index:03d}` — e.g., `cluster_1_000`, `cluster_1_001`.

Human-readable, stable, sortable. The sequential index is stored in `ClusterState` and incremented on each new cluster creation.

#### 3.2.4 Configuration

| Parameter | Default | Rationale |
|-----------|---------|-----------|
| `similarity_threshold` | 0.75 | Higher than typical defaults in prior research — AnimaOS has fewer experiences, so clusters should be tighter to avoid merging unrelated tasks |
| `max_time_gap_days` | 90 | 3-month window. Tasks more than 3 months apart are likely different contexts even if semantically similar. Longer than typical defaults (which use seconds) because AnimaOS conversations are less frequent than streaming API usage patterns |
| `min_cluster_size_for_skill` | 3 | Minimum experiences in a cluster before F13 skill distillation triggers. Too few and the "skill" is just a restatement of individual experiences |

### 3.3 Execution

#### 3.3.1 When Clustering Runs

Immediately after F11 creates a new `AgentExperience` row. Clustering is called synchronously within the same background task — it's O(1) per assignment and adds negligible latency.

```python
# In consolidation.py, after experience extraction
experience = await extract_agent_experience(...)
if experience is not None:
    cluster_id = assign_experience_to_cluster(
        db, user_id=user_id, experience=experience
    )
    experience.cluster_id = cluster_id
    db.commit()

    # Notify F13 if cluster is large enough
    if get_cluster_size(db, cluster_id) >= MIN_CLUSTER_SIZE_FOR_SKILL:
        schedule_skill_distillation(user_id, cluster_id)
```

#### 3.3.2 State Persistence

Load `ClusterState` from `experience_cluster_state.state_json` before clustering. Save updated state after. Single read + single write per clustering operation.

The state blob is small — even with 100 clusters, each centroid is ~1536 floats (4 bytes each) = ~600KB total. Well within SQLite's JSON handling capacity.

#### 3.3.3 Cold Start

On the very first experience, no state exists. Create a new `ClusterState` with one cluster containing the first experience. Store state. All subsequent experiences are incremental.

### 3.4 Cluster Introspection

#### 3.4.1 API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/consciousness/experience-clusters` | GET | List all clusters with size, centroid summary, last activity |
| `/api/consciousness/experience-clusters/{cluster_id}` | GET | List all experiences in a cluster |

#### 3.4.2 Growth Log Integration

When a cluster grows past certain thresholds, log growth entries:
- Size 3: "Developing pattern: I've handled {cluster_summary} 3 times"
- Size 5: "Established pattern: {cluster_summary} — 5 experiences, avg quality {avg_score}"
- Size 10: "Deep expertise: {cluster_summary} — 10+ experiences"

The `cluster_summary` is derived from the centroid's nearest `task_intent` text or generated via a simple prompt.

### 3.5 Memory Block Enhancement

Enhance the `past_approaches` block from F11 to include cluster context:

```
[past_approaches]
1. [0.87 relevance, 5 similar experiences] Helped plan a trip
   Approach: ...
   Quality: 0.9
   Note: I have deep experience with this type of task (5 past cases, avg quality 0.85)

2. [0.72 relevance, 1 similar experience] Organized a surprise party
   Approach: ...
   Quality: 0.7
```

The cluster size annotation helps the agent calibrate confidence — "I've done this many times" vs. "this is my first time."

---

## 4. Implementation Plan

| Step | File | Change |
|------|------|--------|
| 1 | `alembic_core/versions/` | New migration: create `experience_cluster_state` table |
| 2 | `models/` | Add `ExperienceClusterState` SQLAlchemy model |
| 3 | `services/agent/experience_clustering.py` (new) | `ExperienceClusterManager` — pure computation, `ClusterState` dataclass, serialization |
| 4 | `services/agent/experience_extraction.py` | Call clustering after experience creation |
| 5 | `services/agent/memory_blocks.py` | Enhance `build_past_approaches_block()` with cluster size annotation |
| 6 | `services/agent/self_model.py` | Log growth entries at cluster size thresholds |
| 7 | `api/routes/consciousness.py` | Cluster listing and detail endpoints |
| 8 | Tests | Unit tests for clustering algorithm, state serialization, threshold behavior |

### Step Dependencies

```
1 → 2 → 3 (clustering engine)
3 → 4 (integration with F11)
3 → 5 (memory block enhancement)
3 → 6 (growth log)
2 → 7 (API)
All → 8 (tests)
```

---

## 5. Design Decisions

### 5.1 Incremental, Not Batch

Full recomputation (k-means, DBSCAN) would require loading all experience embeddings and running a batch algorithm. For AnimaOS's scale (tens to hundreds of experiences over months of use), incremental centroid-based assignment is sufficient and dramatically simpler. If clusters drift over time, a periodic batch recomputation can be added as a sleep-time task — but this is not expected to be necessary.

### 5.2 Single State Blob, Not Per-Cluster Tables

Prior research stores cluster state per-group in a document database. AnimaOS stores it as one JSON blob per user. This avoids table proliferation and is simpler to migrate. The tradeoff is that the entire state must be loaded/saved atomically — acceptable for the expected scale.

### 5.3 Time Gating in Days, Not Seconds

Streaming agent memory systems process conversation data where temporal proximity is measured in seconds/minutes. AnimaOS processes discrete conversation sessions separated by hours or days. Time gating at the 90-day level prevents "trip planning in January" from merging with "trip planning in December" when the contexts may be very different.

### 5.4 Higher Similarity Threshold (0.75 vs ~0.65)

AnimaOS has fewer experiences than a high-volume API service. Tighter clusters mean more meaningful groupings and better skill distillation input. A cluster that mixes "trip planning" with "event coordination" produces a muddled skill. Better to keep them separate and let F13 produce distinct, focused skills.

### 5.5 No Vector Store Infrastructure

Centroid comparison is a simple numpy dot product operation over a small number of clusters (expected: 10–50 clusters after months of use). No Milvus, no FAISS, no ANN index needed. If the system scales to thousands of clusters, an ANN index can be added transparently.

---

## 6. Success Criteria

- [ ] New experiences are assigned a `cluster_id` within the same background task as extraction
- [ ] Semantically similar experiences (e.g., multiple trip planning tasks) land in the same cluster
- [ ] Dissimilar experiences get separate clusters
- [ ] Time gating prevents merging of temporally distant experiences
- [ ] Cluster state persists across server restarts
- [ ] Growth log entries are created at cluster size thresholds (3, 5, 10)
- [ ] API endpoints return cluster listings and details
- [ ] Past approaches memory block shows cluster size context
- [ ] Clustering adds < 50ms to the background consolidation task

---

## 7. Interaction with Other Features

| Feature | Interaction |
|---------|-------------|
| F11 (Experiences) | F12 reads experiences to assign clusters. Called immediately after F11 creates a new experience. |
| F13 (Skills) | F12 triggers F13 when a cluster reaches `min_cluster_size_for_skill`. Provides the cluster_id for F13 to query. |
| F5 (Sleep Agents) | Clustering runs within the existing consolidation background task. No separate sleep-time trigger needed. |
| Phase 10 (Consciousness) | Cluster growth thresholds log to the growth log, enriching the agent's self-awareness. |

---

## 8. References

- Agent memory systems research (arXiv:2601.02163) — centroid-based incremental clustering algorithm
- AnimaOS F11 — `AgentExperience.cluster_id` column definition
- AnimaOS `services/agent/embeddings.py` — existing embedding infrastructure
