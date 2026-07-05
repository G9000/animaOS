# Memory Atlas Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `/memory/graph` Memory Atlas surface that visualizes ANIMA's entity graph as a temporal Timeline Bloom with on-demand detail and evidence inspection.

**Architecture:** Keep the existing `/graph` page as the technical graph inspector and add a new Memory Atlas product surface under Memory. Backend routes provide a compact canvas payload and focused detail/evidence reads; the desktop uses `@xyflow/react` for deterministic graph rendering, timeline filtering, search focus, and selection inspection. V1 stays read-only and avoids graph editing, forgetting, or first-class evidence nodes.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, existing `KGEntity`/`KGRelation` models, user unlock dependency, TypeScript API client, React/Vite/Tauri desktop, `@xyflow/react`, Tailwind, pytest, Bun/Nx validation.

---

## Planning Inputs

- Design spec: `docs/superpowers/specs/2026-07-05-memory-atlas-graph-design.md`
- Existing graph route: `apps/server/src/anima_server/api/routes/graph.py`
- Existing graph service: `apps/server/src/anima_server/services/agent/knowledge_graph.py`
- Existing KG models: `apps/server/src/anima_server/models/agent_runtime.py`
- Existing API client graph methods: `packages/api-client/src/client.ts`
- Existing API client graph types: `packages/api-client/src/types.ts`
- Existing desktop graph page: `apps/desktop/src/pages/memory/KnowledgeGraph.tsx`
- Existing desktop memory page: `apps/desktop/src/pages/memory/Memory.tsx`
- Existing graph components: `apps/desktop/src/components/graph/`
- Existing desktop routing: `apps/desktop/src/App.tsx`

## Scope

In scope:

- New `/memory/graph` route.
- Link from the Memory page to Memory Atlas.
- Read-only atlas canvas payload for entities and active relations.
- Read-only entity/relation detail reads with on-demand evidence snippets.
- Typed API client methods and types.
- Timeline Bloom graph rendering with search, filters, timeline scrubber, and inspector.
- Focused backend tests, API-client typing checks, desktop build/typecheck, and visual smoke validation.

Out of scope:

- Editing relations or entities.
- Forgetting from the graph canvas.
- Manual node creation.
- Drag-position persistence.
- Episodes, memory items, messages, or evidence as primary graph nodes.
- Schema migrations unless implementation discovers an unavoidable missing field. Prefer existing `KGEntity` and `KGRelation` columns.

## File Map

| Area | Files |
| --- | --- |
| Backend routes | `apps/server/src/anima_server/api/routes/graph.py` |
| Backend tests | `apps/server/tests/test_memory_atlas_graph_api.py` |
| API client types | `packages/api-client/src/types.ts` |
| API client methods | `packages/api-client/src/client.ts` |
| Desktop route | `apps/desktop/src/App.tsx` |
| Memory page link | `apps/desktop/src/pages/memory/Memory.tsx` |
| Atlas page | new `apps/desktop/src/pages/memory/MemoryGraph.tsx` |
| Atlas components | new `apps/desktop/src/components/memory-atlas/` |
| Atlas component tests, if available | new or existing desktop test path under `apps/desktop/tests/` |
| Tickets | `tickets/memory-atlas-graph/` |

## Data Contracts

The atlas canvas endpoint should return drawing data only:

```ts
export interface MemoryAtlasNode {
  id: number;
  name: string;
  type: string;
  description: string | null;
  mentions: number;
  createdAt: string | null;
  updatedAt: string | null;
  firstObservedAt: string | null;
  lastObservedAt: string | null;
}

export interface MemoryAtlasEdge {
  id: number;
  sourceId: number;
  targetId: number;
  type: string;
  mentions: number;
  confidence: number;
  status: string;
  observedAt: string | null;
  validFrom: string | null;
  validTo: string | null;
  createdAt: string | null;
  updatedAt: string | null;
}

export interface MemoryAtlasGraphData {
  nodes: MemoryAtlasNode[];
  edges: MemoryAtlasEdge[];
  stats: {
    totalNodes: number;
    totalEdges: number;
    entityTypes: Record<string, number>;
    relationTypes: Record<string, number>;
    earliestObservedAt: string | null;
    latestObservedAt: string | null;
    capped: boolean;
  };
}
```

