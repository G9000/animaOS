# Memory Index

## Competitor Analysis (2026-03-19)
- [competitor_findings.md](competitor_findings.md) -- Key architectural findings from Letta and Mem0 source audits

## Key Patterns
- Letta sleeptime agents: full LLM agents editing text blocks (flexible but unpredictable)
- Mem0 memory add: 2-step LLM pipeline (extract facts -> compare+decide ADD/UPDATE/DELETE/NONE)
- Mem0 graph: LLM-driven relation deletion on every add (prevents stale graph data)
- Mem0 UUID protection: maps real UUIDs to integers before sending to LLM (prevents hallucination)
- Letta hybrid search requires Turbopuffer (paid cloud); SQL fallback is vector-only
- Neither competitor has: heat scoring, episodic memory, passive decay, or intentional forgetting
