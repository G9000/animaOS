import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";

const desktopRoot = join(import.meta.dir, "..");
const repoRoot = resolve(desktopRoot, "..", "..");

function readRepoSource(path: string): string {
  return readFileSync(join(repoRoot, path), "utf8");
}

describe("portable runtime release paths", () => {
  test("keeps generated runtime state outside the portable Core namespace", () => {
    const prepareRelease = readRepoSource("scripts/prepare-desktop-release.ts");
    const tauriConfig = readRepoSource("apps/desktop/src-tauri/tauri.conf.json");
    const tauriHost = readRepoSource("apps/desktop/src-tauri/src/lib.rs");

    expect(prepareRelease).not.toContain('join(projectRoot, ".anima"');
    expect(prepareRelease).not.toContain('join(desktopTauriDir, "resources", ".anima")');
    expect(prepareRelease).toContain('join(projectRoot, "target", "anima-release-staging")');
    expect(prepareRelease).toContain('join(desktopTauriDir, "resources", "runtime")');

    expect(tauriConfig).not.toContain('"resources/.anima/"');
    expect(tauriConfig).toContain('"resources/runtime/": "runtime/"');

    expect(tauriHost).not.toContain('".anima/runtime-daemon-release.json"');
    expect(tauriHost).toContain('"runtime/runtime-daemon-release.json"');
  });
});
