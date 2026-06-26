# Workflow Checkpoint RAG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a resumable workflow checkpoint layer and extend pgvector-backed retrieval to support document/PDF RAG without confusing document context with durable memory.

**Architecture:** Keep the existing chat run state machine intact and add workflow-level persistence above it. Runtime PostgreSQL stores workflow runs, checkpoints, documents, chunks, and rebuildable embeddings; SQLCipher remains the authority for identity and long-term memory.

**Tech Stack:** Python, FastAPI service layer, SQLAlchemy 2.0 models, runtime PostgreSQL Alembic migrations, pgvector, pytest, existing `RuntimeBase` and agent services.

---

## Scope Check

This is two dependent tracks, not two independent features:

1. Workflow checkpointing provides resumable long-running state.
2. Document RAG uses workflow checkpointing for PDF ingestion and pgvector for chunk retrieval.

Implement checkpointing first. Do not start PDF/RAG workflow execution until checkpoint primitives are tested.

## File Structure

Create:

- `apps/server/src/anima_server/services/workflows/__init__.py`
  Exports workflow service primitives.
- `apps/server/src/anima_server/services/workflows/state.py`
  Defines workflow status/state constants and dataclasses.
- `apps/server/src/anima_server/services/workflows/checkpoints.py`
  Starts workflow runs, appends checkpoints, resumes from latest checkpoint, marks pause/failure/cancellation/completion.
- `apps/server/src/anima_server/services/documents/__init__.py`
  Exports document service primitives.
- `apps/server/src/anima_server/services/documents/models.py`
  Small service dataclasses for extracted chunks and document registration inputs.
- `apps/server/src/anima_server/services/documents/store.py`
  Runtime DB CRUD for documents and chunks.
- `apps/server/src/anima_server/services/documents/rag.py`
  Document retrieval over chunks using embeddings and source filters.
- `apps/server/alembic_runtime/versions/016_workflow_checkpoints.py`
  Runtime migration for workflow/document tables.
- `apps/server/tests/test_workflow_checkpoints.py`
  Unit tests for workflow checkpoint service.
- `apps/server/tests/test_document_store.py`
  Unit tests for document/chunk persistence.
- `apps/server/tests/test_pgvec_document_sources.py`
  Tests for source-aware pgvector operations using fallback-safe paths.
- `apps/server/tests/test_document_rag.py`
  Tests for document-only and filtered RAG retrieval.

Modify:

- `apps/server/src/anima_server/models/runtime.py`
  Add `RuntimeWorkflowRun`, `RuntimeWorkflowCheckpoint`, `RuntimeDocument`, `RuntimeDocumentChunk`.
- `apps/server/src/anima_server/models/runtime_embedding.py`
  Update source comments and add indexes for source filtering if needed.
- `apps/server/src/anima_server/models/__init__.py`
  Export new runtime models.
- `apps/server/src/anima_server/services/agent/pgvec_store.py`
  Generalize upsert/delete/search to support `source_type` and `source_id`.
- `apps/server/src/anima_server/services/agent/vector_store.py`
  Preserve memory-specific helpers while adding source-aware lower-level helpers.
- `apps/server/src/anima_server/services/agent/bm25_index.py`
  Keep existing memory BM25 behavior; do not mix documents into memory BM25 by default.
- `docs/architecture/agent/agent-runtime.md`
  Document workflow checkpointing relationship to runs/steps.
- `docs/architecture/memory/memory-system.md`
  Document document RAG as runtime context, not durable memory.

---

### Task 1: Add Runtime Workflow Models

**Files:**
- Modify: `apps/server/src/anima_server/models/runtime.py`
- Modify: `apps/server/src/anima_server/models/__init__.py`
- Test: `apps/server/tests/test_workflow_checkpoints.py`

- [ ] **Step 1: Write failing model registration test**

Add:

```python
from sqlalchemy import inspect

from anima_server.db.runtime_base import RuntimeBase
from anima_server.models.runtime import RuntimeWorkflowCheckpoint, RuntimeWorkflowRun


def test_workflow_tables_registered(runtime_engine):
    RuntimeBase.metadata.create_all(runtime_engine)
    names = set(inspect(runtime_engine).get_table_names())
    assert RuntimeWorkflowRun.__tablename__ in names
    assert RuntimeWorkflowCheckpoint.__tablename__ in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/server/tests/test_workflow_checkpoints.py::test_workflow_tables_registered -q`

Expected: FAIL because `RuntimeWorkflowRun` does not exist.

- [ ] **Step 3: Add models**

In `models/runtime.py`, add:

```python
class RuntimeWorkflowRun(RuntimeBase):
    __tablename__ = "runtime_workflow_runs"
    __table_args__ = (
        Index("ix_runtime_workflow_runs_user_status", "user_id", "status"),
        Index("ix_runtime_workflow_runs_user_type", "user_id", "workflow_type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    thread_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("runtime_threads.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    workflow_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default=text("'created'"))
    current_state: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'created'"))
    input_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("3"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)


class RuntimeWorkflowCheckpoint(RuntimeBase):
    __tablename__ = "runtime_workflow_checkpoints"
    __table_args__ = (
        UniqueConstraint("workflow_run_id", "checkpoint_index", name="uq_runtime_workflow_checkpoint_index"),
        UniqueConstraint("workflow_run_id", "idempotency_key", name="uq_runtime_workflow_checkpoint_idempotency"),
        Index("ix_runtime_workflow_checkpoints_run_created", "workflow_run_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workflow_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("runtime_workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    checkpoint_index: Mapped[int] = mapped_column(Integer, nullable=False)
    state_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    input_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    artifact_refs_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    error_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False, server_default=func.now())
```

Export both models from `models/__init__.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest apps/server/tests/test_workflow_checkpoints.py::test_workflow_tables_registered -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add apps/server/src/anima_server/models/runtime.py apps/server/src/anima_server/models/__init__.py apps/server/tests/test_workflow_checkpoints.py
git commit -m "runtime: add workflow checkpoint models"
```

---

### Task 2: Add Runtime Migration

**Files:**
- Create: `apps/server/alembic_runtime/versions/016_workflow_checkpoints.py`
- Test: existing runtime migration command

- [ ] **Step 1: Write migration**

Create revision after `015_memory_extraction_failures.py`.

Migration must create:

- `runtime_workflow_runs`
- `runtime_workflow_checkpoints`

Use PostgreSQL JSON for runtime DB and keep SQLite test compatibility where Alembic allows.

- [ ] **Step 2: Verify Alembic head**

Run: `uv run alembic -c apps/server/alembic_runtime.ini heads`

Expected: one head, revision `016`.

- [ ] **Step 3: Apply migrations**

Run: `uv run alembic -c apps/server/alembic_runtime.ini upgrade head`

Expected: migration applies without errors.

- [ ] **Step 4: Commit**

Run:

```bash
git add apps/server/alembic_runtime/versions/016_workflow_checkpoints.py
git commit -m "db: add workflow checkpoint runtime migration"
```

---

### Task 3: Implement Workflow Checkpoint Service

**Files:**
- Create: `apps/server/src/anima_server/services/workflows/__init__.py`
- Create: `apps/server/src/anima_server/services/workflows/state.py`
- Create: `apps/server/src/anima_server/services/workflows/checkpoints.py`
- Test: `apps/server/tests/test_workflow_checkpoints.py`

- [ ] **Step 1: Write failing tests for start/checkpoint/resume**

Add tests:

```python
def test_start_workflow_creates_created_run(runtime_db):
    run = start_workflow(
        runtime_db,
        user_id=1,
        thread_id=None,
        workflow_type="pdf_ingestion",
        input_json={"filename": "guide.pdf"},
    )
    assert run.status == "created"
    assert run.current_state == "created"


def test_append_checkpoint_advances_state(runtime_db):
    run = start_workflow(runtime_db, user_id=1, thread_id=None, workflow_type="pdf_ingestion")
    checkpoint = append_checkpoint(
        runtime_db,
        workflow_run_id=run.id,
        state_name="text_extracted",
        status="completed",
        output_json={"pages": 10},
        idempotency_key="extract:doc-1",
    )
    runtime_db.refresh(run)
    assert checkpoint.checkpoint_index == 1
    assert run.status == "running"
    assert run.current_state == "text_extracted"


def test_load_resume_point_returns_latest_completed_checkpoint(runtime_db):
    run = start_workflow(runtime_db, user_id=1, thread_id=None, workflow_type="pdf_ingestion")
    append_checkpoint(runtime_db, workflow_run_id=run.id, state_name="text_extracted", status="completed", idempotency_key="a")
    append_checkpoint(runtime_db, workflow_run_id=run.id, state_name="chunked", status="completed", idempotency_key="b")
    resume = load_resume_point(runtime_db, workflow_run_id=run.id)
    assert resume.run.id == run.id
    assert resume.latest_checkpoint.state_name == "chunked"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/server/tests/test_workflow_checkpoints.py -q`

Expected: FAIL because service functions do not exist.

- [ ] **Step 3: Implement state constants**

In `state.py`:

```python
from dataclasses import dataclass
from typing import Literal

WorkflowStatus = Literal["created", "running", "awaiting_input", "paused", "completed", "failed", "cancelled"]
CheckpointStatus = Literal["completed", "awaiting_input", "failed", "skipped"]


@dataclass(frozen=True, slots=True)
class WorkflowResumePoint:
    run: object
    latest_checkpoint: object | None
    next_state: str | None
```

- [ ] **Step 4: Implement checkpoint functions**

In `checkpoints.py`, implement:

- `start_workflow()`
- `append_checkpoint()`
- `load_resume_point()`
- `mark_workflow_awaiting_input()`
- `mark_workflow_completed()`
- `mark_workflow_failed()`
- `cancel_workflow()`

Rules:

- `append_checkpoint()` must be idempotent by `(workflow_run_id, idempotency_key)`.
- Successful checkpoints advance `current_state`.
- Failed checkpoints set workflow `status="failed"` unless caller opts to pause.
- Completion sets `completed_at`.

- [ ] **Step 5: Run tests**

Run: `uv run pytest apps/server/tests/test_workflow_checkpoints.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add apps/server/src/anima_server/services/workflows apps/server/tests/test_workflow_checkpoints.py
git commit -m "runtime: add workflow checkpoint service"
```

---

### Task 4: Add Document and Chunk Models

**Files:**
- Modify: `apps/server/src/anima_server/models/runtime.py`
- Modify: `apps/server/src/anima_server/models/__init__.py`
- Modify: `apps/server/alembic_runtime/versions/016_workflow_checkpoints.py`
- Test: `apps/server/tests/test_document_store.py`

- [ ] **Step 1: Write failing model registration test**

Add:

```python
from sqlalchemy import inspect

from anima_server.db.runtime_base import RuntimeBase
from anima_server.models.runtime import RuntimeDocument, RuntimeDocumentChunk


def test_document_tables_registered(runtime_engine):
    RuntimeBase.metadata.create_all(runtime_engine)
    names = set(inspect(runtime_engine).get_table_names())
    assert RuntimeDocument.__tablename__ in names
    assert RuntimeDocumentChunk.__tablename__ in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/server/tests/test_document_store.py::test_document_tables_registered -q`

Expected: FAIL.

- [ ] **Step 3: Add models**

Add `RuntimeDocument` and `RuntimeDocumentChunk` to `models/runtime.py`.

Core constraints:

- `RuntimeDocument.sha256` indexed.
- Unique document hash per user: `(user_id, sha256)`.
- Unique chunk index per document: `(document_id, chunk_index)`.
- `RuntimeDocument.workflow_run_id` nullable FK to workflow runs.

- [ ] **Step 4: Update migration**

Extend revision `016` to create both document tables in the same migration. This is acceptable because the migration has not shipped yet in this plan.

- [ ] **Step 5: Run test**

