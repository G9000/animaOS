# Portable Core Filesystem Tickets

Execution backlog for ANIMA CORE, animaOS's portable encrypted Soul-plus-CoreFS subsystem: move portable user-owned content into encrypted Core objects, restrict SQLCipher to ANIMA's internal Soul, keep PostgreSQL machine-local and rebuildable, provide a customizable stable-role folder tree with explicit ownership/client access, share production-grade Rust file tooling with Animus through explicit backends, and support local streaming full/Soul/CoreFS transfer and recovery.

- Parent tracker: [PCF-000](PCF-000-portable-core-filesystem.md)
- PRD: [Portable Core Filesystem v1](../../docs/prds/portable-core-filesystem-v1.md)
- Plan: [Implementation plan](../../docs/superpowers/plans/2026-07-12-portable-core-filesystem.md)

Execute child tickets in dependency order. `PCF-009` and `PCF-010` are separate later-release cleanup/maintenance work and must not be combined with first cutover.
