# OKF LLM Wiki Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a source-type-agnostic ingestion layer that turns files, media, web captures, transcripts, and app exports into OKF-compatible, LLM-wiki-style knowledge bundles with citations back to raw evidence.

**Architecture:** Keep raw sources and extracted spans as runtime evidence, then compile them into maintained OKF-style concept pages that can be searched, linked, linted, imported, and exported. Existing PDF document ingestion and image annotation ingestion become source adapters under one ingestion contract instead of isolated pipelines. Personal long-term memory remains separate; ingestion may propose memory candidates, but only explicit approval promotes anything into SQLCipher memory.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic runtime migrations, existing runtime PostgreSQL/SQLite fallback, pgvector-compatible `RuntimeEmbedding`, current document and image services, local filesystem under `ANIMA_DATA_DIR`, LLM adapter interfaces, React/Vite/Tauri desktop, TypeScript API client, pytest, Bun/Nx validation.

---

## Source Material And Latest Baseline

Checked on 2026-07-06:

- Google Cloud blog: `Introducing the Open Knowledge Format`, published 2026-06-13.
- OKF upstream spec: `Open Knowledge Format (OKF)`, `Version 0.1 - Draft`.
- Karpathy gist: `LLM Wiki`, an idea file for agent-maintained knowledge bases.

Design implications:

- Treat OKF as a portable interchange format, not an internal database mandate.
- Follow the OKF bundle shape: markdown files, YAML frontmatter, one concept per file, bundle-relative links, optional `index.md`, optional `log.md`, and permissive consumers that ignore unknown fields.
- Preserve Karpathy's LLM-wiki pattern: sources are curated and retained; the agent maintains indexes, concept pages, schemas, links, summaries, lint results, and updates over time.
- Avoid source-type special cases in the core. PDF, images, markdown, webpages, transcripts, datasets, email/calendar exports, code repos, and future audio/video should all enter through adapters that emit the same normalized evidence model.

## Corrected Scope

This is not a PDF feature. PDF is one existing adapter.

This plan covers the umbrella ingestion architecture:

```text
any source
-> raw source registry
-> extractor adapter
-> normalized artifacts and spans
-> source chunk/span embeddings
-> OKF-style concepts
-> LLM-wiki links, claims, questions, decisions, and log entries
-> compiled-knowledge retrieval
-> raw-evidence drilldown
```

In scope:

- Source registry for every ingestible thing, not just documents.
- Normalized source artifacts and evidence spans with citations.
- OKF-compatible concept model and import/export.
- LLM-wiki compiler that creates and updates concept pages.
- Retrieval over compiled concepts first, then raw spans for evidence.
- Maintenance and linting for links, citations, stale pages, duplicate topics, and contradictions.
- Adapter bridge for existing PDF document ingestion.
- Adapter bridge for existing image/OCR/caption annotations.
- First new simple adapters for markdown/plain text and web captures.
- Architecture docs and API client contracts.

Out of scope for the first executable slice:

- Automatic promotion into long-term personal memory.
- Replacing `runtime_documents`, `runtime_document_chunks`, `runtime_image_assets`, or `runtime_image_annotations`.
- Full audio/video transcription implementation.
- Cloud sync, collaborative editing, or remote OKF catalog publishing.
- A full desktop knowledge editor. V1 can expose read/search/export/import endpoints and a minimal viewer later.

## Core Concepts

### Raw Source

A raw source is the thing the user gave Anima or Anima captured through a connector:

- PDF
- Office document
- Markdown or plain text file
- Web page or clipped article
- Image with OCR/caption annotations
- Transcript
- Audio/video with future timecoded derivatives
- CSV/JSON/table dataset
- Email/calendar/mod export
- Code repository snapshot

### Artifact

An artifact is extracted material derived from a source:

- text body
- page text
- OCR text
- image caption
- transcript segment
- table schema
- code file content
- metadata block

Artifacts are rebuildable from raw sources where possible.

### Span

A span is a citable unit of evidence:

- page range in a document
- character range in text
- timestamp range in audio/video/transcript
- row range or cell address in a dataset
- image annotation id
- code file path and line range

