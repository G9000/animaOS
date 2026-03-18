---
title: Service Layer
description: Agent runtime, memory stack, consciousness layer, and LLM client reference
category: architecture
---

# Service Layer

[Back to Index](README.md)

## Agent Runtime (`services/agent/`)

### Core Runtime Files

| File | Responsibility | Key Public API |
|------|---------------|----------------|
| `service.py` (1096 lines) | Top-level orchestrator, manages full turn lifecycle | `run_agent()`, `stream_agent()`, `dry_run_agent()`, `approve_or_deny_turn()`, `cancel_agent_run()`, `reset_agent_thread()` |
| `companion.py` | Process-resident cache above the stateless runtime | `AnimaCompanion`: `ensure_history_loaded()`, `ensure_memory_loaded()`, `invalidate_memory()`, `append_to_window()` |
| `runtime.py` | Stateless agent step-loop: prompt assembly, LLM call, tool execution, repeat | `AgentRuntime`, `build_loop_runtime()`, `invoke()`, `resume_after_approval()` |
| `turn_coordinator.py` | Per-user asyncio locks (LRU, max 256) to serialize concurrent turns | `get_user_lock(user_id)` |
| `executor.py` | Tool call execution engine | `ToolExecutor` |
| `tools.py` | 17 tool definitions available to the agent | `get_tools()`, `get_tool_summaries()`, `get_tool_rules()` |
| `system_prompt.py` | Jinja2 template-based system prompt assembly | `build_system_prompt()`, `SystemPromptContext` |
| `streaming.py` | SSE event construction | `AgentStreamEvent`, `build_chunk_event()`, `build_done_event()` |

### Supporting Runtime Files

| File | Responsibility | Key Public API |
|------|---------------|----------------|
| `messages.py` | Message construction and normalization | `build_conversation_messages()`, `make_assistant_message()` |
| `persistence.py` | Conversation persistence to DB (threads, runs, messages) | `get_or_create_thread()`, `append_user_message()`, `persist_agent_result()`, `create_run()` |
| `sequencing.py` | Monotonic sequence counter for message ordering | `reserve_message_sequences()` |
| `state.py` | Result types | `AgentResult`, `StoredMessage` |
| `runtime_types.py` | Step-level types | `StepTrace`, `ToolCall`, `StopReason`, `DryRunResult` |
| `output_filter.py` | LLM output post-processing | |
| `rules.py` | Tool orchestration rules (must-call, max-calls, etc.) | `ToolRule`, `ToolRulesSolver`, `build_default_tool_rules()` |
| `prompt_budget.py` | Token budget allocation across memory blocks | `plan_prompt_budget()`, `PromptBudgetTrace` |
| `proactive.py` | LLM-generated greetings with full context | `generate_greeting()`, `build_static_greeting()`, `gather_greeting_context()` |
| `tool_context.py` | Thread-local context for tool execution | `ToolContext`, `set_tool_context()`, `get_tool_context()` |

---

## Memory Stack (`services/agent/`)

| File | Responsibility | Key Public API |
|------|---------------|----------------|
| `memory_blocks.py` | Builds all `MemoryBlock` objects for system prompt (15+ block types) | `build_runtime_memory_blocks()`, `MemoryBlock` dataclass |
| `memory_store.py` | Core MemoryItem CRUD, focus tracking, scored retrieval | `add_memory_item()`, `get_memory_items()`, `get_memory_items_scored()`, `supersede_memory_item()`, `store_memory_item()` |
| `consolidation.py` | Post-turn extraction: regex patterns + LLM + emotional signals | `consolidate_turn_memory_with_llm()`, `schedule_background_memory_consolidation()` |
| `embeddings.py` | Embedding generation (Ollama/OpenRouter), hybrid search | `hybrid_search()`, `semantic_search()`, `adaptive_filter()` |
| `vector_store.py` | In-process cosine similarity index | `search()`, `delete_memory()` |
| `episodes.py` | Episodic memory storage and retrieval | |
| `claims.py` | Structured claim extraction and conflict resolution | `upsert_claim()` |
| `session_memory.py` | Working notes scoped to conversation session | `write_session_note()`, `promote_session_note()`, `remove_session_note()` |
| `conversation_search.py` | Full-text + semantic search across conversation history | `search_conversation_history()` |
| `compaction.py` | Context compaction (text-based + LLM-powered summarization) | `compact_thread_context()`, `compact_thread_context_with_llm()`, `estimate_message_tokens()` |
| `reflection.py` | Post-inactivity reflection (5-minute delay timer) | `schedule_reflection()`, `cancel_pending_reflection()` |
| `sleep_tasks.py` | Background maintenance: contradiction scan, profile synthesis, episode gen | `run_sleep_tasks()` |

### Memory Block Types (15+)

Built by `memory_blocks.py:build_runtime_memory_blocks()`:

