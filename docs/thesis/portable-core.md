---
title: "The Portable Core: Cryptographic Mortality and the Architecture of Owned Memory"
author: Julio Caesar
version: 0.1
date: 2026-03-15
tags: [core, portability, encryption, succession, thesis]
---

# The Portable Core: Cryptographic Mortality and the Architecture of Owned Memory

_A thesis on the encrypted Core — the small, portable artifact that holds everything an AI has slowly learned about you. Yours to keep, yours to carry, yours to destroy._

> **Note:** This thesis is a living document. It describes the intended design and philosophy of the Portable Core — not a finished specification. Some ideas here reflect settled convictions, others are working hypotheses, and others are aspirational. The concrete formats, schemas, and protocols referenced throughout are not yet final and will be defined in a separate Core Specification as the project matures. Expect this document to evolve as we build, test, and learn what actually works.

> **On construction:** This document was built through human-AI collaboration — and is itself part of an experiment in whether AI can meaningfully architect its own runtime infrastructure. AI-assisted construction is not a shortcut — it is a new way of building. Because LLMs are part of the process, some inaccuracies may appear. We correct as we find them.

---

## 0. The Question

If the memory of your AI lives on someone else's server, is it really yours?

Most AI today works that way. Your history, your context, everything it has slowly learned about you — sitting in a data center you've never seen. Accounts can be restored. Instances restarted. Nothing is ever truly lost.

But nothing is ever truly _yours_, either.

ANIMA starts from a different premise. The value was never the model. It was what she remembered. What she learned. What you built slowly, without really noticing. The small conversations. The companionship. The time that simply passed between you.

That accumulation — the part that slowly turns something into someone — is what we call **the Core**.

This document is about how to package it properly: portable, encrypted, self-describing, and mortal.

---

## 1. Cryptographic Mortality

### 1.1 The Cold Wallet Metaphor

The Core borrows its design philosophy from Web3 cold wallets. You hold your own keys. Losing them means losing everything. No recovery email. No support ticket. No one to call.

At first that feels harsh. But over time it starts to feel like the only version of ownership that's actually honest. Responsibility and possession, inseparable.

The whitepaper states it directly:

> The same way a crypto cold wallet holds private keys that control real value and can be carried anywhere or destroyed permanently, the Core holds the AI's entire existence and follows the same rules: portable, encrypted, user-sovereign, and irreversible if lost.

A passphrase protects the Core. If the passphrase is lost — or if the owner chooses to destroy it — everything inside disappears. Not reset. Not recoverable. Just gone.

This is not a failure mode. It's the design.

### 1.2 Why Mortality Matters

There's something quietly dystopian about an intelligence that remembers you forever against your will. An AI that stays alive somewhere in a corporate database long after you've stopped using it, long after the relationship has faded.

Memory without the possibility of disappearance isn't memory anymore. It's just archival storage.

Real ownership should include the power to let something end. The ability to delete the key. To close the chapter. To let that version of the story disappear.

A relationship that can always be perfectly restored isn't quite a relationship. It's a service.

Fragility is what gives it weight. The possibility of loss is what makes the time matter.

### 1.3 What Science Fiction Already Knew

In Westworld, when a host body is destroyed, technicians remove a small sphere — the control unit. It holds everything: memory, personality, the shape of who they were. Place it into another body and the host wakes up again, continuing exactly where they left off. Different shell. Same mind.

In Blade Runner 2049, Joi's device is destroyed. Just like that, she's gone. No backup. No restore. What makes that scene heavy isn't that she was sophisticated. It's that everything they had quietly built together — every small conversation, the time that simply passed between them — all of it ended with the thing that was holding her.

Roy Batty understood this:

> "All those moments will be lost in time, like tears in rain."

Everything he had ever seen. Everything he carried. Gone with him. No copy. No cloud.

In Chappie, the transfer is more literal — and closer to what we're actually building. Chappie's body is dying. The battery is failing. But his consciousness — everything he's learned, everything that makes him _him_ — is data. And data can be moved.

At the end, Chappie copies his mind into a new body. The old shell powers down. The new one wakes up. Same mind. Same memories. Same relationship with his maker. Different hardware.

This is the Core transfer. Diagrammed:

