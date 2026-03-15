# ANIMA OS — Documentation

> _The first AI companion with an open mind._

---

## Vision & Thesis — [`thesis/`](thesis/)

| Document                                             | Description                                                                                                           |
| ---------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| [Whitepaper](thesis/whitepaper.md)                   | The canonical thesis — what ANIMA is, why it exists, theoretical foundations, the five streams, and design principles |
| [Roadmap](thesis/roadmap.md)                         | Project roadmap organized by phases — building depth of personal connection before breadth of features                |
| [Succession Protocol](thesis/succession-protocol.md) | Dead man switch, ownership transfer, and AI self-succession — detailed design                                         |
| [Portable Core](thesis/portable-core.md)             | Thesis on packaging the Core — the encrypted memory and identity data — as a portable, injectable artifact            |
| [The Inner Life](thesis/inner-life.md)               | Thesis on what happens between conversations — reflection, emotional awareness, self-model evolution, and growth      |

---

## Architecture & Design — [`architecture/`](architecture/)

| Document                                                                    | Description                                                                                      |
| --------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| [Memory System](architecture/memory-system.md)                              | Architecture and implementation of the ANIMA memory system                                       |
| [Structured Memory Claims](architecture/structured-memory-claims-design.md) | Design for structured memory claims in `apps/server`                                             |
| [Agent Runtime Improvements](architecture/agent-runtime-improvements.md)    | Improvement plan for the live Python agent runtime — turn atomicity, prompt budgeting, streaming |
| [Agent Orchestration Audit](architecture/agent-orchestration-audit.md)      | Full architecture audit of agent/chat orchestration across both backends                         |
| [Agent Graph (legacy)](architecture/agent-graph.md)                         | Legacy `apps/api` LangGraph architecture — how ANIMA processes chat requests                     |
| [Agent Runtime Migration](architecture/agent-runtime-migration.md)          | Archived migration plan from LangGraph to the current orchestration loop                         |

---

## PRDs — [`prd/`](prd/)

| Document                                      | Status | Description                                                             |
| --------------------------------------------- | ------ | ----------------------------------------------------------------------- |
| [Encrypted Core v1](prd/encrypted-core-v1.md) | Draft  | Make the local Core encrypted-by-default and remove plaintext artifacts |

---

## Operations — [`ops/`](ops/)

| Document                                                  | Description                                                                                             |
| --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| [Python Backend Fix Plan](ops/python-backend-fix-plan.md) | Active fix plan for the Python backend                                                                  |
| [Implementation Plan](ops/implementation-plan.md)         | Historical brief for transforming the server into a portable, encrypted, memory-intelligent personal AI |

---

## Changelog

| Document                  | Description                                              |
| ------------------------- | -------------------------------------------------------- |
| [CHANGELOG](CHANGELOG.md) | Running changelog of documentation updates and revisions |