Detail reads can return source snippets after selection:

```ts
export interface MemoryAtlasEvidenceSnippet {
  id: number;
  kind: "memory_item" | "memory_item_evidence" | "relation_evidence";
  text: string;
  observedAt: string | null;
  confidence: number | null;
  sourceMemoryId: number | null;
}
```

Do not decrypt and ship every evidence row in the first canvas payload.

## Execution Order

### Task 1: Backend Atlas Canvas Payload

**Files:**
- Modify: `apps/server/src/anima_server/api/routes/graph.py`
- Test: `apps/server/tests/test_memory_atlas_graph_api.py`

- [ ] **Step 1: Write failing API tests for the atlas canvas endpoint**

Create tests for a route such as `GET /api/graph/{user_id}/atlas`.

Example assertions:

```python
def test_memory_atlas_returns_active_relations_only(client, unlocked_user, db_session):
    response = client.get(f"/api/graph/{unlocked_user.id}/atlas", headers=unlock_headers())
    assert response.status_code == 200
    payload = response.json()
    assert payload["nodes"]
    assert all(edge["status"] == "active" for edge in payload["edges"])
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_memory_atlas_graph_api.py -q
```

Expected: FAIL because the atlas endpoint does not exist.

- [ ] **Step 3: Implement the atlas endpoint**

Add a route that:

- calls `require_unlocked_user(request, user_id)`
- loads `KGEntity` rows for the user
- loads active `KGRelation` rows for the user
- supports `limit`, `entity_type`, `relation_type`, and `q` query parameters
- returns node/edge/stats payload
- includes `capped: true` if the result was reduced by limit

- [ ] **Step 4: Implement timestamp fallback**

Compute per-node `firstObservedAt` and `lastObservedAt` from entity timestamps. Compute edge observed time using:

1. `KGRelation.observed_at`
2. `KGRelation.valid_from`
3. `KGRelation.updated_at`
4. `KGRelation.created_at`
5. source/target entity timestamps

- [ ] **Step 5: Re-run focused backend tests**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_memory_atlas_graph_api.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add apps/server/src/anima_server/api/routes/graph.py apps/server/tests/test_memory_atlas_graph_api.py
git commit -m "graph: add memory atlas payload"
```

### Task 2: Backend Detail And Evidence Reads

**Files:**
- Modify: `apps/server/src/anima_server/api/routes/graph.py`
- Test: `apps/server/tests/test_memory_atlas_graph_api.py`

- [ ] **Step 1: Write failing tests for relation detail and evidence snippets**

Cover:

- relation detail returns source and target entities
- relation detail includes confidence, status, temporal metadata, mentions
- evidence snippets are bounded and user-scoped
- evidence rows are not included in the atlas canvas payload

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_memory_atlas_graph_api.py -q
```

Expected: FAIL on missing relation detail/evidence behavior.

- [ ] **Step 3: Implement relation detail route**

Add a focused route such as:

```text
GET /api/graph/{user_id}/relations/{relation_id}
```

The route should return relation metadata, source/target entity summaries, and optional source memory/evidence references.

- [ ] **Step 4: Add bounded evidence snippet loading**

If `KGRelation.evidence_id` or `source_memory_id` is present, load and decrypt only the selected relation's relevant rows. Use existing `df(...)` patterns with the correct table/field names.

