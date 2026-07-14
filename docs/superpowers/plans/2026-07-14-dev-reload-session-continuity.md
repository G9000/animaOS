# Dev Reload Session Continuity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep a logged-in desktop session valid across backend child reloads within one root `bun dev` run, stabilize reload scheduling, and prevent embedding-only Ollama models from breaking LLM compaction.

**Architecture:** The root launcher creates an ephemeral AES key and temporary snapshot path that only server children inherit. The Python session store transactionally persists authenticated encrypted state under one lock, while a health-gated scheduler coalesces reloads. Shared background-chat target resolution and Ollama completion-capability validation keep extraction and primary model roles correct.

**Tech Stack:** Bun/TypeScript tests, Node.js launcher APIs, Python 3.12, FastAPI, `cryptography` AES-GCM, pytest, httpx.

**Spec:** `docs/superpowers/specs/2026-07-14-dev-reload-session-continuity-design.md`

---

## File map

- Create `apps/server/src/anima_server/services/dev_session_snapshot.py`: parse dev-only environment, encrypt/decrypt versioned snapshots, and atomically replace ciphertext.
- Create `apps/server/tests/test_dev_session_continuity.py`: process-like restore, failure, revocation, expiry, and concurrency coverage.
- Modify `apps/server/src/anima_server/services/sessions.py`: put unlock sessions and SQLCipher key behind one `RLock`, restore once, and transactionally persist prospective state.
- Modify `scripts/dev-root-lib.mjs`: create/clean the parent-scoped continuity environment and expose a reusable reload scheduler.
- Modify `scripts/dev-root.mjs`: pass continuity only to server children and use the health-gated scheduler.
- Modify `tests/dev-root.test.ts`: cover environment scoping, cleanup, coalescing, pending reloads, readiness, and stop behavior.
- Modify `apps/server/src/anima_server/services/agent/llm.py`: define ordered provider/model background chat targets.
- Modify `apps/server/src/anima_server/services/agent/compaction.py`: try extraction then primary targets, closing each client, and degrade only after all fail.
- Modify `apps/server/src/anima_server/services/agent/batch_segmenter.py`: reuse provider/model targets and continue after an optional target failure.
- Modify `apps/server/tests/test_agent_llm.py`, `test_active_recall.py`, and `test_batch_segmenter.py`: target resolution and fallback regressions.
- Modify `apps/server/src/anima_server/api/routes/config.py`: preflight Ollama completion capabilities before mutating settings.
- Modify `apps/server/tests/test_dashboard_api.py`: accepted completion model, rejected embedding-only model, provider failure, and no-partial-mutation tests.

---

### Task 1: Root launcher continuity scope and reload scheduler

**Files:**
- Modify: `tests/dev-root.test.ts`
- Modify: `scripts/dev-root-lib.mjs`
- Modify: `scripts/dev-root.mjs`

- [ ] **Step 1: Write failing continuity-environment tests**

Add tests for a helper with this public shape:

```ts
const continuity = createDevSessionContinuity({ tempRoot, randomBytesImpl });
expect(continuity.serverEnv.ANIMA_DEV_SESSION_STATE_PATH).toStartWith(tempRoot);
expect(continuity.serverEnv.ANIMA_DEV_SESSION_KEY).toBeTruthy();
continuity.cleanup();
expect(existsSync(continuity.directory)).toBe(false);
```

Also assert `buildTargetEnvironment("server", baseEnv, continuity.serverEnv)` includes both variables while `desktop` and `anima-mod` receive only `baseEnv`.

- [ ] **Step 2: Run the new environment tests and verify RED**

Run: `bun test tests/dev-root.test.ts`

Expected: FAIL because `createDevSessionContinuity` and `buildTargetEnvironment` do not exist.

- [ ] **Step 3: Implement ephemeral continuity helpers**

In `scripts/dev-root-lib.mjs`, use `mkdtempSync(path.join(tempRoot, "anima-dev-session-"))`, `randomBytes(32).toString("base64")`, and an idempotent `rmSync(directory, { recursive: true, force: true })`. Return frozen `serverEnv`, `directory`, and `cleanup` fields. Implement target environment selection without mutating the base object.

In `scripts/dev-root.mjs`, create continuity before starting the stack, pass the additional environment only from `spawnNxDevTarget("server")`, and invoke cleanup on normal completion, SIGINT, SIGTERM, and startup failure.

- [ ] **Step 4: Run launcher tests and verify GREEN**

Run: `bun test tests/dev-root.test.ts`

Expected: all launcher tests pass.

- [ ] **Step 5: Write failing scheduler tests**

Add deterministic tests around this interface:

```ts
const scheduler = createServerReloadScheduler({
  quietMs: 5,
  restart: async () => events.push("restart"),
  waitForReady: async () => events.push("ready"),
  onError: (error) => failures.push(error),
});
scheduler.schedule();
scheduler.schedule();
await scheduler.whenIdle();
expect(events).toEqual(["restart", "ready"]);
```