Spans are what retrieval and citations should point to.

### Concept

A concept is the OKF/LLM-wiki compiled knowledge page. Concepts are derived, maintained, and rebuildable. They can represent:

- source summary
- topic
- entity
- project
- claim
- decision
- relationship
- open question
- contradiction
- procedure
- dataset
- system/component
- note
- log entry

## Data Model Direction

Add runtime tables for the universal ingestion layer. Do not move existing document/image data immediately; bridge it through adapters first.

| Table | Purpose |
| --- | --- |
| `runtime_sources` | One source registry row for every ingested file, page, media asset, connector export, repo snapshot, or external bundle |
| `runtime_source_artifacts` | Extracted artifacts derived from a source, such as page text, OCR text, captions, transcript text, table metadata, or code file content |
| `runtime_source_spans` | Citable ranges inside artifacts, with page/time/line/row metadata and content hashes |
| `runtime_knowledge_concepts` | OKF-style compiled concept pages with frontmatter, body markdown, type, slug, title, status, and hash |
| `runtime_knowledge_concept_sources` | Citation links from concepts to source spans |
| `runtime_knowledge_links` | Typed links between concepts, including `mentions`, `supports`, `contradicts`, `depends_on`, `updates`, and `related` |
| `runtime_knowledge_bundle_runs` | Compiler/import/export/lint run records for audit, retry, and debugging |

Reuse `RuntimeEmbedding` for both source spans and compiled concepts:

```text
source_type = "source_span"
source_type = "knowledge_concept"
```

Keep existing source types:

```text
source_type = "document_chunk"
source_type = "image_annotation"
source_type = "memory_item"
source_type = "episode"
source_type = "entity"
```

Existing `document_chunk` and `image_annotation` embeddings should continue to work while the new source-span index is introduced.

## OKF Internal Contract

Internal concept rows should map cleanly to OKF markdown files:

```yaml
---
type: topic
title: Runtime ingestion architecture
description: Source-type-agnostic ingestion model for AnimaOS
tags:
  - ingestion
  - architecture
timestamp: "2026-07-06T00:00:00+08:00"
anima:
  status: active
  source_count: 4
  content_hash: "<sha256>"
---
```

Rules:

- Preserve `type` as required for OKF export.
- Accept and preserve unknown frontmatter fields during import.
- Generate stable slugs from title/type, with collision handling.
- Use normal markdown links between concept files.
- Keep citations as structured source links internally, then render them as markdown footnotes or reference sections on export.
- Write `index.md` and `log.md` on export.
- Import must be permissive: unknown concept types and unknown frontmatter fields are not fatal.

## Retrieval Contract

Use two-stage retrieval:

1. Search compiled concepts for understanding and navigation.
2. Search source spans for precise evidence and citations.

The agent should prefer compiled concepts for broad questions like:

- "What do we know about this project?"
- "What did these files say overall?"
- "What changed since last import?"
- "What contradictions are in this source set?"

The agent should drill into spans for:

- direct quotations
- page/time/line-specific claims
- disputed facts
- citations shown to the user

Do not rely on English keyword routing. Use source type, span kind, concept type, query embedding, link graph, and user-selected scope.

## File Map

