import { chmodSync, copyFileSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, extname, join, resolve } from "node:path";

type Platform = "windows" | "macos" | "debian" | "rpm";

interface Arguments {
  platform: Platform;
  previousArtifact: string;
  artifact: string;
  output: string;
}

const expectedExtensions: Record<Platform, string> = {
  windows: ".msi",
  macos: ".pkg",
  debian: ".deb",
  rpm: ".rpm",
};

function parseArguments(argv: string[]): Arguments {
  const values = new Map<string, string>();
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith("--") || value === undefined) throw new Error("arguments must be --name value pairs");
    values.set(key.slice(2), value);
  }
  const platform = values.get("platform") as Platform | undefined;
  if (!platform || !(platform in expectedExtensions)) throw new Error("--platform must be windows, macos, debian, or rpm");
  const previousArtifact = values.get("previous-artifact");
  const artifact = values.get("artifact");
  const output = values.get("output");
  if (!previousArtifact || !artifact || !output) throw new Error("--previous-artifact, --artifact, and --output are required");
  return { platform, previousArtifact: resolve(previousArtifact), artifact: resolve(artifact), output: resolve(output) };
}

function run(command: string, args: string[]): string {
  const result = Bun.spawnSync([command, ...args], { stdout: "pipe", stderr: "pipe" });
  if (result.exitCode !== 0) {
    throw new Error(`${command} failed: ${result.stderr.toString().trim()}`);
  }
  return result.stdout.toString();
}

function requireFinalArtifact(path: string, platform: Platform): void {
  if (!existsSync(path) || !statSync(path).isFile()) throw new Error(`artifact is not a regular file: ${path}`);
  if (extname(path).toLowerCase() !== expectedExtensions[platform]) {
    throw new Error(`${platform} cleanup authority requires ${expectedExtensions[platform]}, got ${basename(path)}`);
  }
}

function sha256(path: string): string {
  return new Bun.CryptoHasher("sha256").update(readFileSync(path)).digest("hex");
}

function sleep(milliseconds: number): void {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, milliseconds);
}

function waitFor(description: string, predicate: () => boolean): void {
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    if (predicate()) return;
    sleep(100);
  }
  throw new Error(`timed out waiting for ${description}`);
}

function processIsAlive(pid: number): boolean {
  if (process.platform === "win32") {
    const result = Bun.spawnSync(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", `if(Get-Process -Id ${pid} -ErrorAction SilentlyContinue){exit 0}else{exit 1}`]);
    return result.exitCode === 0;
  }
  const result = Bun.spawnSync(["kill", "-0", String(pid)], { stdout: "ignore", stderr: "ignore" });
  return result.exitCode === 0;
}

function startBackground(executable: string, args: string[]): number {
  if (process.platform === "win32") {
    const argumentList = args.map((value) => `'${value.replaceAll("'", "''")}'`).join(",");
    return Number(run("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", `$p=Start-Process -FilePath '${executable.replaceAll("'", "''")}' -ArgumentList @(${argumentList}) -PassThru; $p.Id`]).trim());
  }
  const quote = (value: string): string => `'${value.replaceAll("'", `'"'"'`)}'`;
  return Number(run("/bin/sh", ["-c", `${[executable, ...args].map(quote).join(" ")} >/dev/null 2>&1 & echo $!`]).trim());
}

