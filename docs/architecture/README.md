---
title: AnimaOS Architecture Documentation
description: Index of all architecture documents for the AnimaOS project
category: architecture
---

# AnimaOS Architecture Documentation

AnimaOS is a privacy-first, portable AI companion system. It wraps an AI agent's entire being -- memory, identity, conversations, emotional state, and consciousness -- inside a single encrypted `.anima/` directory backed by SQLite + SQLCipher. The system runs as a local FastAPI server (Python) that communicates with open LLM providers (Ollama, OpenRouter, vLLM) and exposes a REST/SSE API consumed by a Tauri desktop frontend.

The core design thesis is **"portable encrypted AI"**: copy the `.anima/` directory to a USB drive, plug it into a new machine, enter your passphrase, and the AI wakes up with all its memories intact. There is no cloud database, no Docker requirement, and no closed LLM providers (OpenAI, Anthropic, Google are explicitly excluded).

The server is structured as a classic three-layer application: **API routes (FastAPI) -> Service layer (agent services) -> Persistence (SQLAlchemy + SQLite/SQLCipher)**. On top of this foundation sits a sophisticated consciousness system with self-model, emotional intelligence, intentional agency, and inner monologue capabilities.

## Document Index

### System Architecture (`system/`)
| Document | Contents |
|----------|----------|
| [Directory Structure](system/directory-structure.md) | Top-level folder layout and purpose of each directory |
| [API Routes](system/api-routes.md) | All REST endpoints grouped by router, dependency injection |
| [Services](system/services.md) | Agent runtime, memory stack, consciousness layer, LLM clients |
| [Database Schema](system/database-schema.md) | All 19 tables, ER diagram, column details |
| [Data Flow](system/data-flow.md) | End-to-end message flow, call chains, sequence diagrams |
| [Configuration & Startup](system/configuration.md) | Settings, env vars, boot sequence |
| [Cross-Cutting Concerns](system/cross-cutting.md) | Context window management, background tasks, gotchas, test coverage |

### Agent Runtime (`agent/`)
| Document | Contents |
|----------|----------|
| [Agent Runtime](agent/agent-runtime.md) | Deep dive into the cognitive loop, step execution, tool orchestration, compaction, approval flow |
| [Agent Tools](agent/agent-tools.md) | The 17 tools available to the LLM agent |

### Memory Architecture (`memory/`)
| Document | Contents |
|----------|----------|
| [Memory System](memory/memory-system.md) | Full memory lifecycle: write paths, retrieval scoring, consolidation, embeddings, claims, episodic memory, self-model |
| [Memory Implementation Plan](memory/memory-implementation-plan.md) | Detailed engineering spec for F1-F6: function signatures, schemas, test plans, organized by workstream |
| [Memory Repo Analysis](memory/memory-repo-analysis.md) | Comparative source-code analysis of Letta, Mem0, Nemori, MemOS, MemoryOS |

### Crypto & Auth (`crypto/`)
| Document | Contents |
|----------|----------|
| [Crypto & Auth](crypto/crypto-auth.md) | Two-layer encryption, session management, key derivation |

### Planning & Research
| Document | Contents |
|----------|----------|
| [PRDs](../prds/README.md) | All product requirement documents, organized by domain |
| [Competitor Analysis](../thesis/competitor-analysis.md) | Source-code-level comparison of 5 competitors vs AnimaOS thesis |
| [Thesis & Research](../thesis/) | Whitepaper, inner-life, roadmap, research reports |

## High-Level Architecture Diagram

```mermaid
flowchart TD
    subgraph Client["Desktop Client (Tauri + React)"]
        FE[Frontend UI]
    end

    subgraph Server["AnimaOS Server (FastAPI)"]
        subgraph MW["Middleware"]
            CORS[CORS Middleware]
            Nonce[Sidecar Nonce Middleware]
        end

        subgraph API["API Layer"]
            Auth["/api/auth"]
            Chat["/api/chat"]
            Memory["/api/memory"]
            Consc["/api/consciousness"]
            Soul["/api/soul"]
            Tasks["/api/tasks"]
            Users["/api/users"]
            Vault["/api/vault"]
            Config["/api/config"]
            Core["/api/core"]
            DB["/api/db"]
        end

        subgraph Services["Service Layer"]
            AgentRT["Agent Runtime"]
            MemStack["Memory Stack"]
            Consciousness["Consciousness Layer"]
            LLMLayer["LLM Clients"]
            CryptoLayer["Crypto & Auth"]
        end

        subgraph Persistence["Database Layer"]
            Models["19 SQLAlchemy Models"]
            DBSession["Per-User SQLite Sessions"]
        end
    end

    subgraph External["External"]
        Ollama["Ollama (local)"]
        OpenRouter["OpenRouter (API)"]
        VLLM["vLLM (local)"]
    end

    subgraph Storage["Disk"]
        AnimaDir[".anima/ directory"]
        SQLiteDB["SQLite + SQLCipher DB"]
        Manifest["manifest.json"]
    end

    FE -->|"HTTP + SSE + x-anima-nonce"| MW
    MW --> API
    API --> Services
    Services --> Persistence
    Persistence --> SQLiteDB
    CryptoLayer --> Manifest
    LLMLayer -->|"OpenAI-compatible API"| Ollama
    LLMLayer -->|"OpenAI-compatible API"| OpenRouter
    LLMLayer -->|"OpenAI-compatible API"| VLLM
    SQLiteDB --> AnimaDir
    Manifest --> AnimaDir
```

## Key Design Decisions

1. **Single-thread-per-user model**: Per-user asyncio locks serialize conversation turns, preventing race conditions at the cost of queuing concurrent requests from the same user.
2. **AnimaCompanion as cache layer**: The runtime is stateless; `AnimaCompanion` caches memory blocks and history between turns, invalidating via a version counter.
3. **Tool-driven agent architecture**: The agent always starts with `inner_thought`, uses tools, and ends with `send_message`. Enforced by `ToolRulesSolver`. Max 6 steps per turn.
4. **Supersession instead of mutation**: Memory items are never deleted; updates create new rows and set `superseded_by` on the old one.
5. **Per-user SQLite databases**: Each user gets their own SQLite file for true data isolation.
6. **Field-level encryption with domain DEKs**: Data segmented into 5 cryptographic domains for fine-grained access control.
7. **Hybrid search**: Combines vector similarity (cosine on embeddings) with keyword matching using adaptive filtering.