- [ ] **Step 5: Re-run backend tests**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_memory_atlas_graph_api.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add apps/server/src/anima_server/api/routes/graph.py apps/server/tests/test_memory_atlas_graph_api.py
git commit -m "graph: add atlas detail reads"
```

### Task 3: API Client Contract

**Files:**
- Modify: `packages/api-client/src/types.ts`
- Modify: `packages/api-client/src/client.ts`
- Test: existing API client test path, if present

- [ ] **Step 1: Add failing API client test or type assertion**

If the package has client tests, add coverage for:

```ts
api.graph.atlas(userId, { limit: 100 })
api.graph.relation(userId, relationId)
```

- [ ] **Step 2: Run API client tests or typecheck**

Run:

```powershell
bun test packages/api-client/tests/client.test.ts
```

If that path does not exist or is not applicable, run the desktop/client typecheck through:

```powershell
bun run build:desktop
```

Expected: FAIL until the new methods/types exist.

- [ ] **Step 3: Add TypeScript interfaces**

Add `MemoryAtlasNode`, `MemoryAtlasEdge`, `MemoryAtlasGraphData`, `MemoryAtlasRelationDetail`, and `MemoryAtlasEvidenceSnippet`.

- [ ] **Step 4: Add API client methods**

Add methods under `graph`:

```ts
atlas(userId, options)
relation(userId, relationId)
```

Serialize optional query params with `URLSearchParams`.

- [ ] **Step 5: Run client validation**

Run:

```powershell
bun run build:desktop
```

Expected: PASS, or only pre-existing unrelated failures documented in the ticket.

- [ ] **Step 6: Commit**

```powershell
git add packages/api-client/src/types.ts packages/api-client/src/client.ts
git commit -m "api-client: add memory atlas graph contract"
```

### Task 4: Desktop Memory Atlas Route And Shell

**Files:**
- Modify: `apps/desktop/src/App.tsx`
- Modify: `apps/desktop/src/pages/memory/Memory.tsx`
- Create: `apps/desktop/src/pages/memory/MemoryGraph.tsx`
- Create: `apps/desktop/src/components/memory-atlas/index.ts`
- Create: `apps/desktop/src/components/memory-atlas/types.ts`
- Create: `apps/desktop/src/components/memory-atlas/AtlasShell.tsx`
- Create: `apps/desktop/src/components/memory-atlas/AtlasSidebar.tsx`
- Create: `apps/desktop/src/components/memory-atlas/AtlasInspector.tsx`

- [ ] **Step 1: Create the page shell with placeholder states**

Build the route with:

- header
- left filter rail
- central canvas placeholder
- right inspector placeholder
- loading state
- empty state
- error state

- [ ] **Step 2: Add route and Memory page link**

Add:

```tsx
<Route path="/memory/graph" element={withLayout(<MemoryGraph />)} />
```

Add a `Graph` or `Atlas` link on `Memory.tsx` near the existing Images link.

- [ ] **Step 3: Fetch atlas payload**

Use `api.graph.atlas(user.id, options)` in `MemoryGraph.tsx`. Keep fetch state local for v1 unless an existing graph state pattern is clearly reusable.

- [ ] **Step 4: Run desktop build/typecheck**

Run:

```powershell
bun run build:desktop
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add apps/desktop/src/App.tsx apps/desktop/src/pages/memory/Memory.tsx apps/desktop/src/pages/memory/MemoryGraph.tsx apps/desktop/src/components/memory-atlas
git commit -m "desktop: add memory atlas route"
```

### Task 5: Timeline Bloom Canvas And Inspector

**Files:**
- Modify: `apps/desktop/src/pages/memory/MemoryGraph.tsx`
- Create/modify: `apps/desktop/src/components/memory-atlas/AtlasCanvas.tsx`
- Create/modify: `apps/desktop/src/components/memory-atlas/AtlasControls.tsx`
- Create/modify: `apps/desktop/src/components/memory-atlas/AtlasInspector.tsx`
- Create/modify: `apps/desktop/src/components/memory-atlas/layout.ts`
- Create/modify: `apps/desktop/src/components/memory-atlas/time.ts`

- [ ] **Step 1: Add deterministic layout helpers**

Create helpers that convert atlas nodes and edges into React Flow nodes/edges. The layout should be deterministic from ids and observed timestamps so reloads are stable.

- [ ] **Step 2: Render graph with `@xyflow/react`**

Use existing project styling patterns. Add typed node colors and relation edge styles. Keep dimensions stable so controls do not shift.

- [ ] **Step 3: Implement timeline filtering**

The scrubber should produce a selected timestamp. Entities/relations after that timestamp should be hidden or dimmed. Relations with `validTo` before selected timestamp should not appear as active.

- [ ] **Step 4: Implement search and filters**

Search should focus or highlight matching entities. Type and relation filters should dim or hide matching graph elements without refetching when local payload is enough.

- [ ] **Step 5: Implement selection inspector**

Node selection shows clean entity story and relations. Edge selection calls `api.graph.relation(...)` and shows clean relation story first, then evidence snippets.

- [ ] **Step 6: Run desktop validation**

Run:

```powershell
bun run build:desktop
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add apps/desktop/src/pages/memory/MemoryGraph.tsx apps/desktop/src/components/memory-atlas
git commit -m "desktop: render timeline memory atlas"
```

### Task 6: Visual Smoke, Docs, And Final Validation

**Files:**
- Modify: `docs/superpowers/plans/2026-07-05-memory-atlas-graph.md`
- Modify: `docs/superpowers/specs/2026-07-05-memory-atlas-graph-design.md` only if implementation changes the agreed design
- Modify: `tickets/memory-atlas-graph/`
- Optional docs: `docs/architecture/memory/memory-system.md`

- [ ] **Step 1: Run focused backend validation**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_memory_atlas_graph_api.py -q
```