Run: `uv run pytest apps/server/tests/test_document_store.py::test_document_tables_registered -q`

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add apps/server/src/anima_server/models/runtime.py apps/server/src/anima_server/models/__init__.py apps/server/alembic_runtime/versions/016_workflow_checkpoints.py apps/server/tests/test_document_store.py
git commit -m "runtime: add document chunk models"
```

---

### Task 5: Implement Document Store Service

**Files:**
- Create: `apps/server/src/anima_server/services/documents/__init__.py`
- Create: `apps/server/src/anima_server/services/documents/models.py`
- Create: `apps/server/src/anima_server/services/documents/store.py`
- Test: `apps/server/tests/test_document_store.py`

- [ ] **Step 1: Write failing CRUD tests**

Cover:

- registering a document
- idempotent registration by `(user_id, sha256)`
- replacing chunks for a document
- loading chunks in `chunk_index` order

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/server/tests/test_document_store.py -q`

Expected: FAIL.

- [ ] **Step 3: Implement service dataclasses**

In `models.py`:

```python
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DocumentRegistration:
    user_id: int
    filename: str
    mime_type: str
    storage_path: str
    sha256: str
    size_bytes: int
    thread_id: int | None = None
    workflow_run_id: int | None = None
    metadata_json: dict | None = None


@dataclass(frozen=True, slots=True)
class ExtractedDocumentChunk:
    chunk_index: int
    content_text: str
    page_start: int | None = None
    page_end: int | None = None
    section_title: str | None = None
    token_count: int | None = None
    metadata_json: dict | None = None
```

- [ ] **Step 4: Implement store functions**

In `store.py`:

- `register_document(db, registration)`
- `set_document_status(db, document_id, status)`
- `replace_document_chunks(db, document_id, chunks)`
- `list_document_chunks(db, document_id)`
- `get_document_for_user(db, user_id, document_id)`

Compute chunk `content_hash` with SHA-256.

- [ ] **Step 5: Run tests**

Run: `uv run pytest apps/server/tests/test_document_store.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add apps/server/src/anima_server/services/documents apps/server/tests/test_document_store.py
git commit -m "documents: add runtime document store"
```

---

### Task 6: Generalize PgVecStore Source Handling

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/pgvec_store.py`
- Modify: `apps/server/src/anima_server/services/agent/vector_store.py`
- Test: `apps/server/tests/test_pgvec_document_sources.py`

- [ ] **Step 1: Write failing tests**

Cover:

- `PgVecStore.upsert_source(... source_type="document_chunk", source_id=123)`
- source-filtered search excludes memory rows when searching document chunks
- source-specific delete removes only that source type/id
- existing memory-specific `upsert()` still writes `source_type="memory_item"`

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/server/tests/test_pgvec_document_sources.py -q`

Expected: FAIL.

- [ ] **Step 3: Add source-aware methods**

In `PgVecStore`, add:

```python
def upsert_source(
    self,
    user_id: int,
    *,
    source_type: str,
    source_id: int,
    content: str,
    embedding: list[float],
    category: str = "document",
    importance: int = 3,
) -> None:
    ...
```

Keep existing `upsert()` as:

```python
def upsert(... item_id: int, ...):
    return self.upsert_source(
        user_id,
        source_type="memory_item",
        source_id=item_id,
        content=content,
        embedding=embedding,
        category=category,
        importance=importance,
    )
```

Add `delete_source()` and `search_by_vector(... source_types: Sequence[str] | None = None)`.

- [ ] **Step 4: Preserve compatibility**

Memory callers in `vector_store.py`, `embeddings.py`, `memory_store.py`, and `forgetting.py` must not need broad rewrites. Existing memory helpers should call the compatibility methods.

- [ ] **Step 5: Run tests**

Run:

```bash
uv run pytest apps/server/tests/test_pgvec_document_sources.py -q
uv run pytest apps/server/tests/test_vector_store.py apps/server/tests/test_embeddings.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add apps/server/src/anima_server/services/agent/pgvec_store.py apps/server/src/anima_server/services/agent/vector_store.py apps/server/tests/test_pgvec_document_sources.py
git commit -m "retrieval: support source-aware pgvector rows"
```

---

### Task 7: Add Document Embedding Indexing

**Files:**
- Modify: `apps/server/src/anima_server/services/documents/store.py`
- Create: `apps/server/src/anima_server/services/documents/indexing.py`
- Test: `apps/server/tests/test_document_rag.py`