| Area | Files |
| --- | --- |
| Runtime models | `apps/server/src/anima_server/models/runtime.py`, `apps/server/src/anima_server/models/__init__.py` |
| Runtime migration | next new file under `apps/server/alembic_runtime/versions/` generated by `bun run db:server:revision -- "add source knowledge ingestion"` |
| Ingestion package | new `apps/server/src/anima_server/services/ingestion/` |
| Ingestion dataclasses | new `apps/server/src/anima_server/services/ingestion/models.py` |
| Source registry | new `apps/server/src/anima_server/services/ingestion/sources.py` |
| Artifact/span storage | new `apps/server/src/anima_server/services/ingestion/artifacts.py` |
| Adapter contract | new `apps/server/src/anima_server/services/ingestion/adapters/base.py` |
| Existing PDF adapter bridge | new `apps/server/src/anima_server/services/ingestion/adapters/documents.py` |
| Existing image adapter bridge | new `apps/server/src/anima_server/services/ingestion/adapters/images.py` |
| Markdown/text adapter | new `apps/server/src/anima_server/services/ingestion/adapters/text.py` |
| Web capture adapter | new `apps/server/src/anima_server/services/ingestion/adapters/web.py` |
| OKF import/export | new `apps/server/src/anima_server/services/ingestion/okf.py` |
| LLM-wiki compiler | new `apps/server/src/anima_server/services/ingestion/compiler.py` |
| Concept retrieval | new `apps/server/src/anima_server/services/ingestion/retrieval.py` |
| Bundle linting | new `apps/server/src/anima_server/services/ingestion/lint.py` |
| API schemas | new `apps/server/src/anima_server/schemas/knowledge.py` |
| API routes | new `apps/server/src/anima_server/api/routes/knowledge.py`, modify `apps/server/src/anima_server/main.py` |
| Existing document bridge | `apps/server/src/anima_server/services/documents/`, `apps/server/src/anima_server/api/routes/documents.py` |
| Existing image bridge | `apps/server/src/anima_server/services/images/`, `apps/server/src/anima_server/api/routes/images.py` |
| Agent retrieval integration | `apps/server/src/anima_server/services/agent/service.py`, `apps/server/src/anima_server/services/agent/tools.py`, `apps/server/src/anima_server/services/agent/memory_blocks.py` |
| API client | `packages/api-client/src/types.ts`, `packages/api-client/src/client.ts` |
| Desktop minimal viewer | later files under `apps/desktop/src/pages/knowledge/` and `apps/desktop/src/App.tsx` |
| Architecture docs | `docs/architecture/agent/document-processing.md`, new `docs/architecture/agent/source-ingestion.md`, `docs/architecture/memory/memory-system.md` |
| Tests | new `apps/server/tests/test_source_ingestion_models.py`, `test_source_ingestion_adapters.py`, `test_okf_import_export.py`, `test_llm_wiki_compiler.py`, `test_knowledge_retrieval.py`, `test_knowledge_api.py` |

## Execution Order

### Task 1: Runtime Source And Concept Schema

**Files:**
- Modify: `apps/server/src/anima_server/models/runtime.py`
- Modify: `apps/server/src/anima_server/models/__init__.py`
- Create: next runtime migration under `apps/server/alembic_runtime/versions/`
- Test: `apps/server/tests/test_source_ingestion_models.py`

- [ ] **Step 1: Write failing model tests**

Cover:

- inserting one `RuntimeSource`
- inserting multiple `RuntimeSourceArtifact` rows
- inserting citable `RuntimeSourceSpan` rows
- inserting one `RuntimeKnowledgeConcept`
- linking concept to spans through `RuntimeKnowledgeConceptSource`
- linking concepts through `RuntimeKnowledgeLink`
- deleting a source cascades only derived source artifacts/spans, not concepts unless explicitly recompiled

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_source_ingestion_models.py -q
```

Expected: FAIL because the runtime source and concept models do not exist.

- [ ] **Step 3: Add SQLAlchemy models**

Add models after the existing document/image runtime models:

```python
class RuntimeSource(RuntimeBase): ...
class RuntimeSourceArtifact(RuntimeBase): ...
class RuntimeSourceSpan(RuntimeBase): ...
class RuntimeKnowledgeConcept(RuntimeBase): ...
class RuntimeKnowledgeConceptSource(RuntimeBase): ...
class RuntimeKnowledgeLink(RuntimeBase): ...
class RuntimeKnowledgeBundleRun(RuntimeBase): ...
```

Use `user_id` on every table. Use composite foreign keys that include `user_id` where rows are user-owned.

- [ ] **Step 4: Generate the runtime migration**

Run:

```powershell
bun run db:server:revision -- "add source knowledge ingestion"
```

Expected: a new runtime migration is generated under `apps/server/alembic_runtime/versions/`.

- [ ] **Step 5: Review indexes and constraints manually**

Required constraints:

```text
uq_runtime_sources_user_kind_uri_hash
uq_runtime_source_artifacts_source_kind_hash
uq_runtime_source_spans_artifact_locator_hash
uq_runtime_knowledge_concepts_user_slug
uq_runtime_knowledge_links_user_source_target_type
```

Required indexes:

```text
ix_runtime_sources_user_kind_status
ix_runtime_source_spans_user_source
ix_runtime_knowledge_concepts_user_type_status
ix_runtime_knowledge_links_user_source
ix_runtime_knowledge_links_user_target
```

- [ ] **Step 6: Run model tests**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_source_ingestion_models.py -q
```