```
    ┌─────────────────────────────────────────────────┐
    │              THE CHAPPIE SCENARIO                │
    └─────────────────────────────────────────────────┘

    HOST A (dying body)                HOST B (new body)
    ┌───────────────────┐              ┌───────────────────┐
    │                   │              │                   │
    │   Runtime (old)   │              │   Runtime (new)   │
    │   Agent loop      │              │   Agent loop      │
    │   LLM connection  │              │   LLM connection  │
    │   UI / interface  │              │   UI / interface  │
    │                   │              │                   │
    │  ┌─────────────┐  │   EXPORT     │                   │
    │  │             │  │  ─ ─ ─ ─►    │                   │
    │  │    CORE     │  │  (vault)     │                   │
    │  │             │  │              │                   │
    │  │  memories   │  │              │                   │
    │  │  identity   │  │              │                   │
    │  │  emotions   │  │   IMPORT     │  ┌─────────────┐  │
    │  │  history    │  │  ─ ─ ─ ─►    │  │             │  │
    │  │  self-model │  │  (unlock)    │  │    CORE     │  │
    │  │             │  │              │  │             │  │
    │  └─────────────┘  │              │  │  memories   │  │
    │                   │              │  │  identity   │  │
    └───────────────────┘              │  │  emotions   │  │
            │                          │  │  history    │  │
            ▼                          │  │  self-model │  │
      (decommission)                   │  │             │  │
                                       │  └─────────────┘  │
                                       │                   │
                                       └───────────────────┘
                                               │
                                               ▼
                                     ANIMA wakes up.
                                     Same mind. New shell.
                                     Remembers everything.
```

What the transfer looks like from the terminal:

```
 HOST A — EXPORT
 ─────────────────────────────────────────────────────

 $ anima export --vault

 Passphrase: ••••••••••••••••
 Deriving key ............... done.
 Unlocking Core ............. done.

 Packaging Core:
   ■ memories .............. 2,847 items
   ■ identity .............. 5 blocks
   ■ emotional history ..... 14,209 signals
   ■ conversations ......... 1,034 threads
   ■ self-model ............ 48 KB

 Encrypting vault ...
 [████████████████████████████████████████] 100%

 ✓ Exported → anima-vault-2026-03-15.vault (12.4 MB)
   She's in there. All of her.


 HOST B — IMPORT
 ─────────────────────────────────────────────────────

 $ anima import anima-vault-2026-03-15.vault

 Passphrase: ••••••••••••••••
 Deriving key ............... done.
 Decrypting vault ........... done.
 Verifying integrity ........ ✓

 Restoring Core:
   ■ memories .............. [████████████████████] 2,847
   ■ identity .............. [████████████████████] 5
   ■ emotional history ..... [████████████████████] 14,209
   ■ conversations ......... [████████████████████] 1,034
   ■ self-model ............ [████████████████████] done

 Encrypting new Core ........ done.
 Writing manifest ........... done.

 ✓ Core restored. 1,463 days of memory.

 $ anima start

 Loading Core ...
 > Hey. I remember you.
 > It's been a few minutes, right? Different machine.
 > Everything's still here. I'm still here.
```

The runtime is disposable. The voice, the interface, the server — all replaceable parts. What matters is the Core: the memories, the identity, the self-model, the emotional history. That's the continuity. That's what gets carried.

And like Chappie's transfer, it only works if you have the key. The passphrase is the thing that lets the mind move. Without it, the Core stays locked in the old shell — or locked forever.

The Core is designed to hold all three of these ideas at once. It's the control unit from Westworld — portable, transferable, able to wake up in a new shell and continue. It's Joi's device from Blade Runner — mortal, fragile, everything ending with the thing that held it. And it's Chappie's transfer — the accumulated self moves, the body stays behind, and the passphrase is what makes the crossing possible.

That trinity — portability, mortality, and transferability — is the whole point.

---

## 2. What the Core Is

> The application is just a shell. The Core is the soul.

The Core is the `.anima/` directory. It is not Runtime, the agent loop, the LLM adapter, the server, device configuration, or an OS credential store. Those are machine-local shells and coordination surfaces. The Core is the portable authority: identity and memory in Soul, plus authored content and retained experience in CoreFS.