- [ ] **Step 1: Write failing tests**

Test that `embed_document_chunks()`:

- embeds unembedded chunks
- writes `RuntimeEmbedding.source_type == "document_chunk"`
- skips chunks that already have matching `content_hash`
- updates document status to `indexed`

Mock embedding generation. Do not call real providers.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/server/tests/test_document_rag.py::test_embed_document_chunks_indexes_document_chunk_sources -q`

Expected: FAIL.

- [ ] **Step 3: Implement indexing**

In `indexing.py`, implement:

- `embed_document_chunks(runtime_db, user_id, document_id, embedding_fn=generate_embedding)`
- `get_unembedded_chunks(runtime_db, user_id, document_id)`

Use `PgVecStore.upsert_source(source_type="document_chunk", source_id=chunk.id, ...)`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest apps/server/tests/test_document_rag.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add apps/server/src/anima_server/services/documents/indexing.py apps/server/src/anima_server/services/documents/store.py apps/server/tests/test_document_rag.py
git commit -m "documents: index chunks with pgvector"
```

---

### Task 8: Add Document RAG Retrieval

**Files:**
- Create: `apps/server/src/anima_server/services/documents/rag.py`
- Test: `apps/server/tests/test_document_rag.py`

- [ ] **Step 1: Write failing retrieval tests**

Cover:

- document-only query returns chunk ids and snippets
- document id filter excludes other documents
- empty embedding result returns empty list
- retrieved chunks include page metadata for citation

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/server/tests/test_document_rag.py -q`

Expected: FAIL.

- [ ] **Step 3: Implement retrieval result dataclass**

In `rag.py`:

```python
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DocumentRagResult:
    chunk_id: int
    document_id: int
    filename: str
    content: str
    similarity: float
    page_start: int | None
    page_end: int | None
    section_title: str | None
```

- [ ] **Step 4: Implement search**

Implement:

- `search_document_chunks(runtime_db, user_id, query, document_ids=None, limit=8)`

Use existing `generate_embedding()` by default, but allow injection in tests.

Search `RuntimeEmbedding` with `source_type="document_chunk"`, then hydrate `RuntimeDocumentChunk` and `RuntimeDocument`.

- [ ] **Step 5: Run tests**

Run: `uv run pytest apps/server/tests/test_document_rag.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add apps/server/src/anima_server/services/documents/rag.py apps/server/tests/test_document_rag.py
git commit -m "documents: add RAG chunk retrieval"
```

---

### Task 9: Add PDF Workflow Skeleton

**Files:**
- Create: `apps/server/src/anima_server/services/documents/pdf_workflow.py`
- Test: `apps/server/tests/test_pdf_workflow_checkpoints.py`

- [ ] **Step 1: Write failing workflow resume tests**

Use fake extraction/chunking/embedding functions. Cover resume from:

- `file_registered`
- `text_extracted`
- `chunked`
- `indexed`

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/server/tests/test_pdf_workflow_checkpoints.py -q`

Expected: FAIL.

- [ ] **Step 3: Implement workflow state order**

In `pdf_workflow.py`:

```python
PDF_WORKFLOW_STATES = (
    "created",
    "file_registered",
    "text_extracted",
    "chunked",
    "embedded",
    "indexed",
    "summarized",
    "facts_proposed",
    "awaiting_approval",
    "memory_saved",
    "completed",
)
```

- [ ] **Step 4: Implement resumable runner**

Implement:

- `start_pdf_ingestion_workflow(...)`
- `resume_pdf_ingestion_workflow(...)`
- `run_pdf_ingestion_until_wait_or_done(...)`

The first version may use injected functions for extraction/summarization. Real PDF parsing can be a later task.

- [ ] **Step 5: Run tests**

Run: `uv run pytest apps/server/tests/test_pdf_workflow_checkpoints.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add apps/server/src/anima_server/services/documents/pdf_workflow.py apps/server/tests/test_pdf_workflow_checkpoints.py
git commit -m "documents: add resumable PDF workflow skeleton"
```

---