Expected: PASS.

### Task 2: Source Registry And Adapter Contract

**Files:**
- Create: `apps/server/src/anima_server/services/ingestion/__init__.py`
- Create: `apps/server/src/anima_server/services/ingestion/models.py`
- Create: `apps/server/src/anima_server/services/ingestion/sources.py`
- Create: `apps/server/src/anima_server/services/ingestion/artifacts.py`
- Create: `apps/server/src/anima_server/services/ingestion/adapters/__init__.py`
- Create: `apps/server/src/anima_server/services/ingestion/adapters/base.py`
- Test: `apps/server/tests/test_source_ingestion_adapters.py`

- [ ] **Step 1: Write failing adapter contract tests**

Cover:

- registering a source is idempotent by user and content/URI hash
- adapters return normalized artifacts and spans
- span locators can represent page, time, line, row, cell, and image annotation references
- adapter failures create a failed bundle run instead of half-writing spans

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_source_ingestion_adapters.py -q
```

Expected: FAIL because the ingestion package does not exist.

- [ ] **Step 3: Add dataclasses**

Define:

```python
SourceIdentity
SourceArtifactInput
SourceSpanInput
IngestionAdapterResult
IngestionAdapter
```

The result must be source-type agnostic:

```python
@dataclass(frozen=True, slots=True)
class SourceSpanInput:
    artifact_kind: str
    locator_json: dict[str, object]
    content_text: str
    content_hash: str
    metadata_json: dict[str, object] | None = None
```

- [ ] **Step 4: Implement registry helpers**

Implement helpers to:

- create or reuse `RuntimeSource`
- replace artifacts/spans for a source
- preserve source status transitions: `registered`, `extracting`, `indexed`, `failed`, `deleted`
- write `RuntimeKnowledgeBundleRun` records for adapter runs

- [ ] **Step 5: Run adapter contract tests**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_source_ingestion_adapters.py -q
```

Expected: PASS.

### Task 3: OKF Import And Export

**Files:**
- Create: `apps/server/src/anima_server/services/ingestion/okf.py`
- Test: `apps/server/tests/test_okf_import_export.py`

- [ ] **Step 1: Write failing OKF tests**

Cover:

- export writes one markdown file per concept
- export writes `index.md`
- export writes `log.md`
- every exported concept has `type` frontmatter
- optional OKF fields round-trip: `title`, `description`, `resource`, `tags`, `timestamp`
- unknown frontmatter fields survive import/export
- unknown concept types do not fail import
- bundle-relative markdown links are preserved

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_okf_import_export.py -q
```

Expected: FAIL because OKF import/export does not exist.

- [ ] **Step 3: Implement markdown/frontmatter serializer**

Use a structured YAML parser if already available in the server dependency set. If not, add the smallest dependency that supports safe YAML load/dump, or write a limited parser only after confirming dependency constraints.

- [ ] **Step 4: Implement export**

Export layout:

```text
bundle/
  index.md
  log.md
  concepts/
    <slug>.md
```

Do not export raw source files in v1. Export source references and citation metadata.

- [ ] **Step 5: Implement import**

Import should:

- parse all markdown files except ignored internal files
- accept `index.md` and `log.md`
- create or update `RuntimeKnowledgeConcept` rows by slug
- preserve unknown fields in `metadata_json`
- create concept links from markdown links when targets resolve

- [ ] **Step 6: Run OKF tests**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_okf_import_export.py -q
```

Expected: PASS.

### Task 4: LLM-Wiki Compiler

