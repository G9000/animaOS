import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

export interface LinuxInstallIdentityOptions {
  packageFamily: "debian" | "rpm";
  packageVersion: string;
  executable: string;
  desktopEntry: string;
  privateKey: string;
  publicKeyHex: string;
  manifest: string;
  signature: string;
}

function sha256File(path: string): string {
  return new Bun.CryptoHasher("sha256").update(readFileSync(path)).digest("hex");
}

function run(command: string, args: string[]): Uint8Array {
  const result = Bun.spawnSync([command, ...args], { stdout: "pipe", stderr: "pipe" });
  if (result.exitCode !== 0) {
    throw new Error(`${command} failed: ${result.stderr.toString().trim()}`);
  }
  return result.stdout;
}

export function prepareLinuxInstallIdentity(options: LinuxInstallIdentityOptions): void {
  if (!/^[0-9a-f]{64}$/.test(options.publicKeyHex)) {
    throw new Error("ANIMA_INSTALL_IDENTITY_PUBLIC_KEY_HEX must be 32 lowercase-hex bytes");
  }
  const publicDer = run("openssl", ["pkey", "-in", options.privateKey, "-pubout", "-outform", "DER"]);
  const derivedPublicKey = Buffer.from(publicDer.slice(-32)).toString("hex");
  if (derivedPublicKey !== options.publicKeyHex) {
    throw new Error("Linux install-identity private key does not match the public key pinned in the binary");
  }

  const identity = {
    schemaVersion: 1,
    bundleId: "com.leoca.anima",
    packageFamily: options.packageFamily,
    packageName: "anima",
    packageVersion: options.packageVersion,
    executablePath: "/usr/bin/anima",
    executableSha256: sha256File(options.executable),
    launchTargets: [{
      path: "/usr/share/applications/ANIMA.desktop",
      sha256: sha256File(options.desktopEntry),
    }],
  };
  mkdirSync(dirname(options.manifest), { recursive: true });
  writeFileSync(options.manifest, `${JSON.stringify(identity)}\n`, { mode: 0o644 });
  const result = Bun.spawnSync([
    "openssl", "pkeyutl", "-sign", "-rawin",
    "-inkey", options.privateKey,
    "-in", options.manifest,
    "-out", options.signature,
  ], { stdout: "pipe", stderr: "pipe" });
  if (result.exitCode !== 0) {
    throw new Error(`cannot sign Linux install identity: ${result.stderr.toString().trim()}`);
  }
  if (readFileSync(options.signature).byteLength !== 64) {
    throw new Error("Ed25519 install-identity signature has the wrong length");
  }
}

function value(name: string): string {
  const index = process.argv.indexOf(`--${name}`);
  const result = index >= 0 ? process.argv[index + 1] : undefined;
  if (!result) throw new Error(`--${name} is required`);
  return resolve(result);
}

if (import.meta.main) {
  const familyIndex = process.argv.indexOf("--package-family");
  const versionIndex = process.argv.indexOf("--package-version");
  const packageFamily = process.argv[familyIndex + 1] as "debian" | "rpm" | undefined;
  const packageVersion = process.argv[versionIndex + 1];
  if (!packageFamily || !["debian", "rpm"].includes(packageFamily)) throw new Error("--package-family must be debian or rpm");
  if (!packageVersion) throw new Error("--package-version is required");
  prepareLinuxInstallIdentity({
    packageFamily,
    packageVersion,
    executable: value("executable"),
    desktopEntry: value("desktop-entry"),
    privateKey: value("private-key"),
    publicKeyHex: process.env.ANIMA_INSTALL_IDENTITY_PUBLIC_KEY_HEX ?? "",
    manifest: value("manifest"),
    signature: value("signature"),
  });
}
