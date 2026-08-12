import { existsSync, mkdirSync, readdirSync, rmSync } from "node:fs";
import { basename, join, resolve } from "node:path";

const repoRoot = resolve(import.meta.dir, "..");
const desktopRoot = join(repoRoot, "apps", "desktop");
const bundleRoot = join(repoRoot, "target", "release", "bundle");
const appRoot = join(bundleRoot, "macos");
const pkgRoot = join(bundleRoot, "pkg");
const componentPkg = join(pkgRoot, "ANIMA.component.pkg");
const finalPkg = join(pkgRoot, "ANIMA.pkg");
const tauriCli = join(desktopRoot, "node_modules", "@tauri-apps", "cli", "tauri.js");

function run(command: string, args: string[], cwd = repoRoot): void {
  const result = Bun.spawnSync([command, ...args], { cwd, stdout: "inherit", stderr: "inherit" });
  if (result.exitCode !== 0) throw new Error(`${command} failed with exit code ${result.exitCode}`);
}

if (process.platform !== "darwin") throw new Error("signed PKG packaging requires macOS");
const installerIdentity = process.env.ANIMA_MAC_INSTALLER_IDENTITY;
if (!installerIdentity) throw new Error("ANIMA_MAC_INSTALLER_IDENTITY is required");
if (!process.env.APPLE_SIGNING_IDENTITY) throw new Error("APPLE_SIGNING_IDENTITY is required");
const notaryProfile = process.env.ANIMA_MAC_NOTARY_PROFILE;
if (!notaryProfile) throw new Error("ANIMA_MAC_NOTARY_PROFILE is required");
const configIndex = process.argv.indexOf("--config");
const releaseConfig = configIndex >= 0 ? process.argv[configIndex + 1] : undefined;

run("bun", [tauriCli, "build", "--bundles", "app", ...(releaseConfig ? ["--config", releaseConfig] : [])], desktopRoot);
const apps = readdirSync(appRoot).filter((name) => name.endsWith(".app"));
if (apps.length !== 1) throw new Error(`expected one .app intermediate, found ${apps.length}`);

mkdirSync(pkgRoot, { recursive: true });
for (const path of [componentPkg, finalPkg]) if (existsSync(path)) rmSync(path);
run("pkgbuild", [
  "--component", join(appRoot, apps[0]),
  "--identifier", "com.leoca.anima",
  "--install-location", "/Applications",
  "--scripts", join(desktopRoot, "src-tauri", "install", "macos"),
  "--sign", installerIdentity,
  componentPkg,
]);
run("productbuild", ["--package", componentPkg, "--sign", installerIdentity, finalPkg]);
run("xcrun", ["notarytool", "submit", finalPkg, "--keychain-profile", notaryProfile, "--wait"]);
run("xcrun", ["stapler", "staple", finalPkg]);
run("pkgutil", ["--check-signature", finalPkg]);
run("spctl", ["--assess", "--type", "install", "--verbose=4", finalPkg]);
console.info(`built signed replacement package ${basename(finalPkg)}`);
