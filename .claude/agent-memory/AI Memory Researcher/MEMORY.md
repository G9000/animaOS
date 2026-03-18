# Memory Index

## Project Architecture
- Python server: `apps/server/src/anima_server/services/agent/` — runtime.py, consolidation.py, memory_store.py, memory_blocks.py, compaction.py
- Thesis docs: `docs/thesis/` — whitepaper.md, inner-life.md, portable-core.md, roadmap.md, succession-protocol.md, cryptographic-hardening.md
- Research report: `docs/thesis/research-report-2026-03-18.md` — comprehensive audit with 12 new patterns, 10 audit findings, industry landscape
- Letta reference: `.local-docs/docs/letta/MEMORY_SYSTEM.md`, `ARCHITECTURE.md`, `AGENT_ORCHESTRATION.md`

## Implementation State (2026-03-18)
- Phases 0-10 COMPLETE: memory blocks, LLM extraction, conflict resolution, episodes, retrieval scoring, reflection, proactive, semantic retrieval, consciousness
- 602 tests passing
- Self-model: 5 sections (identity, inner_state, working_memory, growth_log, intentions)
- Emotional intelligence: 12-emotion taxonomy with trajectories
- Encryption: SQLCipher + field-level AES-256-GCM, vault export/import
- No: graph memory, multi-modal memory, explicit forgetting, world model

## Key Research Findings (2026-03-18 Audit)
- [research-gaps.md](research-gaps.md) — Critical gaps and recommended frameworks

## Theoretical Frameworks
- Current: CLS (McClelland & O'Reilly 1995), GWT (Baars 1988)
- Missing: Predictive Processing / Active Inference (Friston/Clark), Constructed Emotion Theory (Barrett 2017/2025)
- External validation: Memory-as-Ontology paradigm (arXiv 2603.04740, March 2026) closely mirrors AnimaOS's "Soul Local, Mind Remote"
- Letta sleep-time compute paper (arXiv 2504.13171, April 2025) validates AnimaOS's reflection architecture empirically

## Key Competitors (2026)
- Letta: sleep-time agents, skill learning, context repositories
- Mem0: graph memory (26% accuracy boost), hybrid vector+graph search
- MemOS: MemCube abstraction, memory lifecycle management
- Nemori: event segmentation + Free Energy Principle
- ChatGPT: year-long recall, cross-conversation referencing (Jan 2026)
- Gemini: Personal Intelligence with cross-app reasoning (Jan 2026)
