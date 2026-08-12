import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createPrivateKey, createPublicKey, sign } from "node:crypto";
import { join, resolve } from "node:path";
import {
  resolveDesktopPackageEnvironment,
  type DesktopPackagePlatform,
} from "./desktop-package-environment";

const repoRoot = resolve(import.meta.dir, "..");
const desktopRoot = join(repoRoot, "apps", "desktop");
const tauriCli = join(desktopRoot, "node_modules", "@tauri-apps", "cli", "tauri.js");
const [platform, mode, bundle] = process.argv.slice(2) as [DesktopPackagePlatform?, string?, string?];
if (!platform || !["windows", "macos", "debian", "rpm"].includes(platform)) {
  throw new Error("first argument must be windows, macos, debian, or rpm");
}
if (platform === "windows" && process.platform !== "win32") throw new Error("MSI must be built on Windows");
if (platform === "macos" && process.platform !== "darwin") throw new Error("PKG must be built on macOS");
if ((platform === "debian" || platform === "rpm") && process.platform !== "linux") {
  throw new Error("DEB/RPM must be built on Linux");
}

const tauriConfig = JSON.parse(
  readFileSync(join(desktopRoot, "src-tauri", "tauri.conf.json"), "utf8"),
) as { version: string };
const { environment, version } = resolveDesktopPackageEnvironment(
  platform,
  tauriConfig.version,
);
const hostIdentityPrivateKey = environment.ANIMA_INSTALL_IDENTITY_PRIVATE_KEY;
const hostIdentityPublicKeyHex = environment.ANIMA_INSTALL_IDENTITY_PUBLIC_KEY_HEX;
if (!hostIdentityPrivateKey || !hostIdentityPublicKeyHex) {
  throw new Error("cleanup-capable packages require the ANIMA host-identity signing key");
}
const privateKey = createPrivateKey(readFileSync(hostIdentityPrivateKey));
const derivedPublicKey = createPublicKey(privateKey).export({ type: "spki", format: "der" }).subarray(-32).toString("hex");
if (derivedPublicKey !== hostIdentityPublicKeyHex) {
  throw new Error("ANIMA host-identity private/public keys do not match");
}
environment.ANIMA_HOST_IDENTITY_SIGNATURE_HEX = sign(
  null,
  Buffer.from("anima-host-identity-v1\0com.leoca.anima", "utf8"),
  privateKey,
).toString("hex");

const releaseConfig = JSON.stringify({ version });

function execute(command: string[]): void {
  const result = Bun.spawnSync(command, {
    cwd: desktopRoot,
    env: environment,
    stdout: "inherit",
    stderr: "inherit",
  });
  if (result.exitCode !== 0) throw new Error(`desktop ${platform} package build failed`);
}

if (platform === "debian" || platform === "rpm") {
  const privateKey = environment.ANIMA_INSTALL_IDENTITY_PRIVATE_KEY;
  if (!privateKey) throw new Error("Linux cleanup-capable packages require ANIMA_INSTALL_IDENTITY_PRIVATE_KEY");
  execute(["bun", tauriCli, "build", "--no-bundle", "--config", releaseConfig]);
  const staging = join(repoRoot, "target", "anima-release-staging", "install-identity");
  execute([
    "bun", join(repoRoot, "scripts", "prepare-linux-install-identity.ts"),
    "--package-family", platform,
    "--package-version", version,
    "--executable", join(repoRoot, "target", "release", "anima"),
    "--desktop-entry", join(desktopRoot, "src-tauri", "install", "linux", "anima.desktop"),
    "--private-key", privateKey,
    "--manifest", join(staging, "install-identity-v1.json"),
    "--signature", join(staging, "install-identity-v1.json.sig"),
  ]);
  execute(["bun", tauriCli, "bundle", "--bundles", platform === "debian" ? "deb" : "rpm", "--config", releaseConfig]);
} else {
  const extraConfig: string[] = [];
  if (platform === "windows") {
    const certificateThumbprint = environment.ANIMA_WINDOWS_CERTIFICATE_THUMBPRINT;
    const timestampUrl = environment.ANIMA_WINDOWS_TIMESTAMP_URL;
    if (!certificateThumbprint || !timestampUrl) {
      throw new Error("MSI packaging requires ANIMA_WINDOWS_CERTIFICATE_THUMBPRINT and ANIMA_WINDOWS_TIMESTAMP_URL");
    }
    const configDirectory = join(repoRoot, "target", "anima-release-staging");
    const configPath = join(configDirectory, "tauri.windows.release.conf.json");
    mkdirSync(configDirectory, { recursive: true });
    writeFileSync(configPath, JSON.stringify({
      bundle: {
        windows: { certificateThumbprint, digestAlgorithm: "sha256", timestampUrl },
      },
    }));
    extraConfig.push("--config", configPath);
  }
  const command = mode === "--build-macos-pkg"
    ? ["bun", join(repoRoot, "scripts", "build-macos-pkg.ts"), "--config", releaseConfig]
    : ["bun", tauriCli, "build", mode ?? "", bundle ?? "", "--config", releaseConfig, ...extraConfig];
  execute(command.filter(Boolean));
}