function terminateProcess(pid: number): void {
  if (process.platform === "win32") {
    Bun.spawnSync(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", `Stop-Process -Id ${pid} -Force -ErrorAction SilentlyContinue`]);
  } else {
    Bun.spawnSync(["kill", "-TERM", String(pid)], { stdout: "ignore", stderr: "ignore" });
  }
}

function runInstalledIdentityProbe(executable: string): void {
  const result = Bun.spawnSync([executable], {
    env: { ...process.env, ANIMA_DRAFT_CLEANUP_IDENTITY_PROBE: "1" },
    stdout: "pipe",
    stderr: "pipe",
  });
  if (result.exitCode !== 0) {
    throw new Error(`packaged installed-identity probe failed: ${result.stderr.toString().trim()}`);
  }
}

function runPackagedRuntimeAuthorityProbe(executable: string): void {
  const result = Bun.spawnSync([executable], {
    env: { ...process.env, ANIMA_DRAFT_CLEANUP_RUNTIME_PROBE: "1" },
    stdout: "pipe",
    stderr: "pipe",
  });
  if (result.exitCode !== 0) {
    throw new Error(`packaged post-WebView issue/consume probe failed: ${result.stderr.toString().trim()}`);
  }
}

function preserveLegacyHost(executable: string): string {
  if (!readFileSync(executable).includes(Buffer.from("anima-host-identity-v1:", "utf8"))) {
    throw new Error("predecessor executable has no signed cross-version host identity marker");
  }
  const directory = mkdtempSync(join(tmpdir(), "anima-preserved-legacy-"));
  const path = join(directory, process.platform === "win32" ? "renamed-legacy-host.exe" : "renamed-legacy-host");
  copyFileSync(executable, path);
  if (process.platform !== "win32") chmodSync(path, 0o755);
  return path;
}

function verifyPackagedCensus(executable: string, legacyExecutable: string): void {
  const directory = mkdtempSync(join(tmpdir(), "anima-census-"));
  const candidate = join(directory, process.platform === "win32" ? "renamed-uncooperative-host.exe" : "renamed-uncooperative-host");
  copyFileSync(legacyExecutable, candidate);
  if (process.platform !== "win32") chmodSync(candidate, 0o755);
  const contenderPid = startBackground(candidate, ["--relocated-legacy-writer"]);
  if (!Number.isSafeInteger(contenderPid)) throw new Error("could not launch the native census contender");
  try {
    waitFor("native census contender launch", () => processIsAlive(contenderPid));
    const blocked = Bun.spawnSync([executable], {
      env: { ...process.env, ANIMA_DRAFT_CLEANUP_IDENTITY_PROBE: "1" },
      stdout: "pipe",
      stderr: "pipe",
    });
    if (blocked.exitCode === 0) throw new Error("packaged native census admitted a competing anima executable");
  } finally {
    terminateProcess(contenderPid);
    waitFor("native census contender exit", () => !processIsAlive(contenderPid));
    rmSync(directory, { recursive: true, force: true });
  }
  runInstalledIdentityProbe(executable);
  runPackagedRuntimeAuthorityProbe(executable);
}

function windowsRegistration(): { productCode: string; installLocation: string; target: string; version: string } {
  const script = [
    "$r=@(Get-ItemProperty HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\* | Where-Object {$_.DisplayName -eq 'ANIMA'})",
    "if($r.Count -ne 1){exit 2}",
    "$w=New-Object -ComObject WScript.Shell",
    "$roots=@([Environment]::GetFolderPath('CommonStartMenu'),[Environment]::GetFolderPath('StartMenu'))|Where-Object{$_}",
    "$links=@($roots|ForEach-Object{Get-ChildItem -LiteralPath $_ -Filter '*.lnk' -Recurse -ErrorAction SilentlyContinue}|Where-Object{$_.BaseName -eq 'ANIMA'}|ForEach-Object{$w.CreateShortcut($_.FullName)})",
    "if($links.Count -ne 1){exit 3}",
    "[pscustomobject]@{productCode=$r[0].PSChildName;installLocation=(Split-Path -Parent $links[0].TargetPath);target=$links[0].TargetPath;version=$r[0].DisplayVersion}|ConvertTo-Json -Compress",
  ].join(";");
  return JSON.parse(run("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", script])) as {
    productCode: string;
    installLocation: string;
    target: string;
    version: string;
  };
}

