---
title: Directory Structure
description: Top-level folder layout and purpose of each directory in AnimaOS
category: architecture
last_edited: 2026-07-15
---

# Directory Structure

[Back to Architecture](../README.md)

AnimaOS is a polyglot monorepo. Project manifests describe each application or package, while Bun, Nx, uv, and Cargo coordinate complementary parts of the repository. A directory does not need to belong to every workspace to be a supported project.

## Applications

| Path | Language and role | Primary manifests |
| --- | --- | --- |
| `apps/anima-mod/` | Bun + TypeScript + Elysia external-presence service. Built-in channel and integration modules live in `mods/`; locally installed modules live in `user-mods/`. | `package.json` |
| `apps/animus/` | Rust coding terminal and TUI that connects to the ANIMA server and operates within an approved local workspace. | `Cargo.toml` |
| `apps/desktop/` | React + TypeScript + Vite desktop interface with a Tauri Rust host under `src-tauri/`. | `package.json`, `project.json`, `src-tauri/Cargo.toml` |
| `apps/local-runtime-daemon/` | Rust + Axum background supervisor and local control API for starting, monitoring, restarting, locking, and inspecting the Python runtime. | `Cargo.toml` |
| `apps/server/` | Python + FastAPI cognitive core, including API routes, agent services, SQLAlchemy persistence, tests, and separate Core/runtime Alembic histories. | `pyproject.toml`, `project.json` |
| `apps/site/` | Astro + React + TypeScript public website. | `package.json` |

## Shared Packages

| Path | Runtime and responsibility | Primary manifests |
| --- | --- | --- |
| `packages/anima-auth-contracts/` | TypeScript authentication route constants and request/response contracts shared by clients. | `package.json` |
| `packages/anima-core/` | Rust memory infrastructure for vector, lexical, graph, capsule, and portable-memory operations, with optional PyO3/maturin Python bindings. | `Cargo.toml`, `pyproject.toml` |
| `packages/anima-corefs/` | Rust cryptographic and filesystem primitives for ANIMA CORE. | `Cargo.toml` |
| `packages/anima-file-tools/` | Bounded, storage-agnostic Rust file operations shared by Animus HostFS and ANIMA CoreFS. | `Cargo.toml` |
| `packages/anima-runtime-daemon-contracts/` | TypeScript routes, state models, control payloads, and response contracts for the local runtime daemon API. | `package.json` |
| `packages/api-client/` | TypeScript API client, shared API types, and type-generation tooling used by the desktop application. | `package.json` |
| `packages/ascii-motion/` | React/TypeScript ASCII animation components plus demuxing, rendering, glyph, and edge-detection utilities. | `package.json` |
| `packages/standard-templates/` | React + Tailwind design system containing tokens, primitives, composed components, icons, chat surfaces, and canvas helpers. | `package.json` |

## Repository and Workflow Roots

| Path | Purpose |
| --- | --- |
| `.codex-skill-staging/` | Repository-owned Codex skills that are developed and validated with the repository workflow. |
| `.github/` | GitHub automation and repository metadata. |
| `docs/` | Thesis, architecture, audit history, operational guidance, PRDs, approved specs, and dated implementation plans. Audit history belongs under `docs/audit/`. |
| `scripts/` | Root development, build, database, code-generation, release, and repository-validation helpers. |
| `tests/` | Repository-level Bun tests for root orchestration and organization tooling. Application and package tests remain beside their owning projects. |
| `third_party/` | Third-party license and notice material required for attribution and packaging. |
| `tickets/` | Canonical parent/child initiative backlog, lifecycle state, acceptance evidence, and completion history. |
| `scratchboard/` | Legacy planning history for older workstreams only. New work follows the PRD/spec/plan/ticket workflow documented in `docs/ops/prd-ticket-workflow.md`. |

Root manifests and lockfiles stay at the repository root so the relevant tools can discover their workspaces. Product code stays under `apps/` and reusable implementation stays under `packages/`.

## Workspace Boundaries

- **Bun and package manifests:** root `package.json` declares `apps/*` and `packages/*` workspace globs. JavaScript and TypeScript projects participate through their own `package.json` files, and root scripts provide shared install, development, build, test, and code-generation entry points.
- **Nx orchestration:** `nx.json` defines shared inputs and target defaults. Nx discovers the package-manifest JavaScript/TypeScript projects together with the explicit `server` and `desktop` `project.json` files. The Nx portion of the root `build` script and the root `lint` script select only `server` and `desktop`; after that Nx build succeeds, the root `build` script separately runs `cargo check -p animus`. Cargo crates and uv-only packages are not implicitly forced into Nx.
- **uv Python workspace:** root `pyproject.toml` declares `apps/server` and `packages/anima-core`. The server consumes the maturin-built `anima-core` Python package through that workspace relationship.
- **Cargo workspace:** root `Cargo.toml` owns `apps/animus`, `apps/local-runtime-daemon`, `apps/desktop/src-tauri`, `packages/anima-core`, `packages/anima-corefs`, and `packages/anima-file-tools`.
- **Direct project manifests:** a direct child under `apps/` or `packages/` must expose at least one recognized manifest (`package.json`, `project.json`, `pyproject.toml`, or `Cargo.toml`). That manifest remains authoritative for project-specific dependencies and commands.

## Generated and Machine-Local Paths

The following are runtime state, dependencies, caches, outputs, secrets, or isolated working copies rather than repository source and should not be committed:

- `.anima/` local identity and runtime data, except for the explicit packaged-resource placeholder under `apps/desktop/src-tauri/resources/.anima/`;
- `node_modules/`, `.nx/`, `dist/`, `target/`, and tool-specific `build/` output directories;
- `.venv/`, Python bytecode, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, and other tool caches;
- `.env` and `.env.*` machine-local configuration, except committed `.env.example` templates;
- root `/debug.log`, `.tmp-dev-server.log`, temporary test/evaluation outputs, local databases, and generated indexes;
- `.worktrees/`, `.claude/worktrees/`, and other machine-local worktree directories.

Before adding a new generator or runtime service, give its output a stable location and an appropriate ignore rule rather than mixing it with source.