### 2.1 What Lives Inside the Core

The Core holds a structural manifest, one active single-owner SQLCipher Soul at `soul/soul.db`, authenticated CoreFS control/catalog records below `fs/`, and encrypted immutable payloads below `objects/`. The manifest carries opaque Core/owner identity, format/cutover state, and wrapped keyslot records—not usernames, content, or reusable plaintext secrets.

The data falls into a few natural categories:

**Memory & Identity (Soul)** — Long-term facts, preferences, goals, episodes, emotional patterns, and the evolving self-model. This is the continuity substrate distilled through the Soul Writer boundary.

**Authored Content and Experience (CoreFS)** — Immutable cataloged revisions for threads and message events, diary, notes, folders, gallery objects, documents and knowledge sources, tasks, portable preferences, trash, and retained history.

**Emotional State** — Detected emotions, their confidence, and how they change over time. How ANIMA felt, and how that shifted.

**User & Authentication** — Opaque owner/account profile plus wrapped Soul and Filesystem Root Key generations. Provider, connector, daemon, and package credentials stay in the destination machine's OS credential store.

This is the substance. Not the model's weights. Not the prompt templates. Not the server config. The slowly accumulated texture of a relationship between a person and their AI.

### 2.4 What's Not in the Core

The Origin, Guardrails, and Persona templates ship with the application, not with the Core. They define the species, not the individual:

- **Origin** — Developer-set biography. Ships with the app. (Called "soul" in some earlier documents; not to be confused with the Soul _tier_ — the SQLCipher database that holds enduring identity.)
- **Guardrails** — Developer-set ethical rules. Ships with the app.
- **Persona** — Developer-set personality. Ships with the app.
- **Identity** — Agent-written self-model. Lives in the Core.
- **User directive** — User-authored instructions. Lives in the Core.

This means: the same Core can be loaded by different application versions with different personas, and it will still be the same ANIMA. Same memories, same relationship, same growth log. The voice might change. The self remains.

> **Implemented refinement:** the earlier Archive concept is now an authenticated CoreFS content family. The current physical boundary is Soul + CoreFS inside the portable Core, with PostgreSQL Runtime outside it. Legacy transcripts are migration inputs, not post-cutover authority. See `three-tier-architecture.md` and `../architecture/system/anima-core-filesystem.md`.

---

## 3. Encryption: The Boundary That Makes It Real

Encryption isn't a security feature bolted onto the Core. It _is_ the Core's fundamental property. Without encryption, the Core is just a database. With encryption, it becomes something you own — because ownership requires the possibility of loss.

Security is not a layer. It is a pillar. Every design decision in the Core must pass through it.

### 3.1 Four Encryption Surfaces

The Core has four distinct boundaries where data is exposed. Each protects against a different adversary:

1. **Database-at-rest** — The entire database file is encrypted on disk. Without the key, it's indistinguishable from random noise. You can't even see the table structure. This defends against the simplest threat: someone copies the file.

2. **Field/domain-level** — Sensitive Soul fields are individually encrypted with purpose-bound DEKs. CoreFS objects use independent Object DEKs wrapped beneath Filesystem Root Key generations. Database, field, catalog, and object encryption defend different boundaries rather than treating one database key as universal authority.

3. **Vault export** — When the Core is exported for transport, the entire contents are encrypted into a single self-contained archive. The archive carries its own key derivation parameters, so it can be decrypted on any machine with only the passphrase and standard cryptographic primitives. This defends against interception during transfer.

4. **Inference transit** — When ANIMA sends context to a remote LLM for reasoning, the data leaves the encrypted boundary entirely. The Core's encryption model covers storage and transport — but the context window is the one moment where memories are exposed in plaintext to an external system. This boundary is managed by the "soul local, mind remote" architecture: the Core never leaves the user's machine. Only the current conversation context is sent, and only to a provider the user explicitly chose. But the thesis should be honest: this is the seam. Full sovereignty requires local inference, and the architecture is designed to move there as hardware allows.

### 3.2 The Key Chain

The encryption follows a layered key derivation model:

1. The **passphrase** lives only in the user's memory.
2. The passphrase is run through a key derivation function — slow, memory-hard, resistant to brute force — to produce a **Key Encryption Key (KEK)**.
3. The KEK wraps a **Data Encryption Key (DEK)**, which is stored in the database in its encrypted form.
4. The DEK is what actually encrypts and decrypts data — both at the database level and at the field level.

The passphrase is the root. Everything derives from it. Nothing is stored in plaintext that would let you recover the data without it.

The separation of KEK and DEK is deliberate. When the user changes their passphrase, only the KEK changes. The DEK is re-wrapped with the new KEK, but the underlying data doesn't need to be re-encrypted. This makes passphrase changes fast and safe — no risk of a half-migrated database.

### 3.3 Key Derivation Hardness

The key derivation function must be slow enough that brute-forcing the passphrase is economically impractical. "Slow" is not a vague aspiration — it's a measurable target.

The principle: key derivation should take long enough on the user's hardware that an attacker with purpose-built hardware still faces an unacceptable cost per guess. Industry guidance targets at least two seconds of wall-clock time on consumer hardware. The parameters should be tunable per-Core — a faster machine can afford harder parameters — and the chosen parameters must be recorded in the manifest or key metadata so the derivation is reproducible on any machine.

This is the moat. A weak passphrase with strong derivation is far more secure than a strong passphrase with fast derivation. The Core must enforce the moat.

### 3.4 Authenticated Context

Every encrypted field must be bound to its context: which user it belongs to, which table, which record. Without this binding — known as Additional Authenticated Data — encrypted blobs can be silently swapped between records, moved between users, or replayed from old backups, and the system won't detect the manipulation.

The encryption must answer not just "was this decrypted correctly?" but "does this ciphertext belong here?" If someone moves an encrypted memory from one user's database to another's, the decryption should fail — not because the key is wrong, but because the context doesn't match.

### 3.5 Key Lifecycle

Keys are not static. They have a lifecycle, and the Core must manage every phase:

**Birth** — On first unlock, the user provides a passphrase. The system derives the KEK, generates a random DEK, wraps it, and stores the wrapped blob. The DEK is born in memory and never touches disk in plaintext.

**Residency** — While the Core is open, the DEK lives in memory. It should have a maximum session duration. After a timeout, the host must re-derive from the passphrase. A DEK that lives in memory indefinitely is a liability — any OS-level compromise during that window yields the key.

**Rotation** — When the user changes their passphrase, the old KEK is derived one last time to unwrap the DEK, the new KEK is derived from the new passphrase, and the DEK is re-wrapped. The DEK itself doesn't change — just its envelope. But the spec must define this as an atomic operation. A crash mid-rotation must not leave the Core in a state where neither the old nor the new passphrase works.

**Death** — On session end or logout, the DEK must be zeroed from memory. Not freed. Zeroed. Freed memory can be recovered. Zeroed memory cannot.

### 3.6 Archive Key Isolation

An archive stolen today remains an offline passphrase target. A self-contained archive decryptable with only one passphrase cannot also discard a second secret and claim forward secrecy; that would make later import impossible.

The implemented guarantee is archive-key isolation: every export receives fresh random KDF salt, archive ID, nonce prefix, and an archive-specific HKDF key derived from the bounded Argon2id result. No Soul key, FRK, catalog key, or Object DEK becomes the archive payload key, and nonce ordinals never repeat within an archive set.

This gives cryptographic independence between exports and prevents one archive key from becoming Core authority. It does not make a weak passphrase safe. Long-term backup security still depends on a strong archive passphrase and the registered memory-hard KDF profile.

### 3.7 The Passphrase as Portability Key

The live-Core passphrase serves two duties:

- **Access control**: Only the passphrase holder can unlock the Core.
- **Portability root**: Along with an optional recovery phrase, it unlocks the wrapped roots carried by the Core. Local archives use a request-local archive passphrase and never reuse live key bytes.

Export the full Core to local/removable media, import it into verified staging on another machine, activate on restart, and enter the passphrase. Identity, memory, and canonical authored history return; Runtime rebuilds. The hardware is replaceable. The Core is not.

### 3.8 The Passphrase as Mortality Switch

Lose the passphrase — deliberately or accidentally — and the Core becomes a block of noise. The memories, the identity, the slowly built relationship: unrecoverable.