function installAndInspectWindows(previous: string, current: string): string[] {
  if (process.platform !== "win32") throw new Error("windows verification must run on Windows");
  const signed = run("powershell.exe", ["-NoProfile", "-Command", `(Get-AuthenticodeSignature -FilePath '${current.replaceAll("'", "''")}').Status`]);
  if (signed.trim() !== "Valid") throw new Error("final MSI Authenticode signature is invalid");
  run("msiexec.exe", ["/i", previous, "/qn", "/norestart"]);
  const old = windowsRegistration();
  const oldHash = sha256(old.target);
  const preservedOld = preserveLegacyHost(old.target);
  const oldPid = startBackground(old.target, []);
  if (!Number.isSafeInteger(oldPid)) throw new Error("could not launch the installed older ANIMA host");
  waitFor("older Windows host launch", () => processIsAlive(oldPid));
  run("msiexec.exe", ["/i", current, "/qn", "/norestart"]);
  waitFor("installer shutdown of the older Windows host", () => !processIsAlive(oldPid));
  const installed = windowsRegistration();
  const hosts = JSON.parse(run("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", `@(Get-ChildItem -LiteralPath '${installed.installLocation.replaceAll("'", "''")}' -Recurse -File -Filter '*.exe' | Where-Object {$_.Name -in @('anima.exe','desktop.exe')} | ForEach-Object {$_.FullName}) | ConvertTo-Json -Compress`])) as string | string[] | null;
  const hostPaths = hosts === null ? [] : Array.isArray(hosts) ? hosts : [hosts];
  if (hostPaths.length !== 1 || resolve(hostPaths[0]).toLowerCase() !== resolve(installed.target).toLowerCase()) {
    throw new Error("MSI owns an ambiguous or non-canonical ANIMA executable set");
  }
  if (sha256(installed.target) === oldHash) throw new Error("old and current Windows host identities are indistinguishable");
  verifyPackagedCensus(installed.target, preservedOld);
  rmSync(resolve(preservedOld, ".."), { recursive: true, force: true });
  return [JSON.stringify(installed), `launchTargets=1`, `oldPid=${oldPid}`, `hostExecutables=${hostPaths.length}`];
}

function installAndInspectMacos(previous: string, current: string): string[] {
  if (process.platform !== "darwin") throw new Error("macos verification must run on macOS");
  for (const artifact of [previous, current]) {
    run("pkgutil", ["--check-signature", artifact]);
    run("spctl", ["--assess", "--type", "install", "--verbose=4", artifact]);
    run("xcrun", ["stapler", "validate", artifact]);
  }
  run("sudo", ["installer", "-pkg", previous, "-target", "/"]);
  const oldTarget = "/Applications/ANIMA.app/Contents/MacOS/anima";
  const oldHash = sha256(oldTarget);
  const preservedOld = preserveLegacyHost(oldTarget);
  const oldPid = startBackground(oldTarget, ["--upgrade-fixture-with-argv"]);
  waitFor("older macOS host launch", () => processIsAlive(oldPid));
  run("sudo", ["installer", "-pkg", current, "-target", "/"]);
  waitFor("installer shutdown of the older macOS host", () => !processIsAlive(oldPid));
  const receipt = run("pkgutil", ["--pkg-info", "com.leoca.anima"]);
  const apps = readdirSync("/Applications").filter((name) => name === "ANIMA.app");
  if (apps.length !== 1) throw new Error("expected one installed ANIMA.app");
  run("codesign", ["--verify", "--deep", "--strict", "/Applications/ANIMA.app"]);
  const owned = run("pkgutil", ["--files", "com.leoca.anima"]).split(/\r?\n/).filter(Boolean);
  const hostPaths = owned.filter((path) => /ANIMA\.app\/Contents\/MacOS\/(anima|desktop)$/.test(path)).map((path) => `/${path}`);
  if (hostPaths.length !== 1 || hostPaths[0] !== oldTarget) throw new Error("PKG owns an ambiguous or non-canonical ANIMA executable set");
  if (sha256(oldTarget) === oldHash) throw new Error("old and current macOS host identities are indistinguishable");
  const registrationPaths = [...run("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister", ["-dump"])
    .matchAll(/^\s*path:\s*(.*ANIMA\.app)\s*$/gmi)]
    .map((match) => resolve(match[1]));
  if (new Set(registrationPaths).size !== 1 || registrationPaths[0] !== "/Applications/ANIMA.app") {
    throw new Error(`LaunchServices has ambiguous ANIMA app registrations: ${registrationPaths.join(", ")}`);
  }
  verifyPackagedCensus(oldTarget, preservedOld);
  rmSync(resolve(preservedOld, ".."), { recursive: true, force: true });
  return [receipt.trim(), `launchTargets=${apps.length}`, `oldPid=${oldPid}`, `hostExecutables=${hostPaths.length}`];
}