**Files:**
- Create: `apps/server/src/anima_server/services/ingestion/compiler.py`
- Create: `apps/server/src/anima_server/services/ingestion/prompts/compile_source.md.j2`
- Create: `apps/server/src/anima_server/services/ingestion/prompts/merge_concepts.md.j2`
- Create: `apps/server/src/anima_server/services/ingestion/prompts/detect_links.md.j2`
- Test: `apps/server/tests/test_llm_wiki_compiler.py`

- [ ] **Step 1: Write failing compiler tests with fake model output**

Cover:

- compiler creates a source summary concept
- compiler creates topic/entity/claim/question/decision concepts from spans
- compiler links concepts to source spans
- compiler creates concept-to-concept links
- compiler updates existing concepts instead of duplicating by title/slug
- malformed model JSON records a failed bundle run and does not corrupt existing concepts

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_llm_wiki_compiler.py -q
```

Expected: FAIL because the compiler does not exist.

- [ ] **Step 3: Define compiler input**

The compiler receives:

```python
source_id: int
span_ids: Sequence[int]
mode: Literal["initial", "refresh", "repair"]
selected_concept_ids: Sequence[int] | None
```

- [ ] **Step 4: Define compiler output schema**

Use strict JSON from the model:

```json
{
  "concepts": [
    {
      "type": "topic",
      "title": "Example",
      "slug": "topic-example",
      "description": "Short summary",
      "body_markdown": "Compiled notes with citations",
      "source_span_ids": [1, 2],
      "tags": ["example"]
    }
  ],
  "links": [
    {
      "source_slug": "topic-example",
      "target_slug": "source-example",
      "link_type": "supports",
      "confidence": 0.8
    }
  ]
}
```

- [ ] **Step 5: Implement deterministic merge rules**

Before model merge:

- exact slug match updates the same concept
- same source summary concept updates the existing source concept
- same normalized title and type updates only when confidence is high

After model merge:

- write concept body and frontmatter hash
- replace concept-source links for that compiler run
- upsert concept links
- record bundle run result

- [ ] **Step 6: Run compiler tests**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_llm_wiki_compiler.py -q
```

Expected: PASS.

### Task 5: Fold Existing PDF And Image Paths Into Source Adapters

**Files:**
- Create: `apps/server/src/anima_server/services/ingestion/adapters/documents.py`
- Create: `apps/server/src/anima_server/services/ingestion/adapters/images.py`
- Modify: `apps/server/src/anima_server/services/documents/pdf_workflow.py`
- Modify: `apps/server/src/anima_server/services/images/indexing.py`
- Test: `apps/server/tests/test_source_ingestion_adapters.py`
- Test: `apps/server/tests/test_document_rag.py`
- Test: `apps/server/tests/test_image_indexing.py`

- [ ] **Step 1: Write adapter bridge tests**

Cover:

- indexed `RuntimeDocument` rows can be represented as `RuntimeSource`
- `RuntimeDocumentChunk` rows can be represented as source spans with page locators
- `RuntimeImageAsset` rows can be represented as `RuntimeSource`
- `RuntimeImageAnnotation` rows can be represented as source spans with image annotation locators
- old document/image RAG tests still pass

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_source_ingestion_adapters.py apps/server/tests/test_document_rag.py apps/server/tests/test_image_indexing.py -q
```

Expected: FAIL only for new adapter expectations.

- [ ] **Step 3: Implement document adapter bridge**

Map:

```text
RuntimeDocument -> RuntimeSource(kind="document", media_type=document.mime_type)
RuntimeDocumentChunk -> RuntimeSourceSpan(locator_json={"page_start": ..., "page_end": ..., "chunk_id": ...})
```

- [ ] **Step 4: Implement image adapter bridge**

Map:

```text
RuntimeImageAsset -> RuntimeSource(kind="image", media_type=asset.mime_type)
RuntimeImageAnnotation -> RuntimeSourceSpan(locator_json={"image_asset_id": ..., "annotation_id": ..., "annotation_kind": ...})
```

- [ ] **Step 5: Hook bridges into existing workflows**

After document indexing or image annotation indexing completes, call the adapter bridge to sync source/spans and optionally queue a compiler run.

- [ ] **Step 6: Run bridge and regression tests**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_source_ingestion_adapters.py apps/server/tests/test_document_rag.py apps/server/tests/test_image_indexing.py -q
```

