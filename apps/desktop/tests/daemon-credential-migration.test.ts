import { describe, expect, test } from "bun:test";
import { join } from "node:path";
import { readFileSync } from "node:fs";

const desktopRoot = join(import.meta.dir, "..");
const renderer = readFileSync(join(desktopRoot, "src/lib/daemon.ts"), "utf8");
const tauri = readFileSync(join(desktopRoot, "src-tauri/src/lib.rs"), "utf8");
const daemon = readFileSync(
  join(desktopRoot, "../local-runtime-daemon/src/main.rs"),
  "utf8",
);

describe("daemon credential migration", () => {
  test("keeps the live token in memory and scrubs only exact legacy copies", () => {
    expect(renderer).toContain("let daemonControlToken: string | null = null");
    expect(renderer).toContain("Legacy daemon credential changed during secure migration");
    expect(renderer).toContain("localStorage.removeItem(key)");
    expect(renderer).not.toContain("localStorage.setItem(DAEMON_CONTROL_TOKEN_KEY");
  });

  test("shares the native OS credential reference and never persists a token file", () => {
    expect(tauri).toContain('credential_reference("daemon", "control-token")');
    expect(tauri).toContain("import_legacy_value");
    expect(daemon).toContain('credential_reference("daemon", "control-token")');
    expect(daemon).toContain("load_or_create_migrating_legacy");
    expect(daemon).not.toContain("persist_control_token");
  });
});