Cover a `schedule()` during a blocked active reload producing exactly one later reload, health completing before idle, `stop()` cancelling queued work, and errors reaching `onError`.

- [ ] **Step 6: Run scheduler tests and verify RED**

Run: `bun test tests/dev-root.test.ts`

Expected: FAIL because `createServerReloadScheduler` does not exist.

- [ ] **Step 7: Implement the health-gated scheduler and watcher integration**

Implement a scheduler that tracks `timer`, `running`, `pending`, `stopped`, and idle waiters. `schedule()` marks pending and resets the quiet timer when idle. The run loop clears pending, awaits `restart()`, then `waitForReady()`, and schedules one later quiet-period run when events arrived while active. `stop()` prevents new work and resolves idle waiters after active work settles.

Replace the watcher-local timer/boolean with this scheduler. Retain the exact trigger-path log, set the latest trigger before `schedule()`, wait on the existing `/health`, log readiness, and route readiness failure through root-stack teardown.

- [ ] **Step 8: Run all launcher tests and commit**

Run: `bun test tests/dev-root.test.ts`

Expected: PASS.

Commit:

```powershell
git add tests/dev-root.test.ts scripts/dev-root-lib.mjs scripts/dev-root.mjs
git -c commit.gpgsign=false commit -m "dev: stabilize authenticated server reloads"
```

---

### Task 2: Encrypted process-reload session snapshot

**Files:**
- Create: `apps/server/src/anima_server/services/dev_session_snapshot.py`
- Create: `apps/server/tests/test_dev_session_continuity.py`
- Modify: `apps/server/src/anima_server/services/sessions.py`

- [ ] **Step 1: Write failing encrypted snapshot tests**

Test a small storage class directly with a temporary path and known 32-byte key:

```python
snapshot = DevSessionSnapshot(path=tmp_path / "state.bin", key=b"k" * 32)
snapshot.write({"version": 1, "sessions": [], "sqlcipherKey": None})
assert snapshot.load()["version"] == 1
assert b'"sessions"' not in snapshot.path.read_bytes()
```

Add tamper, wrong-key, malformed-environment, and atomic replace failure cases. Assert invalid decrypt raises the module's snapshot error and no plaintext secrets appear in the file.

- [ ] **Step 2: Run snapshot tests and verify RED**

Run: `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; uv run --project apps/server pytest apps/server/tests/test_dev_session_continuity.py -q`

Expected: collection failure because the snapshot module does not exist.

- [ ] **Step 3: Implement the snapshot storage module**

Implement constants for environment names, magic/version, 12-byte nonce, and fixed AAD. `from_environment()` returns `None` unless both values are present and valid.

Use this exact decrypted JSON schema:

```json
{
  "version": 1,
  "sessions": [
    {
      "token": "non-empty URL-safe bearer token",
      "userId": 1,
      "expiresAt": "2026-07-15T10:20:30.123456Z",
      "deks": {
        "memories": "base64-encoded 32-byte key"
      }
    }
  ],
  "sqlcipherKey": "base64-encoded 32-byte key or null"
}
```

Encode every binary key with standard base64 and decode with `validate=True`. Encode expiry as UTC RFC3339 with a literal `Z`; parsing must produce an aware UTC `datetime`. Strictly reject non-object roots, unknown versions, non-list sessions, non-object session rows, empty/non-string tokens or domain names, booleans/non-integer/negative user IDs, invalid/naive expiries, non-object DEK maps, invalid base64, and decoded keys that are not exactly 32 bytes. Reject duplicate tokens rather than silently choosing a row. Unknown top-level or row fields may be ignored for forward-compatible additive metadata, but every required field must be present.

`write()` serializes compact sorted JSON, encrypts with `AESGCM`, writes `magic + nonce + ciphertext` to a unique sibling temp file opened exclusively, flushes and `os.fsync()`s it, then calls `os.replace`. Any exception before successful replace removes only the temp file and re-raises, leaving the prior ciphertext byte-for-byte intact. `load()` verifies minimum length and magic before decrypting and returns only the strictly decoded typed payload. Never include keys, tokens, or ciphertext bodies in errors/logs.

- [ ] **Step 4: Run snapshot tests and verify GREEN**

Run the command from Step 2.

Expected: snapshot storage cases pass.

- [ ] **Step 5: Write failing session round-trip and security tests**

Construct `UnlockSessionStore(snapshot=...)`, create a token, set the SQLCipher key through the store, construct a fresh store from the same snapshot, and assert token, DEKs, expiry, and SQLCipher key restore. Add tests for:

- expired tokens never resolve after restore;
- DB-viewer verification timestamps do not restore;
- revoke, revoke-user, clear, and SQLCipher clear remain absent after restore;
- injected write failure does not commit a security-reducing in-memory mutation;
- concurrent create/revoke/key operations complete without deadlock and restore a coherent final snapshot;
- no snapshot argument preserves existing in-memory behavior.

- [ ] **Step 6: Run session tests and verify RED**

Run the command from Step 2.

Expected: FAIL because `UnlockSessionStore` has no snapshot injection or instance SQLCipher state.

- [ ] **Step 7: Refactor session state under one transactional lock**

Change the store lock to `RLock`; add instance `_sqlcipher_key`; make module-level SQLCipher helpers delegate to the global store. Restore the typed, strictly validated sessions and key in `__init__`. The store owns conversion between `UnlockSession` objects and the snapshot schema: token/user ID copy directly, expiry uses the UTC `Z` representation, and each 32-byte domain/SQLCipher key uses standard base64.

For each mutation, build prospective dictionaries/key under the lock, persist the prospective payload first, then swap the in-memory fields and zero only secrets removed by the committed state. A `DevSessionSnapshot.write()` exception propagates before any in-memory assignment. Rebuild `_latest_deks_by_user` and filter DB-viewer tokens after commits. Resolve and restore independently reject expired timestamps even if cleanup persistence fails. A malformed or undecryptable startup snapshot is logged and ignored; the store starts empty and locked.

- [ ] **Step 8: Run focused auth suites and commit**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'
uv run --project apps/server pytest apps/server/tests/test_dev_session_continuity.py apps/server/tests/test_auth.py apps/server/tests/test_corefs_keyslots.py -q
```

Expected: PASS.

Commit:

```powershell
git add apps/server/src/anima_server/services/dev_session_snapshot.py apps/server/src/anima_server/services/sessions.py apps/server/tests/test_dev_session_continuity.py
git -c commit.gpgsign=false commit -m "server: preserve unlock state across dev reloads"
```

---

### Task 3: Background chat target resolution and fallback

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/llm.py`
- Modify: `apps/server/src/anima_server/services/agent/compaction.py`
- Modify: `apps/server/src/anima_server/services/agent/batch_segmenter.py`
- Modify: `apps/server/tests/test_agent_llm.py`
- Modify: `apps/server/tests/test_active_recall.py`
- Modify: `apps/server/tests/test_batch_segmenter.py`

- [ ] **Step 1: Write failing target-resolution tests**

Add `ChatTarget(provider, model)` expectations for extraction-provider precedence, fallback to the primary provider when extraction provider is blank, empty extraction model, duplicate removal, and scaffold filtering.

- [ ] **Step 2: Run target tests and verify RED**

Run: `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; uv run --project apps/server pytest apps/server/tests/test_agent_llm.py -q`

Expected: FAIL because `resolve_background_chat_targets` and `ChatTarget` do not exist.

- [ ] **Step 3: Implement shared target resolution**

Add an immutable `ChatTarget` dataclass and a resolver accepting explicit values for unit tests while defaulting to settings. Return ordered, non-empty, non-scaffold, deduplicated `(provider, model)` pairs: extraction first, primary second.

- [ ] **Step 4: Write failing compaction and segmentation fallback tests**

For compaction, configure extraction `ollama/all-minilm:latest`, primary `ollama/qwen3:14b`, make the first client raise a permanent 400 and the second return a summary, and assert both clients close and the summary succeeds. Add all-fail and empty-output cases.

For segmentation, assert a failed extraction target continues to the primary provider/model, parses the result, and closes both clients.