function installAndInspectDebian(previous: string, current: string): string[] {
  if (process.platform !== "linux") throw new Error("debian verification must run on Linux");
  elevated("dpkg", ["-i", previous]);
  const old = inspectLinuxInstall("debian", process.env.ANIMA_INSTALL_IDENTITY_PREVIOUS_PUBLIC_KEY_PEM);
  startBackground("xvfb-run", ["-a", old.executable]);
  const oldPid = waitForLinuxHost(old.executable);
  elevated("dpkg", ["-i", current]);
  waitFor("installer shutdown of the older Debian host", () => !processIsAlive(oldPid));
  const owner = run("dpkg-query", ["-S", "/usr/share/anima/install-identity-v1.json"]);
  const files = run("dpkg-query", ["-L", "anima"]);
  const details = inspectLinuxLaunchTargets(owner, files, old.hash, old.preserved);
  return [`oldPid=${oldPid}`, ...details];
}

function installAndInspectRpm(previous: string, current: string): string[] {
  if (process.platform !== "linux") throw new Error("rpm verification must run on Linux");
  elevated("rpm", ["-Uvh", "--replacepkgs", previous]);
  const old = inspectLinuxInstall("rpm", process.env.ANIMA_INSTALL_IDENTITY_PREVIOUS_PUBLIC_KEY_PEM);
  startBackground("xvfb-run", ["-a", old.executable]);
  const oldPid = waitForLinuxHost(old.executable);
  elevated("rpm", ["-Uvh", "--replacepkgs", current]);
  waitFor("installer shutdown of the older RPM host", () => !processIsAlive(oldPid));
  const owner = run("rpm", ["-qf", "/usr/share/anima/install-identity-v1.json"]);
  const files = run("rpm", ["-ql", "anima"]);
  const details = inspectLinuxLaunchTargets(owner, files, old.hash, old.preserved);
  return [`oldPid=${oldPid}`, ...details];
}

function waitForLinuxHost(executable: string): number {
  let pid = 0;
  waitFor("older Linux host launch", () => {
    const result = Bun.spawnSync(["pgrep", "-f", `^${executable.replaceAll(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`], { stdout: "pipe", stderr: "ignore" });
    if (result.exitCode !== 0) return false;
    const matches = result.stdout.toString().trim().split(/\s+/).filter(Boolean).map(Number);
    if (matches.length !== 1 || !Number.isSafeInteger(matches[0])) return false;
    pid = matches[0];
    return true;
  });
  return pid;
}

function inspectLinuxInstall(platform: "debian" | "rpm", publicKey: string | undefined): { executable: string; hash: string; preserved: string } {
  if (!publicKey) throw new Error("ANIMA_INSTALL_IDENTITY_PREVIOUS_PUBLIC_KEY_PEM is required for old-package verification");
  verifyLinuxManifestSignature(publicKey);
  const manifest = JSON.parse(readFileSync("/usr/share/anima/install-identity-v1.json", "utf8")) as {
    packageFamily: string;
    executablePath: string;
    executableSha256: string;
  };
  if (manifest.packageFamily !== platform || sha256(manifest.executablePath) !== manifest.executableSha256) {
    throw new Error("older Linux package does not match its signed installed identity");
  }
  return { executable: manifest.executablePath, hash: manifest.executableSha256, preserved: preserveLegacyHost(manifest.executablePath) };
}