Expected: PASS.

### Task 6: Add Markdown, Plain Text, And Web Capture Adapters

**Files:**
- Create: `apps/server/src/anima_server/services/ingestion/adapters/text.py`
- Create: `apps/server/src/anima_server/services/ingestion/adapters/web.py`
- Create or modify: `apps/server/src/anima_server/api/routes/knowledge.py`
- Test: `apps/server/tests/test_source_ingestion_adapters.py`
- Test: `apps/server/tests/test_knowledge_api.py`

- [ ] **Step 1: Write failing adapter/API tests**

Cover:

- ingest raw markdown string
- ingest raw plain text file bytes
- ingest web capture from URL plus fetched HTML/text payload supplied by caller or connector
- sanitize file names and URLs
- create source, artifacts, spans, embeddings, and compiler run records

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_source_ingestion_adapters.py apps/server/tests/test_knowledge_api.py -q
```

Expected: FAIL for missing adapters/routes.

- [ ] **Step 3: Implement markdown/plain text adapter**

The adapter should:

- preserve headings in metadata
- split into spans by heading/paragraph
- compute content hashes
- reject empty content

- [ ] **Step 4: Implement web capture adapter**

The adapter should:

- store the source URL
- accept extracted readable text from a connector or local extractor
- preserve canonical URL/title metadata when provided
- avoid live network fetching inside tests

- [ ] **Step 5: Add API endpoints**

Add endpoints under `apps/server/src/anima_server/api/routes/knowledge.py`:

```text
POST /api/knowledge/sources/text
POST /api/knowledge/sources/markdown
POST /api/knowledge/sources/web-capture
GET /api/knowledge/sources/{source_id}
GET /api/knowledge/concepts/{concept_id}
POST /api/knowledge/sources/{source_id}/compile
```

- [ ] **Step 6: Run tests**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_source_ingestion_adapters.py apps/server/tests/test_knowledge_api.py -q
```

Expected: PASS.

### Task 7: Knowledge Retrieval Over Concepts And Source Spans

**Files:**
- Create: `apps/server/src/anima_server/services/ingestion/retrieval.py`
- Modify: `apps/server/src/anima_server/services/agent/tools.py`
- Modify: `apps/server/src/anima_server/services/agent/service.py`
- Test: `apps/server/tests/test_knowledge_retrieval.py`
- Test: `apps/server/tests/test_agent_service.py`

- [ ] **Step 1: Write failing retrieval tests**

Cover:

- concept search returns compiled concepts by query
- source span search returns evidence spans by query
- combined search ranks concepts first for broad questions
- combined search includes source-span citations for evidence-heavy questions
- user isolation
- no English keyword heuristics

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_knowledge_retrieval.py apps/server/tests/test_agent_service.py -q
```

Expected: FAIL for missing retrieval path/tool integration.

- [ ] **Step 3: Implement concept embedding upsert**

Index `RuntimeKnowledgeConcept.body_markdown` and title/description text with:

```text
RuntimeEmbedding.source_type = "knowledge_concept"
category = "knowledge"
```

- [ ] **Step 4: Implement source span embedding upsert**

Index normalized source spans with:

```text
RuntimeEmbedding.source_type = "source_span"
category = "source"
```

Do not delete or replace existing `document_chunk` and `image_annotation` embeddings in this task.

- [ ] **Step 5: Implement retrieval API**

Return:

```python
KnowledgeRetrievalResult(
    concepts=[...],
    evidence_spans=[...],
    links=[...],
)
```

- [ ] **Step 6: Add agent tool**

Add a bounded tool such as:

```text
search_knowledge_bundle(query, scope=None, mode="balanced")
```

The tool returns compiled concept summaries plus evidence refs. It should not promote memory.

- [ ] **Step 7: Run retrieval tests**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_knowledge_retrieval.py apps/server/tests/test_agent_service.py -q
```

Expected: PASS.

### Task 8: Bundle Linting And Maintenance