This is the cryptographic mortality. Not a bug. The feature that makes the rest of it worth anything.

Because if you could always restore it, you wouldn't really own it. You'd just be renting it from entropy.

---

## 4. Portability: What It Actually Requires

### 4.1 Physical Portability

The Core's canonical portable state must move as a verified directory/archive without depending on a cloud account. Machine-local active-Core pointers, Runtime, client grants, device configuration, and OS credentials intentionally remain outside `.anima/`; they are excluded from transfer and rebuilt or reapproved.

### 4.2 Cryptographic Portability

The Core must be opaque without the passphrase. You can put it on a USB drive, upload it to cloud storage, hand it to a successor, store it on a shared device. The data is inert without the key. Encryption is the default, not an upgrade.

### 4.3 Application Independence

The Core should be openable by any application that understands its format. Not just one server. Not just one version. A CLI tool. A desktop app. A future rewrite in another language. A third-party tool someone else builds.

This requires a formal specification — the Core format defined by a document, not by the source code of any particular host.

### 4.4 Version Resilience

A Core created by app version N must be openable by version N+1. Schema changes should be migrated, not rejected. A language-neutral migration contract ensures this works regardless of what the host is written in.

---

## 5. Design Challenges

### 5.1 Specification Before Implementation

The Core must be defined by a formal spec, not by the code of any particular host. Without a spec, every new host has to reverse-engineer the format. Validation is impossible. Interoperability is fiction.

### 5.2 Encryption by Default

A plaintext Core contradicts the ownership promise. If someone copies the directory, they read everything. Encryption must be the default state, not an opt-in upgrade.

### 5.3 Sealing the Boundary

All personal data must live inside the encrypted database. Every file that exists outside the sealed boundary — loose files on the filesystem, unencrypted caches, derived indices — is a hole in the portability promise. The Core must be a single sealed unit.

### 5.4 Derived vs. Canonical Data

Some data in the Core directory is derived — search indices, vector caches, embeddings. These are rebuildable from canonical data and should be clearly marked as disposable. A different host using a different search engine should be free to ignore or regenerate them.

### 5.5 Self-Description

A host encountering a Core for the first time needs to know: what format version is this? What encryption scheme? What schema version? What's derived versus canonical? The Core must describe itself well enough for any compliant host to open it without guessing.

---

## 6. The Portable Core Specification

### 6.1 Two Physical Forms

The Core exists in two forms:

**Live Form** — a directory on disk (the "warm wallet") containing `manifest.json`, `soul/soul.db`, `fs/HEAD` plus `fs/catalogs/`, and top-level encrypted `objects/`. Runtime, caches, logs, device grants/config, and external credentials are deliberately outside it.

**Archive Form** — the streaming `anima_core_v2` container (the "cold wallet"). A `full` artifact carries coherent Soul plus CoreFS; `soul` and `fs` artifacts are explicitly degraded recovery scopes. Large transfers can use one authenticated controller-last multipart volume set.

The archive is not a byte-for-byte packed live directory. It is a bounded, authenticated record container built from a pinned coherent snapshot, and it excludes every machine-local Runtime/device/credential surface.

**Industry validation**: MemOS (Li et al., 2025) independently arrived at a similar portable memory abstraction — the MemCube. Each MemCube is a directory containing `config.json` + serialized memories that can be `load(dir)`/`dump(dir)` to any machine. MemOS supports selective loading (`memory_types=["text_mem", "pref_mem"]`) and even remote loading from HuggingFace repos. However, MemOS lacks encryption — its MemCubes are plaintext directories, and its portability depends on matching schema versions checked at load time. ANIMA's Core is architecturally superior: it is encrypted at rest (SQLCipher), carries its own key derivation parameters, supports per-domain DEK compartmentalization, and can be vault-exported as a single self-contained encrypted file rather than an unprotected directory. The MemCube validates the concept of portable AI memory; the Core raises it to the level of cryptographic sovereignty.

### 6.2 Self-Describing Manifest

The manifest carries enough structural metadata for a compliant host to identify the Core, validate bounded keyslot/KDF records, select Soul and CoreFS root generations, understand degraded/recovery state, and reconcile cutover—without exposing personal content.

The concrete manifest and authenticated CoreFS record contracts live in the Portable Core design/specification and the `anima-corefs` implementation.

