import { describe, expect, test } from "bun:test";
import { mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { extname, join, relative, resolve } from "node:path";
import ts from "typescript";
import { resolveDesktopPackageEnvironment } from "../../../scripts/desktop-package-environment";
import { prepareLinuxInstallIdentity } from "../../../scripts/prepare-linux-install-identity";

const desktopRoot = join(import.meta.dir, "..");
const repoRoot = resolve(desktopRoot, "..", "..");
const draftMutationOwner = "apps/desktop/src/features/diary/lib/draftMigration.ts";

function source(path: string): string {
  return readFileSync(join(repoRoot, path), "utf8");
}

function sourceFiles(root: string): string[] {
  const absoluteRoot = join(repoRoot, root);
  const result: string[] = [];
  const visit = (directory: string) => {
    for (const name of readdirSync(directory)) {
      const path = join(directory, name);
      const stat = statSync(path);
      if (stat.isDirectory()) visit(path);
      else if ([".ts", ".tsx", ".js", ".jsx", ".rs"].includes(extname(path))) {
        result.push(relative(repoRoot, path));
      }
    }
  };
  visit(absoluteRoot);
  return result;
}

describe("desktop plaintext-draft cleanup release contract", () => {
  test("publishes only replacement-only installer families", () => {
    const config = JSON.parse(source("apps/desktop/src-tauri/tauri.conf.json")) as {
      bundle: { targets: string | string[] };
    };
    const packageJson = JSON.parse(source("apps/desktop/package.json")) as {
      scripts: Record<string, string>;
    };
    const releasePreparation = source("scripts/prepare-desktop-release.ts");

    expect(config.bundle.targets).not.toBe("all");
    expect(config.bundle.targets).not.toContain("nsis");
    expect(config.bundle.targets).not.toContain("appimage");
    expect(config.bundle.targets).not.toContain("dmg");
    expect(config).toMatchObject({ mainBinaryName: "anima" });
    expect(config.bundle.windows.wix.upgradeCode).toMatch(/^[0-9a-f-]{36}$/);
    expect(config.bundle.windows.wix.fragmentPaths).toContain("install/windows/close-anima.wxs");
    expect(config.bundle.windows.wix.componentRefs).toContain("AnimaUpgradeCloseGuard");
    const windowsUpgrade = source("apps/desktop/src-tauri/install/windows/close-anima.wxs");
    expect(windowsUpgrade).toContain('Target="anima.exe"');
    expect(windowsUpgrade).toContain('Target="desktop.exe"');
    expect(windowsUpgrade).toContain('TerminateProcess="1"');
    expect(packageJson.scripts.package).toBeUndefined();
    expect(packageJson.scripts["package:windows"]).toContain("--bundles msi");
    expect(packageJson.scripts["package:debian"]).toContain("--bundles deb");
    expect(packageJson.scripts["package:rpm"]).toContain("--bundles rpm");
    expect(packageJson.scripts["package:macos"]).toContain("build-macos-pkg");
    expect(releasePreparation).toContain("ANIMA_DRAFT_CLEANUP_RELEASE");
    expect(releasePreparation).toContain("installIdentityStagingDir");
  });

  test("embeds the resolved current version when no external override exists", () => {
    const { environment, version } = resolveDesktopPackageEnvironment(
      "macos",
      "7.8.9",
      {},
    );
    expect(version).toBe("7.8.9");
    expect(environment.ANIMA_DESKTOP_VERSION_OVERRIDE).toBe("7.8.9");
    expect(environment.ANIMA_DRAFT_CLEANUP_RELEASE).toBe("1");
    expect(environment.ANIMA_INSTALLER_FAMILY).toBe("macos");
  });

  test("has a native four-platform final-artifact upgrade gate", () => {
    const workflow = source(".github/workflows/desktop-draft-cleanup-authority.yml");
    const verifier = source("scripts/verify-desktop-release-contract.ts");

    expect(workflow).toContain("COST_DISABLED");
    expect(workflow).toMatch(/^on: \[\]$/m);
    expect(workflow).not.toMatch(/^  workflow_dispatch:/m);
    for (const platform of ["windows", "macos", "debian", "rpm"]) {
      expect(workflow).toContain(platform);
      expect(verifier).toContain(platform);
    }
    for (const format of [".msi", ".pkg", ".deb", ".rpm"]) {
      expect(verifier).toContain(format);
    }
    expect(workflow).toContain("verify-desktop-release-contract.ts");
    expect(workflow).toContain("draft_cleanup_process");
    expect(workflow).toContain("artifact-digests");
    expect(workflow).toContain("fixture_version");
    expect(workflow).toContain("ANIMA_DRAFT_CLEANUP_LEGACY_FIXTURE");
    expect(workflow).toContain("same protected source");
    expect(workflow).toContain("Prove first-release bootstrap boundary");
    expect(workflow).toContain("matching-refs/tags/desktop-v");
    expect(workflow).toContain("prior installer-managed desktop host exists");
    expect(workflow).toContain("ANIMA_LINUX_INSTALL_IDENTITY_PREVIOUS_PUBLIC_KEY_PEM");
    expect(workflow).toContain('test "$PUBLIC_KEY_HEX" != "$PREVIOUS_PUBLIC_KEY_HEX"');
    expect(workflow).not.toContain("previous_release_ref");
    expect(workflow).not.toContain("gh release download");
    expect(verifier).toContain("verifyPackagedCensus");
    expect(verifier).toContain("predecessor executable has no signed cross-version host identity marker");
    expect(verifier).toContain("installer shutdown of the older");
    expect(verifier).toContain("runInstalledIdentityProbe");
    expect(verifier).toContain("runPackagedRuntimeAuthorityProbe");
    const native = source("apps/desktop/src-tauri/src/draft_cleanup.rs");
    expect(native).toContain("pidfd_open");
    expect(native).toContain("proc_listpids");
    expect(native).toContain("WTSEnumerateProcessesW");
    expect(native).toContain("process_memory_has_trusted_host_identity");
    expect(native).toContain("SetAccessRuleProtection($true,$false)");
    expect(native).toContain("canonical_msi_guid");
  });

  test("proves Linux old-to-current signing-key rollover", () => {
    const directory = mkdtempSync(join(tmpdir(), "anima-install-key-rollover-"));
    try {
      const keys = ["old", "current"].map((name) => {
        const privateKey = join(directory, `${name}.private.pem`);
        const publicKey = join(directory, `${name}.public.pem`);
        expect(Bun.spawnSync(["openssl", "genpkey", "-algorithm", "ED25519", "-out", privateKey]).exitCode).toBe(0);
        expect(Bun.spawnSync(["openssl", "pkey", "-in", privateKey, "-pubout", "-out", publicKey]).exitCode).toBe(0);
        const publicDer = Bun.spawnSync(["openssl", "pkey", "-in", privateKey, "-pubout", "-outform", "DER"]).stdout;
        return { privateKey, publicKey, publicKeyHex: Buffer.from(publicDer.slice(-32)).toString("hex") };
      });
      expect(keys[0].publicKeyHex).not.toBe(keys[1].publicKeyHex);
      const executable = join(directory, "anima");
      const desktop = join(directory, "ANIMA.desktop");
      const manifest = join(directory, "identity.json");
      const signature = join(directory, "identity.json.sig");
      writeFileSync(executable, "old host");
      writeFileSync(desktop, "[Desktop Entry]\nExec=/usr/bin/anima\n");
      const verify = (publicKey: string) => Bun.spawnSync([
        "openssl", "pkeyutl", "-verify", "-rawin", "-pubin", "-inkey", publicKey,
        "-in", manifest, "-sigfile", signature,
      ]).exitCode;
      prepareLinuxInstallIdentity({
        packageFamily: "debian", packageVersion: "1.0.0", executable, desktopEntry: desktop,
        privateKey: keys[0].privateKey, publicKeyHex: keys[0].publicKeyHex, manifest, signature,
      });
      expect(verify(keys[0].publicKey)).toBe(0);
      expect(verify(keys[1].publicKey)).not.toBe(0);
      writeFileSync(executable, "current host");
      prepareLinuxInstallIdentity({
        packageFamily: "debian", packageVersion: "2.0.0", executable, desktopEntry: desktop,
        privateKey: keys[1].privateKey, publicKeyHex: keys[1].publicKeyHex, manifest, signature,
      });
      expect(verify(keys[1].publicKey)).toBe(0);
      expect(verify(keys[0].publicKey)).not.toBe(0);
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });

  test("signs a canonical Linux installed identity and detects tampering", () => {
    const directory = mkdtempSync(join(tmpdir(), "anima-install-identity-"));
    try {
      const privateKey = join(directory, "private.pem");
      const publicKey = join(directory, "public.pem");
      const executable = join(directory, "anima");
      const desktop = join(directory, "ANIMA.desktop");
      const manifest = join(directory, "install-identity-v1.json");
      const signature = join(directory, "install-identity-v1.json.sig");
      const generated = Bun.spawnSync([
        "openssl", "genpkey", "-algorithm", "ED25519", "-out", privateKey,
      ]);
      expect(generated.exitCode).toBe(0);
      expect(Bun.spawnSync([
        "openssl", "pkey", "-in", privateKey, "-pubout", "-out", publicKey,
      ]).exitCode).toBe(0);
      const publicDer = Bun.spawnSync([
        "openssl", "pkey", "-in", privateKey, "-pubout", "-outform", "DER",
      ]).stdout;
      const publicKeyHex = Buffer.from(publicDer.slice(-32)).toString("hex");
      writeFileSync(executable, "fixture executable");
      writeFileSync(desktop, "[Desktop Entry]\nExec=/usr/bin/anima\n");

      prepareLinuxInstallIdentity({
        packageFamily: "debian",
        packageVersion: "1.2.3",
        executable,
        desktopEntry: desktop,
        privateKey,
        publicKeyHex,
        manifest,
        signature,
      });
      const parsed = JSON.parse(readFileSync(manifest, "utf8")) as Record<string, unknown>;
      expect(parsed).toMatchObject({
        schemaVersion: 1,
        bundleId: "com.leoca.anima",
        packageFamily: "debian",
        packageName: "anima",
        packageVersion: "1.2.3",
        executablePath: "/usr/bin/anima",
      });
      const verify = () => Bun.spawnSync([
        "openssl", "pkeyutl", "-verify", "-rawin", "-pubin", "-inkey", publicKey,
        "-in", manifest, "-sigfile", signature,
      ]).exitCode;
      expect(verify()).toBe(0);
      writeFileSync(manifest, `${readFileSync(manifest, "utf8")} `);
      expect(verify()).not.toBe(0);
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });

  test("makes the migration module the sole draft-key mutation owner", () => {
    const inventory: string[] = [];
    for (const path of sourceFiles("apps/desktop/src")) {
      if (!/\.[jt]sx?$/.test(path)) continue;
      const text = source(path);
      const syntax = ts.createSourceFile(path, text, ts.ScriptTarget.Latest, true);
      const visit = (node: ts.Node): void => {
        if (
          ts.isCallExpression(node) &&
          ts.isPropertyAccessExpression(node.expression) &&
          ["setItem", "removeItem"].includes(node.expression.name.text)
        ) {
          const object = node.expression.expression.getText(syntax);
          if (["localStorage", "sessionStorage", "storage"].includes(object)) {
            inventory.push(`${path}|${object}.${node.expression.name.text}|${node.arguments[0]?.getText(syntax) ?? ""}`);
          }
        }
        ts.forEachChild(node, visit);
      };
      visit(syntax);
    }
    // This AST snapshot makes every renderer storage writer an explicit,
    // reviewed capability. Variables, wrappers, computed keys, and multiline
    // calls cannot bypass it by hiding the literal draft prefix.
    expect(inventory.sort()).toEqual(`
apps/desktop/src/components/database/hooks/useLocalStorage.ts|localStorage.setItem|key
apps/desktop/src/components/database/hooks/useQueryDraft.ts|localStorage.removeItem|STORAGE_KEY
apps/desktop/src/components/database/hooks/useQueryDraft.ts|localStorage.setItem|STORAGE_KEY
apps/desktop/src/components/layout/LayoutSidebar.tsx|localStorage.setItem|SIDEBAR_STORAGE_KEY
apps/desktop/src/context/AsciiSettingsContext.tsx|localStorage.removeItem|"anima_ascii_settings"
apps/desktop/src/context/AsciiSettingsContext.tsx|localStorage.setItem|"anima_ascii_settings"
apps/desktop/src/context/AuthContext.tsx|localStorage.removeItem|STORAGE_KEY
apps/desktop/src/features/diary/lib/draftMigration.ts|storage.removeItem|draftMigrationStateKey(storageKey)
apps/desktop/src/features/diary/lib/draftMigration.ts|storage.removeItem|storageKey
apps/desktop/src/features/diary/lib/draftMigration.ts|storage.setItem|stateKey
apps/desktop/src/hooks/useBgm.ts|localStorage.setItem|"anima_bgm_muted"
apps/desktop/src/hooks/useBgmPlayer.ts|localStorage.setItem|STATE_KEY
apps/desktop/src/hooks/useClockFormat.ts|localStorage.setItem|STORAGE_KEY
apps/desktop/src/lib/api.ts|localStorage.removeItem|UNLOCK_TOKEN_KEY
apps/desktop/src/lib/api.ts|sessionStorage.removeItem|UNLOCK_TOKEN_KEY
apps/desktop/src/lib/api.ts|sessionStorage.setItem|UNLOCK_TOKEN_KEY
apps/desktop/src/lib/background.ts|localStorage.setItem|BACKGROUND_CONFIG_KEY
apps/desktop/src/lib/daemon.ts|localStorage.removeItem|DAEMON_CONTROL_TOKEN_KEY
apps/desktop/src/lib/daemon.ts|localStorage.setItem|DAEMON_CONTROL_TOKEN_KEY
apps/desktop/src/lib/greetingCache.ts|sessionStorage.removeItem|GREETING_CACHE_KEY
apps/desktop/src/lib/greetingCache.ts|sessionStorage.removeItem|GREETING_ONESHOT_KEY
apps/desktop/src/lib/greetingCache.ts|sessionStorage.removeItem|GREETING_ONESHOT_KEY
apps/desktop/src/lib/greetingCache.ts|sessionStorage.setItem|GREETING_CACHE_KEY
apps/desktop/src/lib/greetingCache.ts|sessionStorage.setItem|GREETING_ONESHOT_KEY
apps/desktop/src/lib/mod-client.ts|localStorage.setItem|MOD_URL_KEY
apps/desktop/src/lib/preferences.ts|localStorage.setItem|DB_VIEWER_KEY
apps/desktop/src/lib/preferences.ts|localStorage.setItem|SHOW_TRACE_KEY
apps/desktop/src/lib/preferences.ts|localStorage.setItem|TRANSLATE_LANG_KEY
apps/desktop/src/lib/theme.ts|localStorage.setItem|THEME_KEY
apps/desktop/src/lib/today-context.ts|storage.removeItem|STORAGE_KEY
apps/desktop/src/lib/today-context.ts|storage.removeItem|STORAGE_KEY
apps/desktop/src/lib/today-context.ts|storage.removeItem|STORAGE_KEY
apps/desktop/src/lib/today-context.ts|storage.removeItem|STORAGE_KEY
apps/desktop/src/lib/today-context.ts|storage.setItem|STORAGE_KEY
apps/desktop/src/pages/agent-customization/AgentCustomization.tsx|localStorage.setItem|RAIL_STORAGE_KEY
apps/desktop/src/pages/auth/Login.tsx|localStorage.setItem|CACHED_USER_KEY
apps/desktop/src/pages/dashboard/Dashboard.tsx|localStorage.removeItem|"anima_dashboard_node_positions"
apps/desktop/src/pages/dashboard/Dashboard.tsx|localStorage.removeItem|CLOSED_NODES_KEY
apps/desktop/src/pages/dashboard/Dashboard.tsx|localStorage.setItem|CLOSED_NODES_KEY
apps/desktop/src/pages/dashboard/useNodePositions.ts|localStorage.setItem|STORAGE_KEY
apps/desktop/src/pages/init/useSetupMachine.ts|sessionStorage.removeItem|PENDING_PHRASE_KEY
apps/desktop/src/pages/init/useSetupMachine.ts|sessionStorage.setItem|PENDING_PHRASE_KEY
apps/desktop/src/pages/settings/AiSettings.tsx|localStorage.removeItem|CLOUD_STORAGE_KEY
apps/desktop/src/pages/settings/AiSettings.tsx|localStorage.removeItem|keyHintKey(p)
apps/desktop/src/pages/settings/AiSettings.tsx|localStorage.setItem|CLOUD_STORAGE_KEY
apps/desktop/src/pages/settings/AiSettings.tsx|localStorage.setItem|CLOUD_STORAGE_KEY
apps/desktop/src/pages/settings/AiSettings.tsx|localStorage.setItem|keyHintKey(p)
`.trim().split("\n"));
    const owner = source(draftMutationOwner);
    expect(owner).toContain("draftMigrationLockName");
    expect(owner).toContain("storage.removeItem(storageKey)");
    const genericWriter = source("apps/desktop/src/components/database/hooks/useLocalStorage.ts");
    expect(genericWriter).toContain("type DatabaseStorageKey =");
    expect(genericWriter).not.toContain("key: string, initial");
  });
});
