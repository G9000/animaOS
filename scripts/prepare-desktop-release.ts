import { fileURLToPath } from "node:url";
import { copyFileSync, existsSync, mkdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";

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
const desktopTauriDir = join(projectRoot, desktopPackage, "src-tauri");
const runtimeDir = join(projectRoot, "apps/server");
const runtimeEntrypoint = join(runtimeDir, "src", "anima_server", "main.py");
const daemonDir = join(projectRoot, "apps/local-runtime-daemon");
const manifestPath = join(projectRoot, ".anima", "runtime-daemon-release.json");
const stagedDaemonDir = join(projectRoot, ".anima");
const bundledResourcesDir = join(desktopTauriDir, "resources", ".anima");
const bundledManifestPath = join(bundledResourcesDir, "runtime-daemon-release.json");
const bundledDaemonDir = bundledResourcesDir;

const localArtifacts = [
  join(projectRoot, "target", "release", "anima-local-runtime-daemon"),
  join(projectRoot, "target", "release", "anima-local-runtime-daemon.exe"),
  join(projectRoot, "target", "debug", "anima-local-runtime-daemon"),
  join(projectRoot, "target", "debug", "anima-local-runtime-daemon.exe"),
];

function requireFile(path: string, label: string) {
  if (!isFile(path)) {
    throw new Error(`Missing ${label} at ${path}`);
  }
}

function isFile(path: string): boolean {
  try {
    return statSync(path).isFile();
  } catch {
    return false;
  }
}

function writeManifest(path: string, manifest: ReleaseManifest): void {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, JSON.stringify(manifest, null, 2), "utf8");
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

function buildManifest(artifactCandidates: string[]): ReleaseManifest {
  const runtimeArtifactHint = process.env.ANIMA_DAEMON_RUNTIME_ARTIFACT || "";
  const pythonEntry = process.env.ANIMA_DAEMON_RUNTIME_COMMAND || "python";

  return {
    preparedAt: new Date().toISOString(),
    platform: process.platform,
    version: resolvePackageJsonVersion(),
    daemon: {
      project: "apps/local-runtime-daemon",
      configDefault: {
        daemonBindHost: process.env.ANIMA_DAEMON_BIND_HOST || "127.0.0.1",
        daemonBindPort: Number(process.env.ANIMA_DAEMON_BIND_PORT || 3032),
        runtimeHost: process.env.ANIMA_DAEMON_RUNTIME_HOST || "127.0.0.1",
        runtimePort: Number(process.env.ANIMA_DAEMON_RUNTIME_PORT || 3031),
        runtimeLaunchMode: process.env.ANIMA_DAEMON_RUNTIME_LAUNCH_MODE || "python",
        runtimeArtifact: runtimeArtifactHint || null,
        pythonEntry,
      },
      artifactCandidates,
    },
    runtime: {
      sourceRoot: "apps/server",
      runtimeEntrypoint: "apps/server/src/anima_server/main.py",
      pythonLauncherHint: process.env.ANIMA_DAEMON_PYTHON || "python",
    },
  };
}

function artifactVariant(path: string): string {
  if (path.includes(`${process.platform === "win32" ? "\\" : "/"}release${process.platform === "win32" ? "\\" : "/"}`)) {
    return "release";
  }
  if (path.includes(`${process.platform === "win32" ? "\\" : "/"}debug${process.platform === "win32" ? "\\" : "/"}`)) {
    return "debug";
  }
  return "bin";
}

function stageDaemonArtifacts(artifactCandidates: string[], destinationRoot: string): string[] {
  rmSync(join(destinationRoot, "runtime-daemon"), { recursive: true, force: true });

  return artifactCandidates.map((artifactPath) => {
    const relativePath = join("runtime-daemon", artifactVariant(artifactPath), basename(artifactPath));
    const destinationPath = join(destinationRoot, relativePath);
    mkdirSync(dirname(destinationPath), { recursive: true });
    copyFileSync(artifactPath, destinationPath);
    return relativePath.replace(/\\/g, "/");
  });
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

  const artifactCandidates = localArtifacts.filter(isFile);
  if (artifactCandidates.length === 0) {
    throw new Error(
      `Cannot prepare release: no local daemon binary found. Build one of ${localArtifacts.join(", ")}`,
    );
  }

  const manifestCandidates = stageDaemonArtifacts(artifactCandidates, stagedDaemonDir);
  stageDaemonArtifacts(artifactCandidates, bundledDaemonDir);

  const manifest = buildManifest(manifestCandidates);
  writeManifest(manifestPath, manifest);
  writeManifest(bundledManifestPath, manifest);

  console.log(
    `[prepare-desktop-release] Runtime daemon release metadata written to ${manifestPath}`,
  );
  console.log(
    `[prepare-desktop-release] Bundled daemon resources staged under ${bundledResourcesDir}`,
  );
  console.log(`[prepare-desktop-release] Runtime launch mode: ${manifest.daemon.configDefault.runtimeLaunchMode}`);
  console.log("[prepare-desktop-release] Note: build the release artifacts and install scripts before packaging.");
}

main();