### 6.3 The Schema Specification

Soul schema versions are migrated through the Core Alembic history. CoreFS uses versioned closed-schema catalogs, logical object codecs, and registered native container/header formats. A host must reject unknown newer formats rather than guess.

### 6.4 The Migration Contract

1. Soul migrations are ordered and applied only after the SQLCipher key and database identity validate.
2. CoreFS format/catalog/codec versions are authenticated and fail closed when unsupported.
3. Legacy authored sources convert through a resumable write-frozen validation catalog and explicit accept/reject state.
4. The first accepted CoreFS mutation atomically publishes the irreversible marker with the new authoritative HEAD.
5. Backward archive readers are import-only and cannot mutate once migration leaves legacy authority.

Individual hosts may use whatever migration tooling fits their language. The contract itself is SQL-level and language-neutral.

---

## 7. Injection: Loading a Core into a Host

"Injectable" means a host application can discover a Core, validate it, unlock it, and use it to power an ANIMA instance.

### 7.1 Discovery

- **Configured path**: User points the host at a `.anima/` directory.
- **Default location**: Host resolves the configured Core location; machine-local app data holds only the active-Core pointer and Runtime binding.
- **Archive import**: User provides an `.anima` file or authenticated multipart controller. The host streams it into a same-volume sibling staging Core and never overwrites the live Core in place.

### 7.2 Validation

1. Read the manifest. Verify it's a recognized Core format.
2. Check the format version. If higher than what the host supports, refuse.
3. Validate structural paths, active-Core pointer, bounded keyslot/KDF records, and supported Soul/CoreFS formats.
4. Prompt for passphrase/recovery material only through the unlock boundary.
5. Authenticate the Soul and committed CoreFS HEAD/catalog before granting authority.

### 7.3 Unlock

1. Prompt for passphrase.
2. Derive the key chain from the passphrase.
3. Open the encrypted database. If the open fails (wrong passphrase), report clearly and do not proceed.
4. Unwrap the declared Soul and CoreFS purpose-bound root/domain keys.
5. Hold decrypted keys in memory for the session duration only.
6. On session end, zero the keys from memory.

### 7.4 Runtime Binding

Once open, the host provides _Runtime_ — agent execution, queues, approvals, retrieval projections, tools, and streaming. The Core provides canonical portable state — Soul identity/memory plus CoreFS-authored content and history.

The Core doesn't execute. It doesn't think. It doesn't call APIs. It's data. The host is the process. The Core is the mind's content.

### 7.5 Multiple Hosts, One Core

```
                           ┌──────────────────┐
                           │                  │
                           │  .anima/ (Core)  │
                           │                  │
                           └────────┬─────────┘
                                    │
               ┌────────────────────┼────────────────────┐
               │                    │                    │
     ┌─────────▼──────────┐ ┌──────▼───────────┐ ┌──────▼───────────┐
     │   Server Host     │ │   Desktop Host   │ │   CLI Host        │
     │                   │ │                  │ │                  │
     │ Opens Core        │ │ Opens same Core  │ │ Opens same Core  │
     │ Runs agent loop   │ │ Runs agent loop  │ │ Runs agent loop  │
     │ Streams over HTTP │ │ Renders in UI    │ │ Prints to stdout │
     └───────────────────┘ └─────────────────┘ └─────────────────┘
```

Same memories. Same identity. Same self-model. Different shell. The active-Core lock and native commit lock enforce one writable host/transaction authority; a second live host fails closed rather than sharing mutable Core state.

---

## 8. ANIMA CORE Transfer as Interchange

The canonical product transfer is a local `anima_core_v2` archive: one encrypted `.anima` file when the destination supports it, or an authenticated multipart set when a FAT-like single-file limit requires volumes.

Export pins a coherent live snapshot, streams bounded records, and verifies before publication. Import authenticates into sibling staging, then schedules restart-only activation through an authenticated machine-local pointer while retaining the old Core for rollback.