### Task 10: Add Minimal PDF Text Extraction

**Files:**
- Modify: `apps/server/src/anima_server/services/documents/pdf_workflow.py`
- Create: `apps/server/src/anima_server/services/documents/pdf_text.py`
- Test: `apps/server/tests/test_pdf_text.py`

- [ ] **Step 1: Write failing tests for text normalization**

Test plain extracted text cleanup using existing `prepare_memory_text(... apply_pdf_spacing=True)` behavior. Do not require a binary PDF fixture unless the repo already has one.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/server/tests/test_pdf_text.py -q`

Expected: FAIL.

- [ ] **Step 3: Implement extraction wrapper**

Use a conservative parser already available in the environment. If no PDF parser dependency exists, add only the service boundary and skip binary extraction until dependency choice is approved.

Function:

```python
def extract_pdf_text(path: str) -> list[PageText]:
    ...
```

Return page-numbered text objects so chunk citations can include pages.

- [ ] **Step 4: Run tests**

Run: `uv run pytest apps/server/tests/test_pdf_text.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add apps/server/src/anima_server/services/documents/pdf_text.py apps/server/src/anima_server/services/documents/pdf_workflow.py apps/server/tests/test_pdf_text.py
git commit -m "documents: add PDF text extraction boundary"
```

---

### Task 11: Add Chunking

**Files:**
- Create: `apps/server/src/anima_server/services/documents/chunking.py`
- Test: `apps/server/tests/test_document_chunking.py`

- [ ] **Step 1: Write failing chunking tests**

Cover:

- stable chunk order
- page_start/page_end retained
- chunks stay under target character budget
- tiny documents produce one chunk

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/server/tests/test_document_chunking.py -q`

Expected: FAIL.

- [ ] **Step 3: Implement simple chunker**

Use paragraph-aware chunking first. Do not add semantic chunking yet.

Function:

```python
def chunk_pages(pages: Sequence[PageText], *, target_chars: int = 1800, overlap_chars: int = 200) -> list[ExtractedDocumentChunk]:
    ...
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest apps/server/tests/test_document_chunking.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add apps/server/src/anima_server/services/documents/chunking.py apps/server/tests/test_document_chunking.py
git commit -m "documents: add page-aware chunking"
```

---

### Task 12: Wire Workflow to Document Services

**Files:**
- Modify: `apps/server/src/anima_server/services/documents/pdf_workflow.py`
- Test: `apps/server/tests/test_pdf_workflow_checkpoints.py`

- [ ] **Step 1: Write failing end-to-end service test**

Use fake PDF pages and fake embeddings. Assert:

- document registered
- chunks written
- chunk embeddings written
- checkpoints exist for each completed state
- rerun resumes without duplicating chunks or embeddings

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/server/tests/test_pdf_workflow_checkpoints.py -q`

Expected: FAIL.

- [ ] **Step 3: Implement state transitions**

For each durable operation:

1. Perform operation idempotently.
2. Commit or flush state.
3. Append checkpoint.
4. Continue to next state.

- [ ] **Step 4: Run tests**

Run: `uv run pytest apps/server/tests/test_pdf_workflow_checkpoints.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add apps/server/src/anima_server/services/documents/pdf_workflow.py apps/server/tests/test_pdf_workflow_checkpoints.py
git commit -m "documents: wire PDF workflow checkpoints"
```

---

### Task 13: Add User Approval Boundary for Memory Promotion

**Files:**
- Modify: `apps/server/src/anima_server/services/documents/pdf_workflow.py`
- Test: `apps/server/tests/test_pdf_workflow_checkpoints.py`

- [ ] **Step 1: Write failing tests**

Assert PDF-derived facts are staged as proposals and not promoted directly to `MemoryItem`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/server/tests/test_pdf_workflow_checkpoints.py::test_pdf_facts_wait_for_approval_before_memory -q`

Expected: FAIL.

- [ ] **Step 3: Implement awaiting approval state**

At `facts_proposed`, write proposals to workflow `result_json` or artifact refs. Mark workflow:

- `status="awaiting_input"`
- `current_state="awaiting_approval"`

Do not create MemoryCandidates until approval.

