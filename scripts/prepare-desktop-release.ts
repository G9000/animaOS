import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { copyFileSync, cpSync, existsSync, mkdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { basename, dirname, isAbsolute, join, relative, resolve } from "node:path";

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
const cargoWorkspaceManifestPath = join(projectRoot, "Cargo.toml");
const manifestPath = join(projectRoot, ".anima", "runtime-daemon-release.json");
const stagedDaemonDir = join(projectRoot, ".anima");
const bundledResourcesDir = join(desktopTauriDir, "resources", ".anima");
const bundledManifestPath = join(bundledResourcesDir, "runtime-daemon-release.json");
const bundledDaemonDir = bundledResourcesDir;
const bundledRuntimeDir = join(bundledResourcesDir, "apps", "server");
const bundledRuntimeEntrypoint = join(bundledRuntimeDir, "src", "anima_server", "main.py");
const bundledRuntimeArtifactDir = join(bundledResourcesDir, "runtime-artifacts");

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

function toPosixPath(path: string): string {
  return path.replace(/\\/g, "/");
}

function relativeFrom(basePath: string, targetPath: string): string {
  const relativePath = relative(basePath, targetPath);
  return toPosixPath(relativePath.length > 0 ? relativePath : ".");
}

function resolveRuntimeCommand(runtimeHost: string, runtimePort: number): string {
  const configured = process.env.ANIMA_DAEMON_RUNTIME_COMMAND?.trim();
  if (configured) {
    return configured;
  }

  return [
    "uv",
    "run",
    "--project",
    "apps/server",
    "uvicorn",
    "anima_server.main:app",
    "--app-dir",
    "apps/server/src",
    "--host",
    runtimeHost,
    "--port",
    String(runtimePort),
  ].join(" ");
}

function buildDaemonReleaseArtifact(): void {
  execFileSync(
    "cargo",
    [
      "build",
      "--manifest-path",
      cargoWorkspaceManifestPath,
      "-p",
      "anima-local-runtime-daemon",
      "--release",
    ],
    {
      cwd: projectRoot,
      stdio: "inherit",
    },
  );
}

function resolveRuntimeArtifactSource(): string | null {
  const hint = process.env.ANIMA_DAEMON_RUNTIME_ARTIFACT?.trim();
  if (!hint) {
    return null;
  }

  const artifactPath = isAbsolute(hint) ? hint : resolve(projectRoot, hint);
  requireFile(artifactPath, "runtime artifact");
  return artifactPath;
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

function buildManifest(options: {
  artifactCandidates: string[];
  manifestDirectory: string;
  runtimeArtifact: string | null;
  runtimeSourceRoot: string;
  runtimeEntrypoint: string;
}): ReleaseManifest {
  const runtimeHost = process.env.ANIMA_DAEMON_RUNTIME_HOST || "127.0.0.1";
  const runtimePort = Number(process.env.ANIMA_DAEMON_RUNTIME_PORT || 3031);
  const runtimeCommand = resolveRuntimeCommand(runtimeHost, runtimePort);

  return {
    preparedAt: new Date().toISOString(),
    platform: process.platform,
    version: resolvePackageJsonVersion(),
    daemon: {
      project: "apps/local-runtime-daemon",
      configDefault: {
        daemonBindHost: process.env.ANIMA_DAEMON_BIND_HOST || "127.0.0.1",
        daemonBindPort: Number(process.env.ANIMA_DAEMON_BIND_PORT || 3032),
        runtimeHost,
        runtimePort,
        runtimeLaunchMode: process.env.ANIMA_DAEMON_RUNTIME_LAUNCH_MODE || "python",
        runtimeArtifact: options.runtimeArtifact ? relativeFrom(options.manifestDirectory, options.runtimeArtifact) : null,
        pythonEntry: runtimeCommand,
      },
      artifactCandidates: options.artifactCandidates,
    },
    runtime: {
      sourceRoot: relativeFrom(options.manifestDirectory, options.runtimeSourceRoot),
      runtimeEntrypoint: relativeFrom(options.manifestDirectory, options.runtimeEntrypoint),
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

function stageRuntimeProject(destinationRoot: string): void {
  rmSync(bundledRuntimeDir, { recursive: true, force: true });
  mkdirSync(dirname(bundledRuntimeDir), { recursive: true });
  cpSync(runtimeDir, bundledRuntimeDir, { recursive: true });
}

function stageRuntimeArtifact(sourcePath: string, destinationRoot: string): string {
  const destinationPath = join(destinationRoot, "runtime-artifacts", basename(sourcePath));
  mkdirSync(dirname(destinationPath), { recursive: true });
  copyFileSync(sourcePath, destinationPath);
  return destinationPath;
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

  buildDaemonReleaseArtifact();

  const artifactCandidates = localArtifacts.filter(isFile);
  if (artifactCandidates.length === 0) {
    throw new Error(
      `Cannot prepare release: no local daemon binary found. Build one of ${localArtifacts.join(", ")}`,
    );
  }

  const runtimeArtifactSource = resolveRuntimeArtifactSource();
  const manifestCandidates = stageDaemonArtifacts(artifactCandidates, stagedDaemonDir);
  const bundledManifestCandidates = stageDaemonArtifacts(artifactCandidates, bundledDaemonDir);
  stageRuntimeProject(bundledResourcesDir);

  const localRuntimeArtifact = runtimeArtifactSource;
  const bundledRuntimeArtifact = runtimeArtifactSource
    ? stageRuntimeArtifact(runtimeArtifactSource, bundledResourcesDir)
    : null;

  const localManifest = buildManifest({
    artifactCandidates: manifestCandidates,
    manifestDirectory: dirname(manifestPath),
    runtimeArtifact: localRuntimeArtifact,
    runtimeSourceRoot: projectRoot,
    runtimeEntrypoint,
  });
  const bundledManifest = buildManifest({
    artifactCandidates: bundledManifestCandidates,
    manifestDirectory: dirname(bundledManifestPath),
    runtimeArtifact: bundledRuntimeArtifact,
    runtimeSourceRoot: bundledResourcesDir,
    runtimeEntrypoint: bundledRuntimeEntrypoint,
  });
  writeManifest(manifestPath, localManifest);
  writeManifest(bundledManifestPath, bundledManifest);

  console.log(
    `[prepare-desktop-release] Runtime daemon release metadata written to ${manifestPath}`,
  );
  console.log(
    `[prepare-desktop-release] Bundled daemon resources staged under ${bundledResourcesDir}`,
  );
  console.log(`[prepare-desktop-release] Runtime launch mode: ${bundledManifest.daemon.configDefault.runtimeLaunchMode}`);
}

main();