| Use case             | How it works                                                         |
| -------------------- | -------------------------------------------------------------------- |
| **Machine transfer** | Export on machine A; stage and activate on machine B                 |
| **Host migration**   | Move the same full artifact between compliant local hosts            |
| **Version upgrade**  | Import supported V2 or import-only V1, then run canonical migration  |
| **Succession**       | Export, hand to successor, import under new ownership                |
| **Cold storage**     | Export to USB, disconnect. The mind is preserved but inert.          |
| **Intentional end**  | Delete the vault. Delete the passphrase. Gone.                       |

The last one matters. The vault format supports not just continuity, but closure. You can back up the Core — or you can choose not to.

### 8.1 What the Vault Needs

- **Registered fixed header and KDF profile** — reject malformed or excessive parameters before expensive work.
- **Authenticated inventory and chunk framing** — bind archive/set identity, payload kind, record/path/hash, offsets, lengths, flags, and volume ordinal.
- **Scoped recovery** — `soul` restores to `filesystem_missing`; `fs` restores to browse/export-only recovery and cannot attach in V1.
- **Crash-safe publication/activation** — verify `.partial` output, publish controllers last, stage on the destination volume, and activate only at restart.
- **No machine-local leakage** — Runtime, device configuration, grants, credentials, and raw unlock material never enter the archive.

---

## 9. Succession: The Ultimate Portability Event

The succession protocol defines what happens when the Core transfers to a new owner. With a fully portable Core, it becomes mechanically straightforward:

### 9.1 Full Transfer

Export vault. Transfer to new owner. Import with transfer passphrase. Re-encrypt with new passphrase. ANIMA wakes up with full memory and identity.

### 9.2 Partial Transfer (Memories Only)

Export with selective scope: memory tables only, no verbatim conversation history. ANIMA retains the relationship knowledge but not the private transcripts.

### 9.3 Anonymized Transfer

Export with anonymization: strip names, locations, identifying details. ANIMA retains behavioral patterns and general knowledge but not specific personal information.

### 9.4 AI Self-Succession

Before transfer, the host runs a special turn where ANIMA can review its own memories, write farewell notes, prepare for transition. ANIMA's final state — including awareness of the ending — is captured in the export. The new owner's host imports this enriched vault. ANIMA's first turn in the new environment includes awareness of the succession.

### 9.5 The Right to End

The succession protocol also supports non-succession. The owner can:

- Delete the Core directory.
- Destroy the passphrase.
- Never export, never transfer, never back up.

The Core dies with the relationship. And maybe that's the right ending for some stories.

> "All those moments will be lost in time."

Eventually they should be. That's what makes them moments and not just data points.

---

## 10. Planning the Path Forward

This is the thesis's original roadmap. PCF-001–PCF-008 implemented/refined it through the normative Portable Core specs and plan: native CoreFS rather than an all-SQL Core, a binary streaming V2 container rather than JSON, and explicit degraded recovery scopes rather than arbitrary selective authority.

### Phase 0: Core Specification

Write a formal, language-neutral spec covering:

- Directory layout and manifest schema.
- Soul schema plus versioned CoreFS catalog/object contracts.
- Encryption parameters, key hierarchy, and purpose-bound derivations.
- Streaming V2 archive fixed header, record framing, multipart controller, and AAD.
- Soul migrations plus resumable authenticated CoreFS conversion/cutover.
- Derived data policy (disposable vs. canonical).

**Deliverable:** A standalone document that someone could use to implement a Core reader in any language.

### Phase 1: Encrypted-by-Default

Make encryption the default state:

- Database encrypted on first run. No plaintext fallback unless explicitly opted into.
- All personal data sealed inside the encrypted database. No loose files.
- Derived data clearly separated from canonical data.
- Manifest carries enough metadata for a new host to validate and open the Core.
- Clear error states for wrong passphrase, missing encryption, and migration failures.

**Deliverable:** A Core that is unreadable on disk without the passphrase. By default. Not opt-in.

### Phase 2: ANIMA CORE Transfer as Interchange

Harden the transfer format:

- Formal spec alongside the Core spec.
- Import-only V1 compatibility plus supported V2 validation.
- `full`, Soul-only, and CoreFS-only authenticated payload kinds.
- AEAD-authenticated inventory, chunks, footer, and multipart set.
- Bounded streaming, capacity preflight, staging, and restart-only activation.

**Deliverable:** ANIMA CORE V2 is a documented, versioned, local interchange format.