function inspectLinuxLaunchTargets(owner: string, files: string, oldHash: string, preservedOld: string): string[] {
  const packageFiles = files.split(/\r?\n/).filter(Boolean);
  const launchTargets = packageFiles.filter((path) => path.toLowerCase().endsWith("/anima.desktop"));
  if (launchTargets.length !== 1) throw new Error(`expected one package-owned desktop entry, found ${launchTargets.length}`);
  const canonicalHost = "/usr/bin/anima";
  const knownHostTargets = [canonicalHost, "/usr/bin/desktop", "/usr/local/bin/anima", "/usr/local/bin/desktop"].filter(existsSync);
  if (knownHostTargets.length !== 1 || knownHostTargets[0] !== canonicalHost) {
    throw new Error(`installer residue leaves ambiguous host targets: ${knownHostTargets.join(", ")}`);
  }
  const packageHosts = packageFiles.filter((path) => {
    if (!existsSync(path) || !statSync(path).isFile() || (statSync(path).mode & 0o111) === 0) return false;
    return ["anima", "desktop"].includes(basename(path).toLowerCase());
  });
  if (packageHosts.length !== 1 || packageHosts[0] !== canonicalHost) {
    throw new Error(`package owns an ambiguous host executable set: ${packageHosts.join(", ")}`);
  }
  const globalEntries = findDesktopEntries().filter((path) => /^Exec=.*\/(anima|desktop)(?:\s|$)/m.test(readFileSync(path, "utf8")));
  if (globalEntries.length !== 1 || resolve(globalEntries[0]) !== resolve(launchTargets[0])) {
    throw new Error(`system has ambiguous ANIMA launch registrations: ${globalEntries.join(", ")}`);
  }
  for (const required of ["/usr/share/anima/install-identity-v1.json", "/usr/share/anima/install-identity-v1.json.sig"]) {
    if (!packageFiles.includes(required)) throw new Error(`package does not own ${required}`);
    const stat = statSync(required);
    if (!stat.isFile() || (stat.mode & 0o022) !== 0) throw new Error(`${required} is not a protected regular file`);
  }
  const manifest = JSON.parse(readFileSync("/usr/share/anima/install-identity-v1.json", "utf8")) as {
    executablePath: string;
    executableSha256: string;
    launchTargets: Array<{ path: string; sha256: string }>;
  };
  if (sha256(manifest.executablePath) !== manifest.executableSha256) throw new Error("installed executable hash does not match its signed manifest");
  if (manifest.launchTargets.length !== 1 || sha256(manifest.launchTargets[0].path) !== manifest.launchTargets[0].sha256) {
    throw new Error("installed launch target does not match its signed manifest");
  }
  const currentPublicKey = process.env.ANIMA_INSTALL_IDENTITY_PUBLIC_KEY_PEM;
  if (!currentPublicKey) throw new Error("ANIMA_INSTALL_IDENTITY_PUBLIC_KEY_PEM is required for current-package verification");
  verifyLinuxManifestSignature(currentPublicKey);
  if (manifest.executablePath !== canonicalHost || manifest.executableSha256 === oldHash) {
    throw new Error("current Linux executable is non-canonical or indistinguishable from the older host");
  }
  verifyPackagedCensus(canonicalHost, preservedOld);
  rmSync(resolve(preservedOld, ".."), { recursive: true, force: true });
  return [owner.trim(), `launchTargets=${launchTargets.length}`, `hostExecutables=${packageHosts.length}`, ...launchTargets];
}

function findDesktopEntries(): string[] {
  const roots = ["/usr/share/applications", "/usr/local/share/applications"];
  const userRoot = process.env.HOME ? join(process.env.HOME, ".local", "share", "applications") : undefined;
  if (userRoot) roots.push(userRoot);
  const results: string[] = [];
  const visit = (directory: string): void => {
    if (!existsSync(directory)) return;
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) visit(path);
      else if (entry.isFile() && entry.name.endsWith(".desktop")) results.push(path);
    }
  };
  for (const root of roots) visit(root);
  return results;
}

function elevated(command: string, args: string[]): string {
  return typeof process.getuid === "function" && process.getuid() === 0
    ? run(command, args)
    : run("sudo", [command, ...args]);
}

function verifyLinuxManifestSignature(publicKey: string): void {
  run("openssl", [
    "pkeyutl", "-verify", "-rawin", "-pubin", "-inkey", publicKey,
    "-in", "/usr/share/anima/install-identity-v1.json",
    "-sigfile", "/usr/share/anima/install-identity-v1.json.sig",
  ]);
}

const args = parseArguments(process.argv.slice(2));
requireFinalArtifact(args.previousArtifact, args.platform);
requireFinalArtifact(args.artifact, args.platform);
const details = args.platform === "windows"
  ? installAndInspectWindows(args.previousArtifact, args.artifact)
  : args.platform === "macos"
    ? installAndInspectMacos(args.previousArtifact, args.artifact)
    : args.platform === "debian"
      ? installAndInspectDebian(args.previousArtifact, args.artifact)
      : installAndInspectRpm(args.previousArtifact, args.artifact);
mkdirSync(args.output, { recursive: true });
writeFileSync(join(args.output, `${args.platform}.sha256`), `${sha256(args.artifact)}  ${basename(args.artifact)}\n`);
writeFileSync(join(args.output, `${args.platform}.txt`), `${details.join("\n")}\n`);
