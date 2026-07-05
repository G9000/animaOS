# Memory Atlas Graph Design

**Status:** Draft
**Date:** 2026-07-05
**Purpose:** Define a living, Obsidian-like memory graph viewer for the desktop app without changing memory governance or write semantics.

## Design Decision

Add a new `/memory/graph` Memory Atlas surface focused on living memory formation. Keep the existing `/graph` page as the lower-level technical knowledge graph inspector.

The first version visualizes durable knowledge graph entities and active relations as the main canvas. Memory items, episodes, and evidence do not become primary graph nodes in v1; they appear in the detail inspector for selected entities or relations.

## Product Surface

`/memory/graph` is a working surface, not a landing page. The first viewport is the atlas:

- left rail for search, entity type filters, relation filters, and visible layers
- center Timeline Bloom canvas
- bottom or integrated time scrubber
- right inspector for selected node or edge details

The Memory page links to this surface near the existing image memory entry point.

## Timeline Bloom Behavior

The graph is temporal first. The time scrubber represents observed memory time:

- entity `createdAt` / `updatedAt`
- relation `observedAt`
- relation `validFrom` / `validTo`
- relation `updatedAt` when observation fields are missing
- connected entity timestamps as final fallback

At the current-time position, the graph shows active memory as ANIMA currently understands it. Moving the scrubber backward dims or hides entities and relations that did not exist yet or were not valid at that point. Recent or changed nodes may glow subtly so memory formation is visible without making the graph hard to read.

## Architecture

Add a read-only Memory Atlas API payload, separate from the existing graph endpoints. The endpoint returns the data needed to render the canvas in one request:

- nodes: entity id, name, type, description, mentions, created/updated timestamps
- edges: relation id, source id, target id, relation type, mentions, confidence, status, observed/valid timestamps
- summary stats: total nodes, total edges, available entity types, available relation types, earliest/latest observed timestamps

The API remains scoped through the existing unlocked-user dependency and must only return data for the requested user.

The desktop consumes this payload through `@anima/api-client` and renders with the existing `@xyflow/react` dependency. Layout should be deterministic so the same graph does not jump randomly on every page load.

Keep canvas data and evidence data separate:

- the atlas payload is optimized for drawing and filtering the graph
- entity detail can reuse or extend the existing graph entity detail route
- relation detail should expose a focused relation read path with source/target entities, temporal metadata, confidence, and optional source memory/evidence references
- evidence snippets load only after selection so the first canvas render does not decrypt and ship every evidence row

## Detail Inspector

Selection opens the right inspector. The inspector prioritizes clean story first, then evidence:

1. entity or relation name/type
2. readable summary or description
3. first observed, last updated, mentions, confidence, and status
4. connected entities and relations
5. source memory or evidence snippets loaded on demand

Evidence should be directly available but not required for scanning the graph.

## Controls

V1 controls:

- search focus for matching entities
- entity type toggles: people, places, organizations, projects, concepts, unknown
- relation type filters
- timeline scrubber from earliest observed memory to now
- current-time / now reset
- selection inspector for nodes and edges

## Out Of Scope

V1 is read-only.

Do not add:

- relation editing
- direct graph-based forgetting
- evidence mutation
- manual node creation
- drag-position persistence
- episodes, memory items, source messages, or evidence as first-class graph nodes

Those features require memory governance, audit behavior, and forgetting semantics before they are safe.

## Empty, Large, And Degraded States

Empty graph:

- show a quiet atlas empty state
- explain that graph data appears after conversations or memory consolidation
- keep navigation and filters visible

Large graph:

- cap the initial payload
- default to recent and high-mention entities
- let search expand or focus the view
- avoid rendering unlimited nodes at once

Missing timestamps:

- prefer relation observation timestamps
- fall back to relation updated/created timestamps
- fall back to connected entity timestamps
- keep the scrubber functional even with partial temporal data

## Validation

Backend validation:

- atlas payload returns only current user data
- active relation filtering matches existing graph semantics
- timestamp fallback works for entities and relations
- payload cap and type/relation filters behave deterministically

Frontend validation:

- desktop typecheck/build
- `/memory/graph` renders a non-empty canvas with seeded data
- search focuses matching entities
- timeline scrubber changes visible or dimmed nodes/edges
- node and edge selection open the inspector
- empty graph state renders without errors

Visual validation should include a browser screenshot or equivalent desktop web smoke test, not only typecheck.
