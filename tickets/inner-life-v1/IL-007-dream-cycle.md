# IL-007 - Dream cycle (F5 extension)

- Status: done
- Priority: P2
- Scope: `apps/server/src/anima_server/services/agent/sleep_agent.py`, `apps/server/src/anima_server/services/agent/inner_life`, `apps/server/src/anima_server/models`
- Parent: `IL-000`
- Depends on: `IL-001`, `IL-002`, `IL-006`
- Owner: Claude
- PRD: docs/prds/presence/inner-life-v1.md
- Plan: docs/superpowers/plans/2026-07-15-inner-life-v1.md
- Created: 2026-07-15 16:55 MYT
- Updated: 2026-07-28 MYT
- Started: 2026-07-21 MYT
- Completed: 2026-07-23 MYT

## Goal

Add an idle-time dream cycle: during long-idle night windows, recombine important-but-cold memories and latent traces into a dream journal entry that feeds affect, initiative pressure, and optional "while you were away" surfacing.

## Deliverables

- Eligibility check on presence tick: idle ≥ 4 h, 00:00–06:00 local, ≤ 1 dream/night.
- Material selection: K = 3 items by `significance × coldness` where `coldness = 1 − rank_normalized(heat)` (raw F2 heat is unbounded), latent traces > 0.5 weight, one random old transcript fragment.
- Single extraction-model reflection pass producing a dream narrative; affect deltas at 25 % turn strength; touched memories reconsolidated at η = 0.02.
- `dream_journal` table (narrative, source refs, affect delta, timestamp), rolling cap 30, soul-store scoped.
- Share-worthy flagging raising `dream_residue` (IL-003); `presence_config.dream_sharing` gate (`off | on_ask | ambient`, default `on_ask`).

## Acceptance

- Never runs during an active session; nightly cap enforced.
- Journal entries carry full source provenance; identity-class content untouched.
- Affect deltas respect the 25 % scale (tests).

## Activity Log

- 2026-07-15 16:55 MYT - Ticket created.
- 2026-07-15 17:25 MYT - Dream sampling now uses rank-normalized coldness per review (raw heat exceeds 1, so `1 − heat` can go negative).
- 2026-07-21 MYT - Implemented. Pure math (`inner_life/dream.py`): night-window + idle + per-night-cap eligibility, rank-normalized coldness, significance×coldness weighted (seeded, without-replacement) sampling, 25% affect scaling, share-worthy detection. Edge (`inner_life/dream_edge.py`): `run_dream_for_user` — eligibility (incl. consuming IL-002's `dream_deferred` catch-up marker), active-DEK gate, material = sampled important-but-cold non-identity memories + latent traces ≥0.5 + one best-effort random transcript fragment, ONE extraction-model reflection call, then effects: `dream_journal` row (narrative field-encrypted, rolling cap 30), 25% affect nudge, η=0.02 reconsolidation on touched memories, share-worthy → IL3 `dream_residue`. Wired into the presence tick idle loop (sibling of the initiative tick). Un-stubbed `dream_residue` in IL-003. `presence_config.dream_sharing` (off|on_ask|ambient) across model/service/schema/route. Soul migration `20260721_0001` (dream_journal + dream_sharing) + legacy repair. dream_journal in vault export/import + eval-reset.
- 2026-07-23 MYT - Done: PR #116 (branch feature/il-007-dream-cycle) squash-merged to main as `bc7363c` (2026-07-23 02:16 MYT). Review rounds on the PR hardened right-to-forget (dream scrubbing on memory/latent-topic/derived-pattern forget, transcript-fragment source dropped), field-encrypted latent topic keys in dream source_refs (incl. vault-import re-keying), bounded failed dream attempts (marker committed before effects), non-finite delta rejection, configurable reconsolidation eta (full skip at eta<=0), and dreamSharing=off enforcement.
- 2026-07-28 MYT - Ticket closed out (status/dates updated, merged feature branch deleted); tracker table in IL-000 updated.

## Validation

- Commands:
  - `uv run pytest tests/test_inner_life_dream.py tests/test_inner_life_dream_edge.py -q` -> 37 passed (25 pure + 12 edge/integration)
  - focused inner_life + dashboard suite -> 201 passed
  - `bun run test` -> (full suite gate: see PR; expected 54-failure pre-existing baseline, zero IL-007 regressions)
  - `alembic -c alembic_core.ini heads` -> single head `20260721_0001`
- Changed paths:
  - `apps/server/src/anima_server/services/agent/inner_life/{dream,dream_edge}.py` (new)
  - `apps/server/src/anima_server/services/agent/inner_life/{initiative,presence}.py`
  - `apps/server/src/anima_server/services/agent/templates/prompts/dream_narrative.md.j2` (new)
  - `apps/server/src/anima_server/models/{agent_runtime,presence,__init__}.py`
  - `apps/server/alembic_core/versions/20260721_0001_add_dream_journal.py` (new)
  - `apps/server/src/anima_server/db/session.py`
  - `apps/server/src/anima_server/services/{presence_config,vault,eval_reset}.py`
  - `apps/server/src/anima_server/{schemas/presence.py,api/routes/presence.py,services/agent/prompt_loader.py}`
  - `apps/server/tests/{test_inner_life_dream,test_inner_life_dream_edge}.py` (new), `tests/{test_inner_life_initiative,test_dashboard_api}.py`
- Notes:
  - Never runs during an active session (idle-only loop); nightly cap + idle≥4h + 00:00–06:00 gates; requires an active memories DEK (no plaintext at rest, mirrors IL3).
  - Identity-class memories excluded from dream material; touched memories reconsolidated at η=0.02 (identity confined to confidence-only inside `apply_reconsolidation`).
  - Dream narration uses the EXTRACTION model (not the main agent model) via an explicitly-built client.
  - Client-side surfacing UI (dream_sharing controls, "while you were away") is out of scope here → follow-up.
