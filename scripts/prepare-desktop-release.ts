import { fileURLToPath } from "node:url";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

interface ReleaseManifest {
  preparedAt: string;
  platform: NodeJS.Platform;
  version: string;
  daemon: {
    project: string;
    configDefault: {
      daemonBindHost: string;
      daemonBindPort: number;
      runtimeHost: string;
      runtimePort: number;
      runtimeLaunchMode: string;
      runtimeArtifact: string | null;
      pythonEntry: string;
    };
    artifactCandidates: string[];
  };
  runtime: {
    sourceRoot: string;
    runtimeEntrypoint: string;
    pythonLauncherHint: string;
  };
}

const scriptPath = resolve(fileURLToPath(import.meta.url));
const projectRoot = resolve(scriptPath, "..", "..");
const desktopPackage = "apps/desktop";
const runtimeDir = join(projectRoot, "apps/server");
const runtimeEntrypoint = join(runtimeDir, "src", "anima_server", "main.py");
const daemonDir = join(projectRoot, "apps/local-runtime-daemon");
const manifestPath = join(projectRoot, ".anima", "runtime-daemon-release.json");

const localArtifacts = [
  join(projectRoot, "target", "release", "anima-local-runtime-daemon"),
  join(projectRoot, "target", "release", "anima-local-runtime-daemon.exe"),
  join(projectRoot, "target", "debug", "anima-local-runtime-daemon"),
  join(projectRoot, "target", "debug", "anima-local-runtime-daemon.exe"),
];

function requireFile(path: string, label: string) {
  if (!existsSync(path)) {
    throw new Error(`Missing ${label} at ${path}`);
  }
}

function writeManifest(manifest: ReleaseManifest): void {
  mkdirSync(join(projectRoot, ".anima"), { recursive: true });
  writeFileSync(manifestPath, JSON.stringify(manifest, null, 2), "utf8");
}

function resolvePackageJsonVersion(fallback = "0.1.0"): string {
  try {
    const raw = readFileSync(resolve(projectRoot, desktopPackage, "package.json"), "utf8");
    const parsed = JSON.parse(raw) as { version?: string };
    return parsed.version ?? fallback;
  } catch {
    return fallback;
 }
}

function buildManifest(): ReleaseManifest {
  const runtimeArtifactHint = process.env.ANIMA_DAEMON_RUNTIME_ARTIFACT || "";
  const pythonEntry = process.env.ANIMA_DAEMON_RUNTIME_COMMAND || "python";

  return {
    preparedAt: new Date().toISOString(),
    platform: process.platform,
    version: resolvePackageJsonVersion(),
    daemon: {
      project: daemonDir,
      configDefault: {
        daemonBindHost: process.env.ANIMA_DAEMON_BIND_HOST || "127.0.0.1",
        daemonBindPort: Number(process.env.ANIMA_DAEMON_BIND_PORT || 3032),
        runtimeHost: process.env.ANIMA_DAEMON_RUNTIME_HOST || "127.0.0.1",
        runtimePort: Number(process.env.ANIMA_DAEMON_RUNTIME_PORT || 3031),
        runtimeLaunchMode: process.env.ANIMA_DAEMON_RUNTIME_LAUNCH_MODE || "python",
        runtimeArtifact: runtimeArtifactHint || null,
        pythonEntry,
      },
      artifactCandidates: localArtifacts,
    },
    runtime: {
      sourceRoot: runtimeDir,
      runtimeEntrypoint,
      pythonLauncherHint: process.env.ANIMA_DAEMON_PYTHON || "python",
    },
  };
}

function main(): void {
  if (!existsSync(daemonDir)) {
    throw new Error(`Local daemon crate is missing at ${daemonDir}`);
  }

  if (!existsSync(runtimeDir)) {
    throw new Error(`Runtime project is missing at ${runtimeDir}`);
  }

  try {
    requireFile(runtimeEntrypoint, "Python runtime entrypoint");
  } catch (error) {
    const message = error instanceof Error ? error.message : "Runtime entrypoint check failed";
    throw new Error(`Cannot prepare release: ${message}`);
  }

  const manifest = buildManifest();
  writeManifest(manifest);

  console.log(
    `[prepare-desktop-release] Runtime daemon release metadata written to ${manifestPath}`,
  );
  console.log(`[prepare-desktop-release] Runtime launch mode: ${manifest.daemon.configDefault.runtimeLaunchMode}`);
  console.log("[prepare-desktop-release] Note: build the release artifacts and install scripts before packaging.");
}

main();
