# OKF-000 - OKF LLM Wiki Ingestion Parent Tracker

- Status: in_progress
- Priority: P1
- Scope: `apps/server`, `packages/api-client`, `apps/desktop`, `docs/architecture`
- Parent: none
- Depends on: none
- Owner: Codex
- PRD: none
- Plan: `docs/superpowers/plans/2026-07-06-okf-llm-wiki-ingestion.md`
- Created: 2026-07-06 23:23 MYT
- Updated: 2026-07-07 01:32 MYT
- Started: 2026-07-07 00:16 MYT
- Completed:

## Goal

Track the source-type-agnostic ingestion initiative that turns files, media, web captures, transcripts, and app exports into OKF-compatible, LLM-wiki-style knowledge bundles with citations back to raw evidence.

## Child Tickets

| Ticket | Title | Status | Depends on |
| --- | --- | --- | --- |
| `OKF-001` | Runtime source and concept schema | done | none |
| `OKF-002` | Source registry and adapter contract | done | `OKF-001` |
| `OKF-003` | OKF import and export | done | `OKF-001`, `OKF-002` |
| `OKF-004` | LLM-wiki compiler | done | `OKF-001`, `OKF-002` |
| `OKF-005` | PDF and image adapter bridges | done | `OKF-002`, `OKF-004` |
| `OKF-006` | Markdown, text, and web adapters | done | `OKF-002`, `OKF-004` |
| `OKF-007` | Knowledge retrieval and agent tool | done | `OKF-003`, `OKF-004`, `OKF-005`, `OKF-006` |
| `OKF-008` | Bundle linting and maintenance | done | `OKF-003`, `OKF-004` |
| `OKF-009` | API client and desktop knowledge library | done | `OKF-007`, `OKF-008` |
| `OKF-010` | Architecture docs and final validation | backlog | `OKF-001` through `OKF-009` |

## Deliverables

- Runtime source, artifact, span, concept, citation, link, and bundle-run schema.
- Source registry and adapter contract that supports every source type through normalized artifacts and spans.
- OKF-compatible import/export with permissive frontmatter handling.
- LLM-wiki compiler that maintains concept pages, links, citations, questions, decisions, and contradictions.
- Bridges from current PDF and image ingestion paths into the universal source model.
- Markdown, plain text, and web capture adapters.
- Retrieval over compiled concepts and raw evidence spans.
- Bundle linting and maintenance checks.
- API client and minimal desktop knowledge-library surface.
- Architecture docs and validation records.

## Acceptance

- Every child ticket is completed with validation recorded.
- Existing PDF document RAG and image annotation paths keep their current behavior while also syncing into the universal source/span model.
- OKF import/export round-trips concept pages, frontmatter, links, `index.md`, and `log.md`.
- Retrieval can search compiled knowledge and drill into citable source spans without promoting personal memory automatically.
- Final validation commands are recorded in `OKF-010`.

## Completed Tickets

- `OKF-001` - Runtime source and concept schema, completed 2026-07-07 00:26 MYT.
- `OKF-002` - Source registry and adapter contract, completed 2026-07-07 00:30 MYT.
- `OKF-003` - OKF import and export, completed 2026-07-07 00:33 MYT.
- `OKF-004` - LLM-wiki compiler, completed 2026-07-07 00:37 MYT.
- `OKF-005` - PDF and image adapter bridges, completed 2026-07-07 00:48 MYT.
- `OKF-006` - Markdown, text, and web adapters, completed 2026-07-07 00:57 MYT.
- `OKF-007` - Knowledge retrieval and agent tool, completed 2026-07-07 01:10 MYT.
- `OKF-008` - Bundle linting and maintenance, completed 2026-07-07 01:19 MYT.

## Activity Log

- 2026-07-06 23:23 MYT - Ticket created from `docs/superpowers/plans/2026-07-06-okf-llm-wiki-ingestion.md`.
- 2026-07-07 00:16 MYT - OKF-001 claimed by Codex and parent tracker moved to `in_progress`.
- 2026-07-07 00:26 MYT - OKF-001 completed with focused model tests and ruff validation.
- 2026-07-07 00:27 MYT - OKF-002 claimed by Codex.
- 2026-07-07 00:30 MYT - OKF-002 completed with focused adapter tests and ruff validation.
- 2026-07-07 00:31 MYT - OKF-003 claimed by Codex.
- 2026-07-07 00:33 MYT - OKF-003 completed with OKF round-trip tests and ruff validation.
- 2026-07-07 00:34 MYT - OKF-004 claimed by Codex.
- 2026-07-07 00:37 MYT - OKF-004 completed with fake-model compiler tests and ruff validation.
- 2026-07-07 00:40 MYT - OKF-005 claimed by Codex.
- 2026-07-07 00:48 MYT - OKF-005 completed with document/image bridge tests and existing RAG/indexing regressions.
- 2026-07-07 00:50 MYT - OKF-006 claimed by Codex.
- 2026-07-07 00:57 MYT - OKF-006 completed with adapter/API tests and ruff validation.
- 2026-07-07 00:59 MYT - OKF-007 claimed by Codex.
- 2026-07-07 01:10 MYT - OKF-007 completed with targeted retrieval/tool tests; broader agent suite has one unrelated context-pill assertion failure recorded in the child ticket.
- 2026-07-07 01:12 MYT - OKF-008 claimed by Codex.
- 2026-07-07 01:19 MYT - OKF-008 completed with lint service tests and ruff validation.
- 2026-07-07 01:20 MYT - OKF-009 claimed by Codex.
- 2026-07-07 01:32 MYT - OKF-009 completed with backend API, API client, desktop build, and focused route/client tests.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - Parent tracker only; child tickets define executable validation.
