# Workflow Checkpoints and RAG Index Design

## Context

ANIMA already has a turn-level runtime state machine in PostgreSQL:

- `RuntimeRun` tracks a chat turn.
- `RuntimeStep` stores step traces for LLM/tool execution.
- `RuntimeMessage` stores durable turn transcript rows.
- `save_approval_checkpoint()` persists approval waits inside a run.
- `RuntimeBackgroundTaskRun` tracks background maintenance tasks.

This is strong turn persistence, but it does not yet model long-running workflows that may span several turns, process restarts, model failures, or user approval pauses. PDF ingestion and document RAG need workflow-level durability.

ANIMA also already has a pgvector-backed runtime embedding cache:

- `RuntimeEmbedding` supports `source_type` and `source_id`.
- `PgVecStore` currently hardcodes `source_type="memory_item"`.
- `MemoryItem.embedding_json` remains the portable cache in SQLCipher.

The upgrade should keep ANIMA's existing architecture: SQLCipher remains durable identity and memory authority; runtime PostgreSQL stores operational state, workflow checkpoints, document chunks, and rebuildable vector indexes.

## Goals

1. Add a reusable workflow checkpoint layer above the existing turn state machine.
2. Make workflow progress resumable after process exit, crash, cancellation, model failure, or user return.
3. Extend pgvector retrieval beyond memories to document chunks for PDF/RAG.
4. Keep document RAG context separate from durable personal memory.
5. Promote only approved extracted conclusions into SQLCipher memory.

## Non-Goals

- Do not replace the current agent runtime with LangGraph.
- Do not store workflow state in pgvector.
- Do not dump every PDF chunk into long-term memory.
- Do not require cloud storage or external vector databases.
- Do not make document chunks portable soul state in the first pass.

## Architecture

The upgrade introduces two layers:

1. Workflow checkpointing.
   Runtime PostgreSQL stores workflow runs and checkpoint snapshots. Each workflow is a resumable state machine with explicit states and idempotent transitions.

2. Document RAG indexing.
   Runtime PostgreSQL stores uploaded document metadata and text chunks. pgvector stores embeddings for `source_type="document_chunk"`. Retrieval can search memory-only, document-only, or blended sources.

The existing chat run state machine remains unchanged. Workflow runs can reference a `thread_id` and may create chat `RuntimeRun` rows when a step requires an LLM/tool turn, but workflow state is tracked independently.

## State Model

### RuntimeWorkflowRun

Fields:

- `id`
- `user_id`
- `thread_id`
- `workflow_type`
- `status`
- `current_state`
- `input_json`
- `result_json`
- `error_json`
- `retry_count`
- `max_retries`
- `created_at`
- `updated_at`
- `started_at`
- `completed_at`

Statuses:

- `created`
- `running`
- `awaiting_input`
- `paused`
- `completed`
- `failed`
- `cancelled`

### RuntimeWorkflowCheckpoint

Fields:

- `id`
- `workflow_run_id`
- `checkpoint_index`
- `state_name`
- `status`
- `input_json`
- `output_json`
- `artifact_refs_json`
- `idempotency_key`
- `error_json`
- `created_at`

The latest successful checkpoint is the resume anchor.

## PDF/RAG Workflow

Initial workflow:

```text
created
-> file_registered
-> text_extracted
-> chunked
-> embedded
-> indexed
-> summarized
-> facts_proposed
-> awaiting_approval
-> memory_saved
-> completed
```

Each state writes a checkpoint only after its durable side effects are complete.

If the process stops after `chunked`, resume starts at `embedded`. If embedding fails halfway through, the chunk embedding operation is idempotent and can continue from missing chunk vectors.

## Document Storage

### RuntimeDocument

Stores uploaded file metadata:

- `id`
- `user_id`
- `thread_id`
- `workflow_run_id`
- `filename`
- `mime_type`
- `storage_path`
- `sha256`
- `size_bytes`
- `status`
- `metadata_json`
- timestamps

### RuntimeDocumentChunk

Stores extracted text chunks:

- `id`
- `document_id`
- `user_id`
- `chunk_index`
- `page_start`
- `page_end`
- `section_title`
- `content_text`
- `content_hash`
- `token_count`
- `metadata_json`
- timestamps

Chunks are operational RAG artifacts. They are not long-term memory.

## pgvector Extension

`RuntimeEmbedding` already supports generic sources. The implementation should generalize the vector store API:

- `source_type="memory_item"` for existing memory rows.
- `source_type="document_chunk"` for RAG chunks.
- Future sources can include `episode`, `entity`, or `workflow_note`.

Search must support source filtering:

- memory-only
- document-only
- selected document ids
- selected source types
- blended memory + document retrieval

## Memory Boundary

Documents are external context. ANIMA may summarize, quote, answer questions, and cite page/chunk references from them.

Only user-approved extracted facts, goals, preferences, or relationship information should enter SQLCipher memory via the existing MemoryCandidate/Soul Writer pipeline.

## Error Handling

Workflow steps must be idempotent. Retrying a state should not duplicate documents, chunks, embeddings, or memory candidates.

Failures should store:

- failed state
- exception class/message
- retry count
- recoverability
- last checkpoint

User-facing resume should be able to say what was completed and what remains.

## Testing Strategy

Tests must cover:

- workflow start/checkpoint/resume/cancel/fail
- checkpoint ordering and latest-checkpoint selection
- idempotent checkpoint writes
- document/chunk schema creation
- vector upsert/search/delete for `document_chunk`
- PDF workflow resume from each major state
- no document chunks promoted directly to durable memory