| Block | Source | Description |
|-------|--------|-------------|
| Soul biography | `self_model_blocks` (section=soul) | Core persona/directive |
| Living persona | `self_model_blocks` (section=persona) | Dynamic persona description |
| Human core | `self_model_blocks` (section=human) | Agent's holistic understanding of user |
| User directive | `self_model_blocks` (section=user_directive) | User-authored instructions |
| Self-model (identity) | `self_model_blocks` | Who the agent is |
| Self-model (inner_state) | `self_model_blocks` | Current emotional/cognitive state |
| Self-model (working_memory) | `self_model_blocks` | Active context |
| Self-model (growth_log) | `self_model_blocks` | Learning journal |
| Self-model (intentions) | `self_model_blocks` | Active goals |
| Emotional context | `emotional_signals` | Synthesized emotional state |
| Semantic memories | `memory_items` + embeddings | Query-relevant memories via hybrid search |
| Facts | `memory_items` (category=fact) | Scored factual memories |
| Preferences | `memory_items` (category=preference) | User preferences |
| Goals | `memory_items` (category=goal) | User goals |
| Tasks | `tasks` table | Open task list |
| Relationships | `memory_items` (category=relationship) | Relationship info |
| Current focus | `memory_items` (focus) | Active focus topics |
| Thread summary | compaction summary | Conversation summary from compaction |
| Episodes | `memory_episodes` | Recent episodic memories |
| Session notes | `session_notes` | Working scratch pad |

---

## Consciousness Layer (`services/agent/`)

| File | Responsibility | Key Public API |
|------|---------------|----------------|
| `self_model.py` | Self-model CRUD, 5-section identity, seeding, versioning | `get_all_self_model_blocks()`, `set_self_model_block()`, `ensure_self_model_exists()`, `append_growth_log_entry()` |
| `emotional_intelligence.py` | 12-emotion detection, signal storage, emotional synthesis | `get_recent_signals()`, `synthesize_emotional_context()` |
| `intentions.py` | Intention lifecycle, procedural rule management | `add_intention()`, `complete_intention()`, `get_intentions_text()` |
| `inner_monologue.py` | Quick reflection + deep monologue generation (LLM-driven) | `run_deep_monologue()` |
| `feedback_signals.py` | Re-ask/correction detection, growth log recording | `collect_feedback_signals()`, `record_feedback_signals()` |

### Self-Model Sections

| Section | Purpose |
|---------|---------|
| `identity` | Who the agent is, name, personality traits |
| `inner_state` | Current emotional and cognitive state |
| `working_memory` | Active context, recent topics |
| `growth_log` | Learning journal, corrections, insights |
| `intentions` | Active goals and plans |

### 12 Emotions

Detected by `emotional_intelligence.py` with confidence scores and trajectory tracking (rising, falling, stable).

---

## LLM Clients (`services/agent/`)

| File | Responsibility | Key Public API |
|------|---------------|----------------|
| `llm.py` | Client factory, provider config validation | `create_llm()`, `resolve_base_url()` |
| `openai_compatible_client.py` | HTTP client for OpenAI-compatible APIs | `OpenAICompatibleChatClient` |
| `adapters/base.py` | LLM adapter base class | `BaseLLMAdapter` |
| `adapters/openai_compatible.py` | OpenAI-compatible adapter implementation | |
| `adapters/scaffold.py` | Test/dev scaffold adapter (no real LLM) | |

### Supported Providers

| Provider | Type | Base URL |
|----------|------|----------|
| `ollama` | Local | `http://localhost:11434/v1` |
| `openrouter` | Cloud (open models) | `https://openrouter.ai/api/v1` |
| `vllm` | Local | Configurable |

---

## Crypto & Auth Services (`services/`)

| File | Responsibility | Key Public API |
|------|---------------|----------------|
| `crypto.py` | Argon2id KDF, AES-256-GCM wrap/unwrap, field-level encrypt/decrypt, SQLCipher key derivation via HKDF | `derive_sqlcipher_key()`, `wrap_dek()`, `encrypt_text_with_dek()`, `decrypt_text_with_dek()` |
| `data_crypto.py` | Domain-aware field encryption/decryption helpers | `ef()` (encrypt field), `df()` (decrypt field) |
| `auth.py` | Password hashing/verification, user serialization | `get_user_by_id()`, `change_user_password()`, `verify_password()` |
| `sessions.py` | In-memory unlock session store, SQLCipher key cache | `UnlockSessionStore`, `UnlockSession` (user_id + DEKs + expiry) |
| `vault.py` | Encrypted vault export/import (full user data backup) | `export_vault()`, `import_vault()` |
| `core.py` | Core manifest management, provisioning, lock file | `ensure_core_manifest()`, `get_sqlcipher_kdf_salt()`, `store_wrapped_sqlcipher_key()` |
| `storage.py` | File/blob storage, per-user data directory management | `get_user_data_dir()` |
| `creation_agent.py` | AI creation ceremony (multi-turn LLM onboarding) | `handle_creation_turn()` |
