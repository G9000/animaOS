import { resolve, join } from "node:path";
import { pathToFileURL } from "node:url";
import { mkdirSync, rmSync, existsSync } from "node:fs";
import { readFile, writeFile } from "node:fs/promises";
import { parse, stringify } from "yaml";
import { createLogger } from "../core/logger.js";
import { clearConfigCache } from "../core/config.js";
import type { Mod } from "../core/types.js";

const logger = createLogger("installer");
const USER_MODS_DIR = "./user-mods";

export interface InstallResult {
  id: string;
  version: string;
  path: string;
}

/**
 * Parse a source string like "github:user/repo" or "github:user/repo#tag"
 */
function parseSource(source: string): { owner: string; repo: string; ref?: string } {
  const match = source.match(/^github:([^/]+)\/([^#]+)(?:#(.+))?$/);
  if (!match) throw new Error(`Invalid source format: ${source}. Expected: github:user/repo`);
  return { owner: match[1], repo: match[2], ref: match[3] };
}

/**
 * Install a mod from a GitHub repository
 */
export async function installMod(source: string): Promise<InstallResult> {
  const { owner, repo, ref } = parseSource(source);
  const targetDir = resolve(USER_MODS_DIR, repo);

  if (existsSync(targetDir)) {
    throw new Error(`Module directory already exists: ${targetDir}`);
  }

  mkdirSync(USER_MODS_DIR, { recursive: true });

  // Clone the repository
  const cloneUrl = `https://github.com/${owner}/${repo}.git`;
  const args = ["git", "clone", "--depth", "1"];
  if (ref) args.push("--branch", ref);
  args.push(cloneUrl, targetDir);

  const proc = Bun.spawn(args, { stdout: "pipe", stderr: "pipe" });
  const exitCode = await proc.exited;
  if (exitCode !== 0) {
    const stderr = await new Response(proc.stderr).text();
    rmSync(targetDir, { recursive: true, force: true });
    throw new Error(`Git clone failed: ${stderr}`);
  }

  // Validate mod contract
  const modFile = join(targetDir, "mod.ts");
  if (!existsSync(modFile)) {
    rmSync(targetDir, { recursive: true, force: true });
    throw new Error(`Invalid mod: no mod.ts found in ${repo}`);
  }

  // Dynamic import to validate
  let mod: Mod;
  try {
    const modUrl = pathToFileURL(modFile).href;
    const modModule = await import(modUrl);
    mod = modModule.default ?? modModule.mod;
    if (!mod || typeof mod.init !== "function") {
      throw new Error("Does not export a valid Mod");
    }
  } catch (err) {
    rmSync(targetDir, { recursive: true, force: true });
    throw new Error(`Invalid mod: ${err instanceof Error ? err.message : String(err)}`);
  }

  // Add to anima-mod.config.yaml
  await addToConfig(mod.id, `./user-mods/${repo}`);

  logger.info(`Installed mod '${mod.id}' from ${source}`, { version: mod.version });

  return { id: mod.id, version: mod.version, path: targetDir };
}

/**
 * Uninstall a mod by ID
 */
export async function uninstallMod(modId: string, modPath: string): Promise<void> {
  const resolvedPath = resolve(modPath);

  // Only allow uninstalling from user-mods/
  if (!resolvedPath.includes("user-mods")) {
    throw new Error("Cannot uninstall built-in mods");
  }

  // Remove directory
  if (existsSync(resolvedPath)) {
    rmSync(resolvedPath, { recursive: true, force: true });
  }

  // Remove from config
  await removeFromConfig(modId);

  logger.info(`Uninstalled mod '${modId}'`);
}

async function addToConfig(modId: string, modPath: string): Promise<void> {
  const configPath = "./anima-mod.config.yaml";
  const content = await readFile(configPath, "utf-8");
  const config = parse(content);

  if (!config.modules) config.modules = [];

  // Check for duplicate
  if (config.modules.some((m: any) => m.id === modId)) {
    throw new Error(`Module '${modId}' already exists in config`);
  }

  config.modules.push({ id: modId, path: modPath, config: {} });
  await writeFile(configPath, stringify(config));
  clearConfigCache();
}

async function removeFromConfig(modId: string): Promise<void> {
  const configPath = "./anima-mod.config.yaml";
  const content = await readFile(configPath, "utf-8");
  const config = parse(content);

  if (config.modules) {
    config.modules = config.modules.filter((m: any) => m.id !== modId);
    await writeFile(configPath, stringify(config));
    clearConfigCache();
  }
}