- [ ] **Step 4: Implement approval continuation**

Add:

- `approve_pdf_memory_proposals(...)`
- `reject_pdf_memory_proposals(...)`

Approved proposals enter existing MemoryCandidate/Soul Writer pipeline.

- [ ] **Step 5: Run tests**

Run: `uv run pytest apps/server/tests/test_pdf_workflow_checkpoints.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add apps/server/src/anima_server/services/documents/pdf_workflow.py apps/server/tests/test_pdf_workflow_checkpoints.py
git commit -m "documents: require approval before PDF memory promotion"
```

---

### Task 14: Add Minimal API Surface

**Files:**
- Create: `apps/server/src/anima_server/api/routes/documents.py`
- Modify: `apps/server/src/anima_server/main.py`
- Test: `apps/server/tests/test_documents_api.py`

- [ ] **Step 1: Write failing API tests**

Cover:

- start workflow
- get workflow status
- resume workflow
- search document chunks

Use test auth helpers already used by route tests.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/server/tests/test_documents_api.py -q`

Expected: FAIL.

- [ ] **Step 3: Implement routes**

Add endpoints:

- `POST /api/documents/workflows/pdf`
- `GET /api/documents/workflows/{workflow_id}`
- `POST /api/documents/workflows/{workflow_id}/resume`
- `POST /api/documents/search`
- `POST /api/documents/workflows/{workflow_id}/approve-memory`

Keep payloads small and explicit. Do not stream in first pass.

- [ ] **Step 4: Register router**

In `main.py`, include the documents router.

- [ ] **Step 5: Run tests**

Run: `uv run pytest apps/server/tests/test_documents_api.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add apps/server/src/anima_server/api/routes/documents.py apps/server/src/anima_server/main.py apps/server/tests/test_documents_api.py
git commit -m "api: expose document workflow routes"
```

---

### Task 15: Documentation

**Files:**
- Modify: `docs/architecture/agent/agent-runtime.md`
- Modify: `docs/architecture/memory/memory-system.md`
- Test: docs grep/manual

- [ ] **Step 1: Update agent runtime docs**

Add a "Workflow Checkpoints" section explaining:

- turn state machine vs workflow state machine
- how workflow runs reference threads
- how latest checkpoint enables resume
- failure/cancel/awaiting-input behavior

- [ ] **Step 2: Update memory docs**

Add a "Document RAG Boundary" section explaining:

- documents/chunks are runtime context
- pgvector indexes chunks as `document_chunk`
- approved extracted conclusions go through MemoryCandidate/Soul Writer

- [ ] **Step 3: Verify docs mention key tables**

Run:

```bash
rg -n "RuntimeWorkflowRun|runtime_documents|document_chunk|Workflow Checkpoints|Document RAG Boundary" docs apps/server/src/anima_server
```

Expected: all new concepts appear in docs and code.

- [ ] **Step 4: Commit**

Run:

```bash
git add docs/architecture/agent/agent-runtime.md docs/architecture/memory/memory-system.md
git commit -m "docs: document workflow checkpoints and document RAG"
```

---

### Task 16: Final Verification

**Files:**
- No new files.

- [ ] **Step 1: Run focused backend tests**

Run:

```bash
uv run pytest apps/server/tests/test_workflow_checkpoints.py apps/server/tests/test_document_store.py apps/server/tests/test_pgvec_document_sources.py apps/server/tests/test_document_rag.py apps/server/tests/test_pdf_workflow_checkpoints.py -q
```

Expected: PASS.

- [ ] **Step 2: Run broader backend tests**

Run: `bun run test`

Expected: PASS.

- [ ] **Step 3: Run lint**

Run: `bun run lint`

Expected: PASS.

- [ ] **Step 4: Run build**

Run: `bun run build`

Expected: PASS.

- [ ] **Step 5: Check runtime DB head**

Run: `bun run db:server:current`

Expected: runtime/soul DB commands complete without migration errors.

- [ ] **Step 6: Commit final cleanup if needed**

Run:

```bash
git status --short
git diff --check
```

Expected: only intentional changes, no whitespace errors.