**Files:**
- Create: `apps/server/src/anima_server/services/ingestion/lint.py`
- Modify: `apps/server/src/anima_server/api/routes/knowledge.py`
- Test: `apps/server/tests/test_okf_import_export.py`
- Test: `apps/server/tests/test_llm_wiki_compiler.py`

- [ ] **Step 1: Write failing lint tests**

Cover:

- broken concept links
- uncited claims
- concept pages with no source links
- duplicated concept slugs/titles
- stale concept hash after source span changes
- contradictions represented as links rather than silently merged
- orphan sources with no compiled concept

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_okf_import_export.py apps/server/tests/test_llm_wiki_compiler.py -q
```

Expected: FAIL for missing lint behavior.

- [ ] **Step 3: Implement lint rules**

Return structured findings:

```python
KnowledgeLintFinding(
    code="uncited_claim",
    severity="warning",
    concept_id=...,
    source_id=...,
    message="..."
)
```

- [ ] **Step 4: Add lint endpoint**

Add:

```text
POST /api/knowledge/lint
```

It should support user scope, source scope, and concept scope.

- [ ] **Step 5: Run lint tests**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_okf_import_export.py apps/server/tests/test_llm_wiki_compiler.py -q
```

Expected: PASS.

### Task 9: API Client And Minimal Desktop Knowledge Surface

**Files:**
- Modify: `packages/api-client/src/types.ts`
- Modify: `packages/api-client/src/client.ts`
- Modify: `apps/desktop/src/App.tsx`
- Create: `apps/desktop/src/pages/knowledge/KnowledgeLibrary.tsx`
- Create: `apps/desktop/src/components/knowledge/KnowledgeConceptViewer.tsx`
- Create: `apps/desktop/src/components/knowledge/KnowledgeSourceList.tsx`
- Test: existing desktop/API client tests where applicable

- [ ] **Step 1: Add failing client type tests or build expectations**

Cover API calls for:

```text
list sources
read source
list concepts
read concept
compile source
export OKF bundle
import OKF bundle
run lint
```

- [ ] **Step 2: Run typecheck/build and verify failure**

Run:

```powershell
bun run build:desktop
```

Expected: FAIL until types/client methods exist.

- [ ] **Step 3: Add API client types and methods**

Add typed payloads for sources, artifacts, spans, concepts, links, lint findings, and OKF export/import results.

- [ ] **Step 4: Add minimal desktop viewer**

The first UI should be a working library surface, not a marketing page:

- source list
- concept list
- concept markdown body
- source citations
- run lint button
- export OKF button

- [ ] **Step 5: Run desktop validation**

Run:

```powershell
bun run build:desktop
```

Expected: PASS.

### Task 10: Documentation, Tickets, And Final Validation

**Files:**
- Create: `docs/architecture/agent/source-ingestion.md`
- Modify: `docs/architecture/README.md`
- Modify: `docs/architecture/agent/document-processing.md`
- Modify: `docs/architecture/memory/memory-system.md`
- Create: `tickets/okf-llm-wiki-ingestion/OKF-000-parent.md`
- Create child tickets under `tickets/okf-llm-wiki-ingestion/`

- [ ] **Step 1: Write architecture docs**

Document:

- source registry
- artifact/span model
- source adapters
- OKF concept model
- import/export behavior
- retrieval contract
- memory boundary
- lint/maintenance behavior
- extension path for transcripts, Office docs, datasets, code repos, email/calendar exports, and future audio/video

- [ ] **Step 2: Create parent and child tickets**

Suggested tickets:

```text
OKF-000 parent tracker
OKF-001 runtime source and concept schema
OKF-002 ingestion adapter contract
OKF-003 OKF import/export
OKF-004 LLM-wiki compiler
OKF-005 PDF and image adapter bridges
OKF-006 markdown/text/web adapters
OKF-007 knowledge retrieval and agent tool
OKF-008 linting and maintenance
OKF-009 API client and desktop library
OKF-010 docs and final validation
```

