# Dev Reload Session Continuity and Compaction Model Safety Design

**Date:** 2026-07-14

## Context

Root `bun dev` owns the Python server, desktop, and anima-mod processes. Its backend watcher force-replaces the Python process after changes to server Python or configuration files. Unlock tokens, decrypted domain keys, and the active SQLCipher key are process-local, so every intentional backend reload currently turns a valid desktop token into `401 Session locked` and signs the user out.

The same captured runtime session also exposed a separate compaction problem: a configured Ollama embedding-only model such as `all-minilm` can be sent to the chat-completions API. Compaction then receives `400 Bad Request` and degrades to a line-based summary even when a valid primary chat model is configured.

## Goals

- Preserve valid unlock sessions across Python child-process reloads during one root `bun dev` lifetime.
- Keep a full stop and subsequent `bun dev` launch locked.
- Preserve session expiry and logout/revocation behavior across child reloads.
- Batch rapid backend saves into stable, health-gated reloads without losing changes that arrive during a reload.
- Prevent embedding-only Ollama models from being saved or used as chat/extraction models.
- Respect the configured extraction provider and fall back to the primary chat target when optional extraction fails.
- Keep production and packaged runtime authentication behavior unchanged.

## Non-goals

- Persisting unlock state across a full animaOS or `bun dev` restart.
- Storing decrypted keys durably in `.anima/`, SQLCipher, browser storage, or repository files.
- Making every failed or non-idempotent request automatically retry across the short reload window.
- Redesigning provider configuration or the AI settings screen.
- Generalizing the dev snapshot into a production credential service.

## Design

### 1. Root-dev ephemeral continuity scope

At root `bun dev` startup, `scripts/dev-root.mjs` creates a unique temporary directory and a random 256-bit continuity key. Only server child processes receive two environment variables: the snapshot path and base64-encoded key. Desktop and anima-mod children do not receive them.

The server enables snapshot behavior only when both values are present and valid. Normal `dev:server`, tests, packaged execution, and production therefore retain the current process-local behavior by default.

When root `bun dev` exits through its normal signal or child-exit paths, it removes the temporary directory. If the parent crashes, an encrypted orphan may remain, but its random key existed only in the dead parent environment and a later launch uses a different directory and key.

### 2. Auth snapshot format and lifecycle

A focused server module owns encrypted snapshot serialization. It uses AES-256-GCM from the existing `cryptography` dependency, a versioned JSON payload, a fresh nonce per write, and fixed associated data identifying the snapshot format. Writes go to a sibling temporary file followed by atomic replacement.

The encrypted payload contains only the state required for reload continuity:

- unlock token, user ID, expiry, and per-domain DEKs for each live session;
- the active SQLCipher key, when set.

The derived latest-DEK cache is rebuilt from restored sessions. DB-viewer verification timestamps are deliberately not restored, so sensitive database inspection still requires re-verification after a reload.

The runtime session store owns both unlock sessions and the active SQLCipher key behind one re-entrant state lock; the existing module-level SQLCipher helpers delegate to that store. This removes cross-lock snapshot races and gives every snapshot one coherent view of both kinds of secret state.

`UnlockSessionStore.create`, `revoke`, `revoke_user`, expiry purging, and `clear`, plus the delegated SQLCipher set/clear methods, synchronously persist state while dev continuity is enabled. Each mutation computes its prospective state under the shared lock. Security-reducing operations such as revoke, clear, and key clear atomically replace the encrypted snapshot with that prospective state before committing the same state in memory or reporting success. If persistence fails, the operation raises and leaves the previous in-memory state consistent with the previous snapshot; it must not claim a successful logout or revocation that a reload could undo. Session creation and key installation use the same serialized transaction so a force-kill cannot capture a partially updated combination. State changes are infrequent and the file is small, so synchronous persistence is acceptable.

Expiry remains fail-closed even if cleanup persistence fails: every resolve and restore validates the signed expiry timestamp independently, so stale ciphertext cannot make an expired token valid. A cleanup write failure may leave expired ciphertext on disk, but a later child discards it again rather than restoring it.

On server import/startup, a fresh store attempts one restore. Missing, malformed, expired, tampered, wrong-key, or unsupported-version snapshots are ignored with a concise warning and the runtime remains locked. Restored expired sessions are discarded and the cleaned state is persisted when possible. Restore and cleanup failures must never make server startup fail; mutation persistence failures after startup do fail the affected operation so stale durable state is never reported as successfully revoked.

### 3. Stable reload controller

The watcher becomes a small testable reload scheduler instead of a single `restarting` boolean:

- A relevant file event resets a quiet-period timer so a multi-file edit batch produces one reload.
- When the timer fires, the current server tree is terminated and one replacement is spawned.
- The scheduler waits for `/health` before declaring the reload complete.
- Relevant events received while termination, startup, or health waiting is in progress mark another reload pending. After the active reload becomes healthy, the pending batch observes the same quiet period and reloads once.
- Shutdown cancels timers, closes the watcher, and prevents further spawns.

The existing exact trigger-path log is retained. Successful reloads add one readiness log; failed readiness exits the root stack through the existing failure path rather than leaving a misleadingly alive launcher.

### 4. Background chat-target resolution

A shared resolver returns ordered, deduplicated chat targets:

1. configured extraction provider/model, using `agent_extraction_provider` when set and otherwise the primary provider;
2. primary `agent_provider` and `agent_model`.

Callers that use an optional lightweight model, beginning with compaction and batch segmentation, try these targets in order. Each client is closed before moving to the next target. A failed optional target produces a concise fallback warning; a full traceback is logged only when all targets fail and the caller must degrade.

Compaction returns the first non-empty summary. If every chat target fails or returns empty output, it retains the existing deterministic text-summary fallback.

### 5. Ollama completion-capability validation

For Ollama configuration updates, the server queries `POST /api/show` for each non-empty primary or extraction model being saved. The response `capabilities` list must contain `completion`. A model that reports only `embedding`, including `all-minilm`, is rejected with `422` and a clear message before settings mutate or persist.

If Ollama cannot be reached or returns malformed metadata, the save fails without partially changing active settings. Cloud providers and custom vLLM endpoints retain their existing validation behavior because they do not expose Ollama capability metadata.

Runtime fallback remains necessary for existing persisted configuration, models removed after saving, and provider failures after a valid save.

## Security Properties

- Plaintext DEKs and the SQLCipher key are never written to disk.
- The continuity key is random per root `bun dev` parent and is never persisted.
- Snapshot ciphertext is authenticated; tampering or using a snapshot from another parent fails closed.
- A full root-dev stop destroys continuity and requires login next launch.
- Logout and user revocation update the encrypted snapshot immediately, so revoked tokens do not reappear after a child reload.
- A logout or revocation is reported successful only after its prospective encrypted state is durable; failed persistence cannot leave memory and the reload snapshot claiming different security state.
- Production behavior is opt-in by environment presence and remains unchanged by default.

## Error Handling and Observability

- Snapshot initialization and persistence errors log sanitized paths and exception classes without key or token material.
- Invalid snapshots are removed or overwritten only inside the parent-owned temporary directory.
- Reload logs identify the triggering server path and report health readiness.
- Optional model fallback logs provider/model names and the selected fallback without dumping response bodies containing provider details.
- Configuration validation returns actionable client errors and leaves the previous settings intact.

## Testing

### Launcher tests

- Server children receive the continuity environment; desktop and anima-mod do not.
- The temporary continuity directory is unique and cleanup removes it.
- Rapid file events coalesce into one reload.
- Events during an active reload schedule exactly one later reload.
- A reload is incomplete until health succeeds; shutdown prevents further spawns.

### Server auth tests

- A created session and SQLCipher key round-trip through an encrypted snapshot into fresh process-like state.
- Expired sessions are not restored.
- Revoke, revoke-user, clear, and SQLCipher clear survive restoration.
- Fault-injected persistence failures during revoke, revoke-user, clear, and SQLCipher clear do not commit divergent in-memory state or report success.
- Concurrent session and SQLCipher mutations serialize into coherent snapshots without deadlock or mixed generations.
- Tampered ciphertext, wrong keys, malformed payloads, and missing environment fail closed without breaking startup.
- DB-viewer verification timestamps are not restored.
- Snapshot behavior is disabled when either environment value is absent.

### Provider and compaction tests

- Extraction provider/model precedes and deduplicates against the primary target.
- Compaction falls back from a failing extraction model to the primary model and closes both clients.
- Full target failure retains deterministic summary behavior and degraded logging.
- Ollama configuration accepts a model with `completion`, rejects embedding-only models, and performs no partial mutation on validation failure.
- Existing launcher, desktop auth, config API, compaction, and batch-segmentation tests remain green.

## Validation

- Focused Bun launcher and desktop auth suites.
- Focused Python auth, config, compaction, and segmentation suites with `ANIMA_CORE_REQUIRE_ENCRYPTION=false` where required.
- `bun run lint`, `bun run test`, and `bun run build`.
- Manual root `bun dev` smoke test: log in, edit two backend Python files, observe one health-gated reload, confirm authenticated `/api/auth/me` and chat continue, stop and relaunch root dev, confirm a fresh login is required, and verify `GET /health` returns `200`.
