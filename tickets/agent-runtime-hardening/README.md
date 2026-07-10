# Agent Runtime Hardening Tickets

Issue-style tickets for the Agent Runtime Hardening initiative.

The initiative fixes the silent-failure, cost, durability, and drift problems found in the 2026-07-07 four-track runtime review: dead LLM compaction on Anthropic, stranded runs on disconnect, background writes that erase user memory, missing prompt caching, LLM work redone on unchanged inputs, and duplicated turn logic that has already drifted.

Source artifacts:

- Implementation plan: `docs/superpowers/plans/2026-07-07-agent-runtime-hardening.md`
- Parent tracker: `ARH-000-parent.md`

All file:line references are anchors as of commit `1f661721` — re-verify before editing.