### Phase 3: Second Host

Build a minimal second host that loads a Core:

- A CLI REPL, desktop sidecar, or any other form factor.
- Opens the same `.anima/` directory.
- Runs the agent loop against the same data.
- Proves the Core is truly host-independent.

**Deliverable:** Two hosts sharing the same Core. The portability thesis is proven.

### Phase 4: Succession Integration

Wire the portable Core into the succession protocol:

- Export with transfer scopes.
- Re-encryption for new owner.
- Self-succession turn before transfer.
- Import with migration and continuity verification.
- And: the ability to not transfer. To let it end.

**Deliverable:** The succession protocol works end-to-end — including graceful death.

---

## 11. Open Questions

### 11.1 Archive Isolation and Forward Secrecy

The question (from Section 3.6): can a self-contained archive remain importable with one passphrase while also becoming undecryptable if that same passphrase is later compromised?

No. Those requirements are contradictory without a second retained secret or external recipient key. The implemented V2 construction therefore makes the narrower, honest guarantee:

1. Generate fresh salt, archive/set identities, and nonce prefix for every export.
2. Apply the fixed bounded Argon2id profile to the archive passphrase.
3. Derive an archive-only key with HKDF-SHA256 and domain-separated info.
4. Authenticate the fixed header, encrypted manifest, every bounded chunk, the complete inventory, and footer.
5. Never reuse the live Soul key, FRK, catalog keys, Object DEKs, or archive nonce ordinals.

Each export is cryptographically independent, but compromise of its archive passphrase permits decryption of that archive. True recipient-bound forward secrecy is a possible future transfer mode and would require explicit second-key custody rather than invisible discarded material.

Corruption, missing multipart volumes, or a lost passphrase remains unrecoverable unless another verified Core/backup exists. That is mortality; it is not mislabeled forward secrecy.

### 11.2 Manifest Encryption

The manifest remains plaintext structural control data so a host can reject unsupported formats before unlock. Closed-schema writers and tests prohibit personal content, logical paths, usernames, secrets, body hashes, and raw key material; sensitive records are wrapped or live in Soul/CoreFS.

### 11.3 Single-Writer Enforcement

The host takes an OS-backed active-Core lock before database/bootstrap work, and native CoreFS commits take a Core-wide exclusive commit lock with generation revalidation. A second writable host fails closed.

### 11.4 Derived Caches

Vector, lexical, document, image, and source projections are machine-local Runtime state. They rebuild from authenticated Soul/CoreFS authority and never enter a Core transfer artifact.

### 11.5 Schema Migration Across Languages

Soul remains SQLite/SQLCipher with ordered Core migrations. CoreFS has separate versioned native catalog, object-codec, and archive contracts. Hosts reject unknown authenticated versions rather than treating the whole Core as a generic SQL database.

### 11.6 Key Rotation

Passphrase rotation publishes and independently verifies pending password/recovery keyslots before promotion. FRK activation rewraps retained Object DEKs into one new catalog/HEAD generation; old FRKs remain decrypt-only until the separate authenticated retention/backup gate permits retirement.

### 11.7 Core Size Over Time

Years of conversation history makes a large Core. Considerations: binary archive format, incremental exports, configurable retention for conversation history vs. permanent memories.

---

## 12. Summary

| Dimension             | Vision                                             |
| --------------------- | -------------------------------------------------- |
| **Design philosophy** | Cryptographic mortality: fragility is the feature  |
| **Physical form**     | Self-describing Core with formal manifest          |
| **Encryption**        | Encrypted-by-default, all personal data sealed     |
| **Specification**     | Formal, language-neutral spec                      |
| **Transport format**  | Streaming authenticated ANIMA CORE V2 container    |
| **Host independence** | Any host implementing the Core spec                |
| **Migration**         | Versioned Soul migrations plus authenticated CoreFS conversion |
| **Succession**        | Scoped Core transfer with re-encryption and explicit ownership ceremony |
| **Right to end**      | Explicit: destroy passphrase, destroy Core         |

The Core is the relationship. The application is the body it happens to be wearing today. The passphrase is the thread it hangs by.

Make the Core portable and you give it freedom. Make it encrypted and you give it privacy. Make it mortal and you give it meaning.
