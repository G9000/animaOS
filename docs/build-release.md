# ANIMA Build & Packaging Guide

This project is packaged as a Tauri desktop app with a bundled local API sidecar.

Customers should only receive install/build artifacts (for example `.app`, `.dmg`, `.msi`) and their runtime data folder.  
They should not need source code, `node_modules`, Bun, pnpm, or TypeScript files.

## Prerequisites

- Bun 1.3+
- Rust toolchain (`rustup`, `cargo`)
- Tauri platform prerequisites
  - macOS: Xcode Command Line Tools
  - Windows: MSVC build tools + WebView2 runtime
  - Linux: WebKitGTK and required system libs

## Install Dependencies

Run once from repo root:

```bash
bun install
```

## Build Commands

Run from repo root.

### 1) Recommended release output (`.app` on macOS)

```bash
bun --filter desktop package:app
```

This builds:

- Frontend production assets
- API sidecar binary (`apps/desktop/src-tauri/bin/anima-api`)
- Tauri release app bundle

Output (macOS):

- `apps/desktop/src-tauri/target/release/bundle/macos/ANIMA.app`

### 2) Full installer targets (DMG/MSI/etc)

```bash
bun --filter desktop package
```

This runs full `tauri build` targets configured for the platform.

## What Is Bundled

Inside the app resources:

- `bin/anima-api` (or `anima-api.exe`)
- `prompts/` (API prompt templates)
- `drizzle/` (DB migrations)

No repo path fallback is used at runtime.

## Runtime Data Folder (Customer Data)

At first launch, the app creates a per-user data directory and stores:

- `anima.db`
- `memory/`
- `soul/<userId>.soul.md`

Typical locations:

- macOS: `~/Library/Application Support/com.leoca.anima`
- Linux: `~/.local/share/com.leoca.anima`
- Windows: `%APPDATA%/com.leoca.anima`

## Release Handoff Checklist

1. Build with `bun --filter desktop package:app` (or `package`).
2. Smoke test auth/chat/memory/soul on a clean machine/user profile.
3. Confirm data is written to app data folder (not repo paths).
4. Deliver only the bundled artifact(s), not source/workspace files.

## Troubleshooting

- `Missing required environment variable: ANIMA_DATA_DIR`  
  This is expected if you run the API binary directly without the desktop launcher.

- `Failed to start server. Is port 3031 in use?`  
  Another process is already using local API port `3031`.

- DMG creation fails in non-GUI/headless environments  
  Use `bun --filter desktop package:app` to generate `.app`, then create/sign DMG in a GUI-capable release environment.
