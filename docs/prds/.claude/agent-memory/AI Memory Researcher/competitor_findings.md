# Competitor Source Audit Key Findings

## Letta
- Three-tier memory: core blocks (in-prompt text), recall (messages), archival (passages)
- Blocks are agent-editable text buffers with char limits, not structured data
- Sleeptime agents (v1-v4): frequency-gated background LLM agents that edit memory blocks
- Frequency gating: `turns_counter % sleeptime_agent_frequency`, stored in Group model
- Last-processed message tracking prevents reprocessing on restart
- Task tracking via `Run` table with status lifecycle
- Hybrid search (BM25+vector+RRF k=60) only available via Turbopuffer; SQL path is vector-only
- Message compaction (summarization) exists; structured fact extraction does NOT
- No heat scoring, no decay, no forgetting mechanisms, no episodic memory

## Mem0
- Flat vector store (26 backends) + optional graph store (Neo4j/Kuzu/Memgraph)
- 2-step memory pipeline: LLM extracts facts -> LLM decides ADD/UPDATE/DELETE/NONE vs existing
- Graph: entity extraction via LLM tool calling, embedding-based dedup (threshold 0.7)
- Graph relation deletion: LLM evaluates on every add() whether existing relations are outdated
- BM25 used ONLY for graph result reranking, NOT for main memory search
- Pluggable reranker support (Cohere)
- UUID hallucination protection: maps to integers before LLM calls
- No heat scoring, no decay, no episodic memory, no background processing

## AnimaOS Differentiators Confirmed
- Only system with self-hosted hybrid search (BM25+vector+RRF)
- Only system with heat-based memory scoring
- Only system with episodic memory
- Only system with intentional forgetting (passive decay + active suppression + derived ref cleanup)
- Portable encrypted Core (SQLite) vs competitors requiring PostgreSQL/Neo4j

## Gaps to Address in AnimaOS PRDs
1. F4 needs relation pruning (Mem0 does this via LLM on every add)
2. F4 could benefit from BM25 reranking of graph results
3. Consider UUID-to-integer mapping for LLM prompts that include memory IDs