- [ ] **Step 5: Run fallback tests and verify RED**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'
uv run --project apps/server pytest apps/server/tests/test_active_recall.py apps/server/tests/test_batch_segmenter.py -q
```

Expected: FAIL because both callers still bind only `settings.agent_provider` and do not fall back after invocation failure.

- [ ] **Step 6: Implement target loops and scoped logging**

Update compaction and segmentation to create one client per `ChatTarget`, close it in `finally`, and proceed after invocation, empty-output, or parse failure when another target exists. Log a concise target-to-target fallback warning. Only compaction's terminal all-target failure logs degraded traceback and returns `None`; its caller retains deterministic summary fallback.

- [ ] **Step 7: Run focused LLM tests and commit**

Run the test commands from Steps 2 and 5.

Expected: PASS.

Commit:

```powershell
git add apps/server/src/anima_server/services/agent/llm.py apps/server/src/anima_server/services/agent/compaction.py apps/server/src/anima_server/services/agent/batch_segmenter.py apps/server/tests/test_agent_llm.py apps/server/tests/test_active_recall.py apps/server/tests/test_batch_segmenter.py
git -c commit.gpgsign=false commit -m "server: fall back across background chat models"
```

---

### Task 4: Ollama completion-capability validation

**Files:**
- Modify: `apps/server/src/anima_server/api/routes/config.py`
- Modify: `apps/server/tests/test_dashboard_api.py`

- [ ] **Step 1: Write failing configuration validation tests**

Mock `POST /api/show` metadata through a helper seam. Cover:

- primary and extraction models containing `completion` return `200` and persist;
- `all-minilm:latest` with only `embedding` returns `422` and a completion-capability message;
- unreachable or malformed Ollama metadata returns `503`/`422` without changing provider, model, extraction model, base URL, or persisted settings;
- duplicate primary/extraction model validates once.

- [ ] **Step 2: Run config tests and verify RED**

Run: `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; uv run --project apps/server pytest apps/server/tests/test_dashboard_api.py -q`

Expected: FAIL because configuration accepts embedding-only models without preflight.

- [ ] **Step 3: Implement pre-mutation Ollama validation**

Add an async helper that normalizes the native Ollama base URL, calls `POST /api/show` with `{"model": model}`, validates an object response with a string capability list, and requires `completion`. In `update_config`, compute all prospective values first, validate distinct non-empty Ollama chat models, and only then mutate settings, API keys, base URL, persisted config, and caches. Convert reachability errors to `503` and unsupported capabilities/malformed metadata to `422`.

- [ ] **Step 4: Run config and security suites and commit**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'
uv run --project apps/server pytest apps/server/tests/test_dashboard_api.py apps/server/tests/test_security_hardening.py -q
```

Expected: PASS.

Commit:

```powershell
git add apps/server/src/anima_server/api/routes/config.py apps/server/tests/test_dashboard_api.py
git -c commit.gpgsign=false commit -m "server: reject embedding-only Ollama chat models"
```

---

### Task 5: Integrated verification and PR readiness

**Files:**
- Modify only if verification exposes a regression in an already-touched path.

- [ ] **Step 1: Run all focused regressions together**

```powershell
bun test tests/dev-root.test.ts apps/desktop/tests/api-auth.test.ts
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'
uv run --project apps/server pytest apps/server/tests/test_dev_session_continuity.py apps/server/tests/test_auth.py apps/server/tests/test_corefs_keyslots.py apps/server/tests/test_agent_llm.py apps/server/tests/test_active_recall.py apps/server/tests/test_batch_segmenter.py apps/server/tests/test_dashboard_api.py apps/server/tests/test_security_hardening.py -q
```

Expected: PASS with zero failures.

- [ ] **Step 2: Run repository validation**

Run separately and require exit code 0:

```powershell
bun run lint
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test
bun run build
```

- [ ] **Step 3: Smoke-test root reload continuity**

Start `bun dev`, wait for `/health`, register or log in through the API, retain the unlock token, touch two disposable backend Python-file timestamps inside one quiet window, and verify:

- one reload batch is logged;
- replacement server health returns `200`;
- `GET /api/auth/me` with the pre-reload token returns `200`;
- stopping and starting a new `bun dev` makes the old token return `401`.

Restore timestamps/content without changing source bytes. Do not commit runtime `.anima` data or temporary files.

Use these concrete PowerShell commands from a second terminal while `bun dev` runs in the worktree:

```powershell
$probe = Resolve-Path 'apps/server/src/anima_server/__init__.py'
$before = (Get-Item -LiteralPath $probe).LastWriteTimeUtc
(Get-Item -LiteralPath $probe).LastWriteTimeUtc = [DateTime]::UtcNow
Start-Sleep -Milliseconds 100
(Get-Item -LiteralPath $probe).LastWriteTimeUtc = [DateTime]::UtcNow
# after authenticated health/me checks, restore the original timestamp
(Get-Item -LiteralPath $probe).LastWriteTimeUtc = $before
```

Capture the login token without printing it, call `Invoke-RestMethod -Uri 'http://127.0.0.1:3031/api/auth/me' -Headers @{ 'x-anima-unlock' = $token }` after the readiness log, then stop the root process, start a new one, and assert the same request returns HTTP 401.

- [ ] **Step 4: Audit diff and history**

Run:

```powershell
git status --short
git diff --check origin/main...HEAD
git diff --stat origin/main...HEAD
git log --oneline origin/main..HEAD
```

Confirm only the spec, plan, launcher/tests, auth snapshot/session tests, LLM fallback/tests, and config validation/tests are present.

- [ ] **Step 5: Apply final review fixes, then re-run affected and full verification**

Use `superpowers:requesting-code-review`. Any production correction starts with a failing regression test and repeats the relevant focused command plus Step 2 before completion.

- [ ] **Step 6: Push and open a draft PR**

Use `github:yeet` to push `codex/dev-reload-session-continuity` and open a draft PR against `main`. Include behavior, affected areas, security boundary, test evidence, and manual reload smoke results. Do not include the unrelated main-worktree `apps/desktop/src-tauri/Cargo.toml` change.
