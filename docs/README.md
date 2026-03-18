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

See [Architecture README](architecture/README.md) for the full index, organized by domain:
- **[System](architecture/system/)** — directory structure, API routes, services, database schema, data flow, configuration, cross-cutting concerns
- **[Agent](architecture/agent/)** — agent runtime deep dive, tool catalog
- **[Memory](architecture/memory/)** — memory system, implementation plan, competitor repo analysis
- **[Crypto](architecture/crypto/)** — encryption, session management, key derivation

---

## PRDs — [`prds/`](prds/)

See [PRDs README](prds/README.md) for the full index, organized by domain:
- **Memory Retrieval & Search** — F1 Hybrid Search, F2 Heat Scoring
- **Memory Consolidation & Learning** — F3 Predict-Calibrate, F6 Batch Segmentation, F7 Intentional Forgetting
- **Knowledge Representation** — F4 Knowledge Graph
- **Background Processing** — F5 Async Sleep Agents
- **Cryptography & Security** — Encrypted Core v1, Crypto Hardening

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