Expected: PASS.

- [ ] **Step 2: Run frontend validation**

Run:

```powershell
bun run build:desktop
```

Expected: PASS.

- [ ] **Step 3: Run full repo validation if the worktree is clean enough**

Run:

```powershell
git diff --check
bun run lint
bun run build
```

Expected: PASS, or exact unrelated failures documented in the ticket.

- [ ] **Step 4: Perform visual smoke**

Start the desktop web app or dev server and verify:

- `/memory/graph` loads
- empty state renders if no graph data exists
- seeded/non-empty graph renders visible nodes and edges
- timeline scrubber changes node/edge visibility or dimming
- node selection opens inspector
- edge selection opens relation detail/evidence inspector

- [ ] **Step 5: Update ticket validation**

Record commands, changed paths, screenshots or visual validation notes, and any residual risks in the active ticket and parent tracker.

- [ ] **Step 6: Commit final docs/ticket updates**

```powershell
git add docs/superpowers/plans/2026-07-05-memory-atlas-graph.md tickets/memory-atlas-graph
git commit -m "docs: record memory atlas validation"
```

## Milestones

| Milestone | Delivers | Stop condition |
| --- | --- | --- |
| M1 | Atlas backend payload | User-scoped active entity/relation graph data available in one request |
| M2 | Detail reads | Selected relations can load source/target details and bounded evidence |
| M3 | Typed client | Desktop can call atlas/detail APIs through `@anima/api-client` |
| M4 | Route shell | `/memory/graph` exists, links from Memory, and handles loading/empty/error states |
| M5 | Living canvas | Timeline Bloom, filters, search, and inspector work together |
| M6 | Validation | Focused backend, desktop, visual smoke, and ticket records are complete |

## Test Strategy

- Backend API tests for user scoping, active relation filtering, timestamp fallback, caps, filters, and detail/evidence bounds.
- API client tests or desktop build checks for new types and methods.
- Desktop typecheck/build for route and component integration.
- Visual smoke with a real browser or desktop web server because graph layout failures can pass typecheck while rendering blank.
- Full `bun run lint` and `bun run build` before PR if unrelated dirty worktree state does not block meaningful validation.

## Verification Commands

Use focused commands during ticket execution, then final validation:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_memory_atlas_graph_api.py -q
bun run build:desktop
git diff --check
bun run lint
bun run build
```

## Commit Strategy

Use one commit per child ticket where practical:

- `graph: add memory atlas payload`
- `graph: add atlas detail reads`
- `api-client: add memory atlas graph contract`
- `desktop: add memory atlas route`
- `desktop: render timeline memory atlas`
- `docs: record memory atlas validation`

## Risks

| Risk | Mitigation |
| --- | --- |
| Canvas payload decrypts too much data | Keep evidence out of the initial payload and load snippets only after selection |
| Large graphs become unreadable | Cap payload, default to recent/high-mention nodes, and rely on search/filter focus |
| Timeline fields are sparse | Use explicit fallback order and test missing timestamp cases |
| Existing `/graph` behavior regresses | Add new atlas/detail routes instead of replacing existing overview/entities/search routes |
| Graph renders blank despite typecheck | Require visual smoke with actual `/memory/graph` rendering |
| Editing expectations creep into v1 | Keep the route read-only and move correction/forgetting to future governed tickets |

## Execution Handoff

Recommended execution mode: subagent-driven or inline plan execution from `MAG-001` through `MAG-006`, with review after each ticket. Start with `MAG-001` because every desktop surface depends on the backend atlas payload shape.