- [ ] **Step 3: Run focused backend validation**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_source_ingestion_models.py apps/server/tests/test_source_ingestion_adapters.py apps/server/tests/test_okf_import_export.py apps/server/tests/test_llm_wiki_compiler.py apps/server/tests/test_knowledge_retrieval.py apps/server/tests/test_knowledge_api.py -q
```

Expected: PASS.

- [ ] **Step 4: Run regression tests for existing ingestion paths**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_document_rag.py apps/server/tests/test_documents_api.py apps/server/tests/test_image_indexing.py apps/server/tests/test_image_retrieval_context.py -q
```

Expected: PASS.

- [ ] **Step 5: Run final validation**

Run:

```powershell
git diff --check
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test
bun run lint
bun run build
bun run db:server:current
```

Expected: PASS, or exact unrelated dirty-worktree failures recorded in the active ticket.

## Milestones

| Milestone | Delivers | Stop condition |
| --- | --- | --- |
| M1 | Runtime source/concept schema | Sources, artifacts, spans, concepts, citations, links, and run records can be stored |
| M2 | Adapter contract | Any source type can emit the same normalized artifacts and spans |
| M3 | OKF import/export | Concepts round-trip through OKF-compatible markdown bundles |
| M4 | Compiler | Sources compile into maintained concept pages with links and citations |
| M5 | Existing path bridges | PDF chunks and image annotations sync into the universal source/span model |
| M6 | New text/web adapters | Markdown, plain text, and web captures can be ingested without PDF-specific paths |
| M7 | Retrieval | Agent can search compiled concepts and drill into source evidence |
| M8 | Maintenance | Lint detects broken links, uncited claims, stale pages, contradictions, and orphan sources |
| M9 | User surface | API client and minimal desktop library let the user inspect sources/concepts and export OKF |
| M10 | Docs/tickets | Architecture and execution tickets are complete |

## Test Strategy

- Model tests for runtime source/concept tables, constraints, cascades, and user scoping.
- Adapter contract tests for normalized artifacts/spans across multiple source kinds.
- OKF tests for permissive import, stable export, unknown fields, unknown types, links, `index.md`, and `log.md`.
- Compiler tests with fake model output for deterministic concept creation, updates, links, citations, and failure handling.
- Retrieval tests with mocked embeddings for concept-first and evidence-drilldown behavior.
- Regression tests for current PDF document RAG and image annotation paths.
- API tests for source creation, compile, concept read, search, import/export, and lint.
- API client type checks and desktop build.
- Final validation through `bun run test`, `bun run lint`, `bun run build`, and Alembic current check.

## Commit Strategy

Use one commit per completed ticket:

- `ingestion: add runtime source schema`
- `ingestion: add adapter contract`
- `ingestion: add okf import export`
- `ingestion: compile sources into concepts`
- `ingestion: bridge document and image sources`
- `ingestion: add text and web adapters`
- `agent: search compiled knowledge bundles`
- `ingestion: lint knowledge bundles`
- `desktop: add knowledge library surface`
- `docs: document okf llm wiki ingestion`

## Risks

| Risk | Mitigation |
| --- | --- |
| Scope expands into every media type at once | Build source registry and adapter contract first; implement only existing PDF/image bridges plus text/web adapters in v1 |
| OKF v0.1 changes | Keep OKF at the import/export boundary, preserve unknown fields, and store internal model separately |
| Compiled concepts drift from source evidence | Require concept-source links, content hashes, stale-page lint, and evidence drilldown |
| Duplicate concepts accumulate | Use deterministic slug/title/type merge rules and lint duplicate titles |
| Retrieval overuses summaries without evidence | Two-stage retrieval must return source spans for citation-heavy answers |
| Personal memory boundary blurs | Ingestion creates sources/concepts only; memory promotion remains explicit through existing candidates/Soul Writer |
| Existing document/image behavior regresses | Bridge existing paths after tests, keep `document_chunk` and `image_annotation` embeddings intact |
| Desktop becomes a large editor project | Start with read/search/export/lint viewer only; defer editing workflows |

## Execution Handoff

Recommended execution mode: subagent-driven, one child ticket at a time, with review after each ticket. Start with `OKF-001` because every adapter, compiler, retrieval path, and export/import path depends on the source/concept schema.
